"""
Игровая логика Fiesta — чистый Python, без зависимости от Telegram.
"""

from __future__ import annotations

import logging
import random
import time
from typing import Optional

from models import (
    Association, Card, CardSource, GameState, Player, Room, RoomSettings,
)
from cards import get_characters

logger = logging.getLogger(__name__)


class GameError(Exception):
    """Ошибка игровой логики."""
    pass


class GameEngine:
    """Управляет всеми комнатами и игровой логикой."""

    def __init__(self):
        self.rooms: dict[str, Room] = {}

    def _generate_room_id(self) -> str:
        """Генерирует уникальный 4-символьный код комнаты."""
        import string
        chars = string.ascii_uppercase + string.digits
        for _ in range(100):
            code = ''.join(random.choices(chars, k=4))
            if code not in self.rooms:
                return code
        raise GameError("Не удалось создать код комнаты")

    # ─── Управление комнатами ───

    def create_room(self, host: Player, group_chat_id: int | None = None,
                    settings: RoomSettings | None = None) -> Room:
        """Создать новую комнату."""
        room_id = self._generate_room_id()
        host.is_host = True

        room = Room(
            room_id=room_id,
            host_id=host.user_id,
            group_chat_id=group_chat_id,
            settings=settings or RoomSettings(),
        )
        room.players[host.user_id] = host
        self.rooms[room_id] = room

        logger.info(f"Комната {room_id} создана хостом {host.first_name} ({host.user_id})")
        return room

    def join_room(self, room_id: str, player: Player) -> Room:
        """Присоединиться к комнате."""
        room = self.rooms.get(room_id.upper())
        if not room:
            raise GameError("Комната не найдена")
        if room.state != GameState.LOBBY and room.state != GameState.COLLECTING_CARDS:
            raise GameError("Игра уже идёт, присоединиться нельзя")
        if room.num_players >= room.settings.max_players:
            raise GameError(f"Комната заполнена (максимум {room.settings.max_players})")
        if player.user_id in room.players:
            raise GameError("Ты уже в комнате")

        room.players[player.user_id] = player
        room.last_activity = time.time()
        logger.info(f"Игрок {player.first_name} ({player.user_id}) вошёл в комнату {room_id}")
        return room

    def leave_room(self, room_id: str, user_id: int) -> tuple[Room, bool]:
        """
        Покинуть комнату.
        Возвращает (room, room_destroyed).
        """
        room = self.rooms.get(room_id)
        if not room or user_id not in room.players:
            raise GameError("Ты не в этой комнате")

        del room.players[user_id]
        logger.info(f"Игрок {user_id} покинул комнату {room_id}")

        if not room.players:
            del self.rooms[room_id]
            logger.info(f"Комната {room_id} удалена (все вышли)")
            return room, True

        if room.host_id == user_id:
            room.transfer_host()
            logger.info(f"Хост комнаты {room_id} передан {room.host_id}")

        return room, False

    def find_room_by_player(self, user_id: int) -> Optional[Room]:
        """Найти комнату, в которой состоит игрок."""
        for room in self.rooms.values():
            if user_id in room.players:
                return room
        return None

    # ─── Сбор пользовательских персонажей ───

    def start_collecting(self, room_id: str, user_id: int) -> Room:
        """Начать сбор пользовательских персонажей."""
        room = self.rooms.get(room_id)
        if not room:
            raise GameError("Комната не найдена")
        if user_id != room.host_id:
            raise GameError("Только хост может управлять игрой")
        if room.state != GameState.LOBBY:
            raise GameError("Сбор можно начать только из лобби")

        room.state = GameState.COLLECTING_CARDS
        room.last_activity = time.time()
        logger.info(f"Комната {room_id}: сбор персонажей начат")
        return room

    def add_custom_character(self, room_id: str, user_id: int, character: str) -> Room:
        """Добавить пользовательского персонажа."""
        room = self.rooms.get(room_id)
        if not room:
            raise GameError("Комната не найдена")
        if user_id not in room.players:
            raise GameError("Ты не в этой комнате")

        character = character.strip()
        if len(character) < 2:
            raise GameError("Слишком короткое имя")
        if len(character) > 100:
            raise GameError("Слишком длинное имя (макс 100 символов)")

        # Проверка дублей
        existing = [c.lower() for c in room.custom_characters]
        if character.lower() in existing:
            raise GameError("Такой персонаж уже добавлен")

        room.custom_characters.append(character)
        room.last_activity = time.time()
        logger.info(f"Комната {room_id}: добавлен персонаж '{character}' от {user_id}")
        return room

    # ─── Запуск игры ───

    def start_game(self, room_id: str, user_id: int) -> Room:
        """Запустить игру: раздать карточки, определить порядок."""
        room = self.rooms.get(room_id)
        if not room:
            raise GameError("Комната не найдена")
        if user_id != room.host_id:
            raise GameError("Только хост может запустить игру")
        if room.state not in (GameState.LOBBY, GameState.COLLECTING_CARDS):
            raise GameError("Игра уже идёт")
        if room.num_players < room.settings.min_players:
            raise GameError(f"Нужно минимум {room.settings.min_players} игрока")

        # Проверяем что всем можно писать в ЛС
        no_dm = [p for p in room.players.values() if not p.dm_available]
        if no_dm:
            names = ", ".join(p.first_name for p in no_dm)
            raise GameError(f"Не могу писать в ЛС: {names}. Пусть напишут мне /start в личку.")

        # Определяем порядок игроков (случайный)
        room.player_order = list(room.players.keys())
        random.shuffle(room.player_order)

        # Выбираем источник персонажей
        source = room.settings.card_source.value
        custom = room.custom_characters if room.custom_characters else None

        # Если есть кастомные и нет явного выбора — mixed
        if custom and source == "default":
            source = "mixed"

        characters = get_characters(
            category=room.settings.category,
            count=room.num_players,
            custom=custom,
            source=source,
        )

        if len(characters) < room.num_players:
            raise GameError(f"Недостаточно персонажей: есть {len(characters)}, нужно {room.num_players}")

        # Раздаём карточки
        room.cards.clear()
        for i, uid in enumerate(room.player_order):
            card = Card(
                character=characters[i],
                owner_id=uid,
            )
            room.cards[card.card_id] = card

        room.current_step = 0
        room.step_submitted.clear()
        room.state = GameState.WRITING
        room.last_activity = time.time()

        logger.info(
            f"Комната {room_id}: игра началась, {room.num_players} игроков, "
            f"порядок: {room.player_order}"
        )
        return room

    # ─── Этап ассоциаций ───

    def get_current_task(self, room: Room, user_id: int) -> dict | None:
        """
        Что должен сделать игрок на текущем шаге.
        Возвращает {"card_id": ..., "visible_text": ..., "step": ..., "is_character": bool}
        или None если уже ответил.
        """
        if room.state != GameState.WRITING:
            return None
        if user_id in room.step_submitted:
            return None

        card = room.get_card_for_writer(user_id, room.current_step)
        if not card:
            return None

        is_character = room.current_step == 0
        return {
            "card_id": card.card_id,
            "visible_text": card.visible_text,
            "step": room.current_step,
            "is_character": is_character,
        }

    def submit_association(self, room_id: str, user_id: int, text: str) -> dict:
        """
        Записать ассоциацию.
        Возвращает {"step_complete": bool, "game_phase_changed": bool}.
        """
        room = self.rooms.get(room_id)
        if not room:
            raise GameError("Комната не найдена")
        if room.state != GameState.WRITING:
            raise GameError("Сейчас не этап ассоциаций")
        if user_id not in room.players:
            raise GameError("Ты не в этой комнате")
        if user_id in room.step_submitted:
            raise GameError("Ты уже отправил ассоциацию на этом шаге")

        text = text.strip()
        if not text or len(text) < 1:
            raise GameError("Ассоциация не может быть пустой")
        if len(text) > 200:
            raise GameError("Слишком длинная ассоциация (макс 200 символов)")

        card = room.get_card_for_writer(user_id, room.current_step)
        if not card:
            raise GameError("Не найдена карточка для тебя на этом шаге")

        assoc = Association(
            author_id=user_id,
            text=text,
            step=room.current_step,
        )
        card.associations.append(assoc)
        room.step_submitted.add(user_id)
        room.last_activity = time.time()

        logger.info(
            f"Комната {room_id}: {user_id} написал ассоциацию на шаге {room.current_step} "
            f"для карточки {card.card_id}: '{text[:50]}...'"
        )

        step_complete = len(room.step_submitted) >= room.num_players
        game_phase_changed = False

        if step_complete:
            room.current_step += 1
            room.step_submitted.clear()
            logger.info(f"Комната {room_id}: шаг {room.current_step - 1} завершён")

            if room.current_step >= room.total_steps:
                room.state = GameState.GUESSING
                room.guesses.clear()
                room.guessing_done.clear()
                room.guessing_progress.clear()
                game_phase_changed = True
                logger.info(f"Комната {room_id}: переход к угадыванию")

        return {
            "step_complete": step_complete,
            "game_phase_changed": game_phase_changed,
        }

    def skip_player(self, room_id: str, user_id: int):
        """Пропустить игрока (по таймауту)."""
        room = self.rooms.get(room_id)
        if not room or room.state != GameState.WRITING:
            return

        if user_id in room.step_submitted:
            return

        card = room.get_card_for_writer(user_id, room.current_step)
        if card:
            assoc = Association(
                author_id=user_id,
                text="(пропущено)",
                step=room.current_step,
            )
            card.associations.append(assoc)

        room.step_submitted.add(user_id)
        logger.warning(f"Комната {room_id}: игрок {user_id} пропущен по таймауту")

    # ─── Этап угадывания ───

    def get_guessing_data(self, room: Room) -> dict:
        """
        Данные для этапа угадывания.
        Возвращает:
        {
            "associations": [{"card_id": ..., "text": ...}, ...],
            "characters": [{"card_id": ..., "name": ...}, ...]
        }
        Списки перемешаны по-разному.
        """
        associations = []
        characters = []

        for card in room.cards.values():
            associations.append({
                "card_id": card.card_id,
                "text": card.last_association or "(нет ассоциаций)",
            })
            characters.append({
                "card_id": card.card_id,
                "name": card.character,
            })

        random.shuffle(associations)
        random.shuffle(characters)

        return {"associations": associations, "characters": characters}

    def submit_guess(self, room_id: str, user_id: int,
                     card_id: str, character_card_id: str) -> dict:
        """
        Записать одну пару сопоставления.
        Возвращает {"guess_count": int, "total": int, "all_done": bool}.
        """
        room = self.rooms.get(room_id)
        if not room:
            raise GameError("Комната не найдена")
        if room.state != GameState.GUESSING:
            raise GameError("Сейчас не этап угадывания")
        if user_id not in room.players:
            raise GameError("Ты не в этой комнате")
        if user_id in room.guessing_done:
            raise GameError("Ты уже завершил угадывание")

        # Инициализация
        if user_id not in room.guesses:
            room.guesses[user_id] = {}

        # Получаем имя персонажа по card_id
        character_card = room.cards.get(character_card_id)
        if not character_card:
            raise GameError("Персонаж не найден")

        room.guesses[user_id][card_id] = character_card.character
        room.last_activity = time.time()

        guess_count = len(room.guesses[user_id])
        total = room.num_players

        # Если все карточки сопоставлены
        if guess_count >= total:
            room.guessing_done.add(user_id)

        all_done = len(room.guessing_done) >= room.num_players

        if all_done:
            room.state = GameState.REVEAL
            logger.info(f"Комната {room_id}: все угадали, переход к результатам")

        return {
            "guess_count": guess_count,
            "total": total,
            "all_done": all_done,
        }

    def force_finish_guessing(self, room_id: str):
        """Принудительно завершить угадывание (по таймауту)."""
        room = self.rooms.get(room_id)
        if not room or room.state != GameState.GUESSING:
            return
        room.state = GameState.REVEAL
        logger.info(f"Комната {room_id}: угадывание завершено принудительно")

    # ─── Результаты ───

    def calculate_results(self, room_id: str) -> dict:
        """
        Подсчитать результаты.
        Возвращает:
        {
            "scores": {user_id: points},
            "correct_answers": {card_id: character},
            "chains": {card_id: [{"author": name, "text": text}, ...]},
            "player_details": {user_id: [{"card_id": ..., "guessed": ..., "correct": ..., "is_correct": bool}]}
        }
        """
        room = self.rooms.get(room_id)
        if not room:
            raise GameError("Комната не найдена")

        scores: dict[int, int] = {uid: 0 for uid in room.players}
        correct_answers: dict[str, str] = {}
        chains: dict[str, list] = {}
        player_details: dict[int, list] = {uid: [] for uid in room.players}

        for card in room.cards.values():
            correct_answers[card.card_id] = card.character
            chain = []
            for assoc in card.associations:
                author = room.players.get(assoc.author_id)
                chain.append({
                    "author": author.first_name if author else "???",
                    "text": assoc.text,
                })
            chains[card.card_id] = chain

        for uid in room.players:
            guesses = room.guesses.get(uid, {})
            for card_id, guessed_char in guesses.items():
                card = room.cards.get(card_id)
                if not card:
                    continue
                is_correct = guessed_char == card.character
                if is_correct:
                    scores[uid] = scores.get(uid, 0) + 1
                player_details[uid].append({
                    "card_id": card_id,
                    "guessed": guessed_char,
                    "correct": card.character,
                    "is_correct": is_correct,
                })

        room.state = GameState.FINISHED
        logger.info(f"Комната {room_id}: результаты подсчитаны, очки: {scores}")

        return {
            "scores": scores,
            "correct_answers": correct_answers,
            "chains": chains,
            "player_details": player_details,
        }

    # ─── Очистка ───

    def cleanup_stale_rooms(self, max_age: float = 3600):
        """Удалить неактивные комнаты."""
        now = time.time()
        to_delete = [
            rid for rid, room in self.rooms.items()
            if now - room.last_activity > max_age
            and room.state in (GameState.LOBBY, GameState.FINISHED)
        ]
        for rid in to_delete:
            del self.rooms[rid]
            logger.info(f"Комната {rid} удалена по таймауту")
