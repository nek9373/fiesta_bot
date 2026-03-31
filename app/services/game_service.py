import asyncio
import logging
import random
from sqlalchemy.ext.asyncio import AsyncSession
from aiogram import Bot

from app.models.tables import Arena, ArenaPlayer, Card, Association
from app.repositories.arena import ArenaRepository
from app.repositories.player import PlayerRepository
from app.repositories.card import CardRepository
from app.repositories.character_bank import CharacterBankRepository
from app.config import settings
from app.texts import TEXTS
from app.keyboards.inline import build_guess_keyboard
from llm import calavera_llm

logger = logging.getLogger(__name__)

# Global dict of active timeout tasks: (arena_id, step) -> asyncio.Task
_timeout_tasks: dict[tuple[int, int], asyncio.Task] = {}


class GameService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.arena_repo = ArenaRepository(session)
        self.player_repo = PlayerRepository(session)
        self.card_repo = CardRepository(session)
        self.char_repo = CharacterBankRepository(session)

    async def start_game(self, arena_id: int, user_id: int, bot: Bot) -> None:
        arena = await self.arena_repo.get_by_id(arena_id)
        if arena is None:
            raise ValueError("Комната не найдена.")
        if arena.host_user_id != user_id:
            raise ValueError("Только хост может запустить игру.")
        if arena.state != "lobby":
            raise ValueError("Игра уже запущена или завершена.")

        players = await self.player_repo.get_active_players(arena_id)
        n = len(players)
        if n < settings.MIN_PLAYERS:
            raise ValueError(f"Нужно минимум {settings.MIN_PLAYERS} игрока. Сейчас: {n}.")

        # Load characters
        await self.char_repo.load_defaults()
        characters = await self.char_repo.get_characters_for_game(arena_id)
        if len(characters) < n:
            raise ValueError(f"Недостаточно персонажей ({len(characters)}) для {n} игроков.")

        selected = random.sample(characters, n)

        # Deal cards
        await self.arena_repo.update_state(arena_id, "dealing")
        total_steps = n - 1
        await self.arena_repo.set_total_steps(arena_id, total_steps)

        player_order = [p.user_id for p in players]
        random.shuffle(player_order)

        cards = []
        for i, (player_uid, character) in enumerate(zip(player_order, selected)):
            # Step 0: first holder is next player
            first_holder = player_order[(i + 1) % n]
            card = await self.card_repo.create(arena_id, character, player_uid, first_holder)
            cards.append(card)

        await self.arena_repo.update_state(arena_id, "writing")
        await self.arena_repo.update_step(arena_id, 0)
        await self.session.commit()

        # Notify group
        if arena.group_chat_id:
            await bot.send_message(arena.group_chat_id, TEXTS["game_started"])

        # Send cards to first holders via DM
        await self._send_cards_to_holders(arena_id, 0, player_order, bot)

        # Start timeout
        self._start_timeout(arena_id, 0, arena.association_timeout, bot)

    async def _send_cards_to_holders(
        self, arena_id: int, step: int, player_order: list[int], bot: Bot
    ) -> None:
        cards = await self.card_repo.get_cards_for_arena(arena_id)

        # For step 0: pre-generate Calavera intros for all characters in parallel
        character_intros: dict[str, str] = {}
        if step == 0:
            intro_tasks = {}
            for card in cards:
                intro_tasks[card.character] = calavera_llm(
                    "character_intro",
                    context=f"Персонаж: {card.character}",
                    fallback_phrases=[
                        f"О, {card.character}... Я помню это имя. Давно, ещё при жизни...",
                        f"Ах, {card.character}! Каждый мертвец на карнавале знает эту историю.",
                        f"{card.character}, значит... Интересный выбор, amigo.",
                    ],
                )
            for character, task in intro_tasks.items():
                try:
                    character_intros[character] = await task
                    logger.debug("Generated intro for '%s': %s", character, character_intros[character][:80])
                except Exception as e:
                    logger.warning("Failed to generate intro for '%s': %s", character, e)

        for card in cards:
            holder_uid = card.current_holder_user_id
            if step == 0:
                # First step: show character name + Calavera's intro
                text = TEXTS["your_turn_first"].format(character=card.character)
                intro = character_intros.get(card.character)
                if intro:
                    text += f"\n\n💀 Калавера: «{intro}»"
            else:
                # Show only previous association
                prev_assoc = await self.card_repo.get_association(card.id, step - 1)
                if prev_assoc:
                    text = TEXTS["your_turn_next"].format(association=prev_assoc.text)
                else:
                    text = TEXTS["your_turn_next"].format(association="(пропущено)")

            text += f"\n\n[card:{card.id}]"
            try:
                await bot.send_message(holder_uid, text)
                logger.debug("Sent card %d to holder %d at step %d", card.id, holder_uid, step)
            except Exception as e:
                logger.warning("Cannot DM user %d: %s", holder_uid, e)

    async def submit_association(
        self, card_id: int, user_id: int, text: str, bot: Bot
    ) -> bool:
        """Submit association. Returns True if all players submitted for this step."""
        card = await self.card_repo.get_by_id(card_id)
        if card is None:
            raise ValueError("Карточка не найдена.")
        if card.current_holder_user_id != user_id:
            raise ValueError("Это не твоя карточка сейчас.")

        arena = await self.arena_repo.get_by_id(card.arena_id)
        if arena.state != "writing":
            raise ValueError("Сейчас не фаза ассоциаций.")

        # Check duplicate
        existing = await self.card_repo.get_association(card_id, arena.current_step)
        if existing:
            raise ValueError("Ты уже отправил ассоциацию для этой карточки.")

        await self.card_repo.add_association(card_id, user_id, text, arena.current_step)
        await self.session.commit()

        # Check if all cards have associations for this step
        return await self._check_step_complete(arena, bot)

    async def _check_step_complete(self, arena: Arena, bot: Bot) -> bool:
        cards = await self.card_repo.get_cards_for_arena(arena.id)
        step = arena.current_step

        all_done = True
        for card in cards:
            assoc = await self.card_repo.get_association(card.id, step)
            if assoc is None:
                all_done = False
                break

        if all_done:
            # Cancel timeout
            self._cancel_timeout(arena.id, step)
            await self._advance_step(arena, bot)
            return True
        return False

    async def _advance_step(self, arena: Arena, bot: Bot) -> None:
        players = await self.player_repo.get_active_players(arena.id)
        player_order = [p.user_id for p in players]
        n = len(player_order)
        next_step = arena.current_step + 1

        if next_step >= arena.total_steps:
            # Writing phase done -> guessing
            await self.arena_repo.update_state(arena.id, "guessing")
            await self.session.commit()
            logger.info("Arena %d -> guessing phase", arena.id)

            if arena.group_chat_id:
                await bot.send_message(arena.group_chat_id, TEXTS["guessing_phase_started"])

            await self._send_guessing_interface(arena, bot)
            self._start_guessing_timeout(arena.id, arena.guessing_timeout, bot)
            return

        # Move cards to next holders
        cards = await self.card_repo.get_cards_for_arena(arena.id)

        # We need to figure out the card->owner mapping and player order
        # Card Ci owned by player at index i -> step k holder is player_order[(i+k+1) % n]
        # We need to find each card's owner index
        owner_to_idx = {}
        for idx, uid in enumerate(player_order):
            owner_to_idx[uid] = idx

        for card in cards:
            owner_idx = owner_to_idx.get(card.owner_user_id)
            if owner_idx is None:
                continue
            new_holder = player_order[(owner_idx + next_step + 1) % n]
            await self.card_repo.update_holder(card.id, new_holder, next_step)

        await self.arena_repo.update_step(arena.id, next_step)
        await self.session.commit()

        await self._send_cards_to_holders(arena.id, next_step, player_order, bot)
        self._start_timeout(arena.id, next_step, arena.association_timeout, bot)

    async def _send_guessing_interface(self, arena: Arena, bot: Bot) -> None:
        cards = await self.card_repo.get_cards_for_arena(arena.id)
        players = await self.player_repo.get_active_players(arena.id)
        characters = [c.character for c in cards]

        # Build association summary for each card
        card_summaries = []
        for i, card in enumerate(cards):
            last_assoc = await self.card_repo.get_last_association(card.id)
            assoc_text = last_assoc.text if last_assoc else "(пропущено)"
            card_summaries.append((card.id, assoc_text))

        # Send to each player
        for player in players:
            lines = ["Фаза угадывания! Соотнеси ассоциации с персонажами.\n"]
            lines.append("Ассоциации:")
            for i, (cid, assoc) in enumerate(card_summaries):
                lines.append(f"  {i+1}. {assoc}")
            lines.append("")
            lines.append("Выбери персонажа для ассоциации #1:")
            text = "\n".join(lines)

            kb = build_guess_keyboard(arena.id, card_summaries[0][0], characters, [])
            try:
                await bot.send_message(player.user_id, text, reply_markup=kb)
            except Exception as e:
                logger.warning("Cannot DM user %d for guessing: %s", player.user_id, e)

    def _start_timeout(self, arena_id: int, step: int, timeout: int, bot: Bot) -> None:
        key = (arena_id, step)
        task = asyncio.create_task(self._timeout_handler(arena_id, step, timeout, bot))
        _timeout_tasks[key] = task
        logger.debug("Timeout task started: arena=%d step=%d timeout=%ds", arena_id, step, timeout)

    def _cancel_timeout(self, arena_id: int, step: int) -> None:
        key = (arena_id, step)
        task = _timeout_tasks.pop(key, None)
        if task and not task.done():
            task.cancel()
            logger.debug("Timeout cancelled: arena=%d step=%d", arena_id, step)

    async def _timeout_handler(self, arena_id: int, step: int, timeout: int, bot: Bot) -> None:
        await asyncio.sleep(timeout)
        logger.info("Timeout fired: arena=%d step=%d", arena_id, step)

        from app.db import async_session
        async with async_session() as session:
            svc = GameService(session)
            arena = await svc.arena_repo.get_by_id(arena_id)
            if arena is None or arena.state != "writing" or arena.current_step != step:
                return

            cards = await svc.card_repo.get_cards_for_arena(arena_id)
            for card in cards:
                existing = await svc.card_repo.get_association(card.id, step)
                if existing is None:
                    await svc.card_repo.add_association(
                        card.id, card.current_holder_user_id, "(пропущено)", step
                    )
                    logger.info("Auto-skip: card=%d step=%d holder=%d",
                                card.id, step, card.current_holder_user_id)
                    try:
                        await bot.send_message(
                            card.current_holder_user_id,
                            "Время вышло! Ассоциация пропущена."
                        )
                    except Exception:
                        pass
            await session.commit()

            # Notify group
            if arena.group_chat_id:
                await bot.send_message(arena.group_chat_id, "Время на ассоциации вышло, переходим дальше...")

            await svc._advance_step(arena, bot)

    def _start_guessing_timeout(self, arena_id: int, timeout: int, bot: Bot) -> None:
        task = asyncio.create_task(self._guessing_timeout_handler(arena_id, timeout, bot))
        _timeout_tasks[("guess", arena_id)] = task

    async def _guessing_timeout_handler(self, arena_id: int, timeout: int, bot: Bot) -> None:
        await asyncio.sleep(timeout)
        logger.info("Guessing timeout fired: arena=%d", arena_id)

        from app.db import async_session
        from app.services.scoring import ScoringService
        async with async_session() as session:
            arena_repo = ArenaRepository(session)
            arena = await arena_repo.get_by_id(arena_id)
            if arena is None or arena.state != "guessing":
                return
            await arena_repo.update_state(arena_id, "reveal")
            await session.commit()

            scoring = ScoringService(session)
            await scoring.reveal_results(arena_id, bot)

    async def process_guess(
        self, arena_id: int, user_id: int, card_id: int,
        character: str, bot: Bot
    ) -> dict | None:
        """Process a single guess. Returns remaining info or None if all done."""
        from app.models.tables import Guess

        arena = await self.arena_repo.get_by_id(arena_id)
        if arena is None or arena.state != "guessing":
            raise ValueError("Сейчас не фаза угадывания.")

        card = await self.card_repo.get_by_id(card_id)
        if card is None:
            raise ValueError("Карточка не найдена.")

        is_correct = (character == card.character)
        guess = Guess(
            arena_id=arena_id,
            user_id=user_id,
            card_id=card_id,
            guessed_character=character,
            is_correct=is_correct,
        )
        self.session.add(guess)
        await self.session.commit()
        logger.info("Guess: user=%d card=%d char='%s' correct=%s",
                     user_id, card_id, character, is_correct)

        # Check if this player has guessed all cards
        cards = await self.card_repo.get_cards_for_arena(arena_id)
        from sqlalchemy import select
        result = await self.session.execute(
            select(Guess).where(
                Guess.arena_id == arena_id,
                Guess.user_id == user_id,
            )
        )
        user_guesses = list(result.scalars().all())
        guessed_card_ids = {g.card_id for g in user_guesses}
        guessed_characters = {g.guessed_character for g in user_guesses}

        remaining_cards = [c for c in cards if c.id not in guessed_card_ids]
        remaining_chars = [c.character for c in cards if c.character not in guessed_characters]

        if not remaining_cards:
            # All guessed by this player - check if ALL players done
            players = await self.player_repo.get_active_players(arena_id)
            all_done = True
            for p in players:
                res = await self.session.execute(
                    select(Guess).where(
                        Guess.arena_id == arena_id,
                        Guess.user_id == p.user_id,
                    )
                )
                p_guesses = list(res.scalars().all())
                if len(p_guesses) < len(cards):
                    all_done = False
                    break

            if all_done:
                # Cancel guessing timeout
                task = _timeout_tasks.pop(("guess", arena_id), None)
                if task and not task.done():
                    task.cancel()

                await self.arena_repo.update_state(arena_id, "reveal")
                await self.session.commit()

                from app.services.scoring import ScoringService
                scoring = ScoringService(self.session)
                await scoring.reveal_results(arena_id, bot)

            return None

        return {
            "remaining_cards": remaining_cards,
            "remaining_chars": remaining_chars,
        }
