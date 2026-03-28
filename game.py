"""
Игровая логика Fiesta: Карнавал мёртвых — кооперативная версия.
"""

from __future__ import annotations

import logging
import random
import re
import time
from typing import Optional

from models import (
    TOTAL_CHARACTERS, MAX_TEETH,
    AssociationStep, CardSource, ConstraintType, GameState,
    Player, Room, RoomSettings, Skull,
)
from cards import get_characters

logger = logging.getLogger(__name__)


class GameError(Exception):
    pass


# ─── Валидация слов ───

def validate_word(word: str, previous_word: str | None = None,
                  character: str | None = None,
                  constraint: ConstraintType | None = None) -> str | None:
    """
    Проверить слово по правилам. Возвращает ошибку или None.
    """
    word = word.strip()

    # Одно слово (сложные через дефис допускаются)
    parts = word.split()
    if len(parts) > 1:
        return "Только одно слово! Сложные слова (через дефис) допускаются."

    if len(word) < 2:
        return "Слишком короткое слово."

    if len(word) > 50:
        return "Слишком длинное слово."

    # Нельзя писать имена персонажей
    if character and word.lower() == character.lower():
        return "Нельзя писать имя персонажа!"

    # Нельзя однокоренные с полученным словом
    if previous_word:
        prev_stem = previous_word.lower()[:4] if len(previous_word) >= 4 else previous_word.lower()
        word_stem = word.lower()[:4] if len(word) >= 4 else word.lower()
        if len(prev_stem) >= 4 and prev_stem == word_stem:
            return f"Нельзя писать однокоренные слова с '{previous_word}'!"

    # Ограничения уровней сложности
    if constraint:
        err = _check_constraint(word, constraint)
        if err:
            return err

    return None


def _check_constraint(word: str, constraint: ConstraintType) -> str | None:
    w = word.lower()
    if constraint == ConstraintType.THEME_OBJECT:
        pass  # Тематику автоматически проверить сложно, доверяем игроку
    elif constraint == ConstraintType.THEME_PLACE:
        pass
    elif constraint == ConstraintType.THEME_NATURE:
        pass
    elif constraint == ConstraintType.MAX_6_LETTERS:
        # Считаем буквы (без дефиса)
        letters = re.sub(r'[^а-яёa-z]', '', w)
        if len(letters) > 6:
            return f"Не более 6 букв! (сейчас {len(letters)})"
    elif constraint == ConstraintType.NO_LETTER_E:
        if 'е' in w or 'ё' in w:
            return "Слово не должно содержать букву Е!"
    elif constraint == ConstraintType.ENDS_WITH_A:
        if not w.endswith('а'):
            return "Слово должно заканчиваться на -А!"
    elif constraint == ConstraintType.STARTS_WITH_M:
        if not w.startswith('м'):
            return "Слово должно начинаться на М!"
    elif constraint == ConstraintType.STARTS_WITH_P:
        if not w.startswith('п'):
            return "Слово должно начинаться на П!"
    elif constraint == ConstraintType.STARTS_WITH_T:
        if not w.startswith('т'):
            return "Слово должно начинаться на Т!"
    elif constraint == ConstraintType.STARTS_WITH_R:
        if not w.startswith('р'):
            return "Слово должно начинаться на Р!"
    elif constraint == ConstraintType.STARTS_WITH_S:
        if not w.startswith('с'):
            return "Слово должно начинаться на С!"
    elif constraint == ConstraintType.STARTS_WITH_D:
        if not w.startswith('д'):
            return "Слово должно начинаться на Д!"
    return None


class GameEngine:

    def __init__(self):
        self.rooms: dict[str, Room] = {}

    def _gen_room_id(self) -> str:
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
        room_id = self._gen_room_id()
        host.is_host = True
        room = Room(
            room_id=room_id,
            host_id=host.user_id,
            group_chat_id=group_chat_id,
            settings=settings or RoomSettings(),
        )
        room.players[host.user_id] = host
        self.rooms[room_id] = room
        logger.info(f"Комната {room_id} создана хостом {host.first_name}")
        return room

    def join_room(self, room_id: str, player: Player) -> Room:
        room = self.rooms.get(room_id.upper())
        if not room:
            raise GameError("Комната не найдена")
        if room.state not in (GameState.LOBBY, GameState.COLLECTING_CARDS):
            raise GameError("Игра уже идёт")
        if room.num_players >= room.settings.max_players:
            raise GameError("Комната заполнена (макс 8)")
        if player.user_id in room.players:
            raise GameError("Ты уже в комнате")
        room.players[player.user_id] = player
        room.last_activity = time.time()
        logger.info(f"{player.first_name} ({player.user_id}) вошёл в комнату {room_id}")
        return room

    def leave_room(self, room_id: str, user_id: int) -> tuple[Room, bool]:
        room = self.rooms.get(room_id)
        if not room or user_id not in room.players:
            raise GameError("Ты не в этой комнате")
        del room.players[user_id]
        if not room.players:
            del self.rooms[room_id]
            return room, True
        if room.host_id == user_id:
            room.transfer_host()
        return room, False

    def find_room_by_player(self, user_id: int) -> Optional[Room]:
        for room in self.rooms.values():
            if user_id in room.players:
                return room
        return None

    # ─── Сбор персонажей ───

    def start_collecting(self, room_id: str, user_id: int) -> Room:
        room = self.rooms.get(room_id)
        if not room:
            raise GameError("Комната не найдена")
        if user_id != room.host_id:
            raise GameError("Только хост")
        if room.state != GameState.LOBBY:
            raise GameError("Сбор можно начать только из лобби")
        room.state = GameState.COLLECTING_CARDS
        return room

    def add_custom_character(self, room_id: str, user_id: int, character: str) -> Room:
        room = self.rooms.get(room_id)
        if not room or user_id not in room.players:
            raise GameError("Ты не в комнате")
        character = character.strip()
        if len(character) < 2 or len(character) > 100:
            raise GameError("Имя от 2 до 100 символов")
        existing = [c.lower() for c in room.custom_characters]
        if character.lower() in existing:
            raise GameError("Уже добавлен")
        room.custom_characters.append(character)
        return room

    # ─── Запуск игры ───

    def start_game(self, room_id: str, user_id: int) -> Room:
        room = self.rooms.get(room_id)
        if not room:
            raise GameError("Комната не найдена")
        if user_id != room.host_id:
            raise GameError("Только хост может запустить")
        if room.state not in (GameState.LOBBY, GameState.COLLECTING_CARDS):
            raise GameError("Игра уже идёт")
        if room.num_players < room.settings.min_players:
            raise GameError(f"Нужно минимум {room.settings.min_players} игрока")

        no_dm = [p for p in room.players.values() if not p.dm_available]
        if no_dm:
            names = ", ".join(p.first_name for p in no_dm)
            raise GameError(f"Не могу писать в ЛС: {names}. Пусть напишут /start в личку.")

        # Порядок игроков
        room.player_order = list(room.players.keys())
        random.shuffle(room.player_order)

        # Число зубов = число игроков (каждый подержит каждый череп), но не более MAX_TEETH
        room.total_teeth = min(room.num_players, MAX_TEETH)
        logger.info(f"Комната {room_id}: total_teeth={room.total_teeth} (игроков={room.num_players}, макс={MAX_TEETH})")

        # Получаем персонажей: N для игроков + (8-N) обманок
        source = room.settings.card_source.value
        custom = room.custom_characters or None
        if custom and source == "default":
            source = "mixed"

        all_chars = get_characters(
            category=room.settings.category,
            count=TOTAL_CHARACTERS,
            custom=custom,
            source=source,
        )
        if len(all_chars) < TOTAL_CHARACTERS:
            raise GameError(f"Недостаточно персонажей: {len(all_chars)}, нужно {TOTAL_CHARACTERS}")

        # Первые N — для игроков, остальные — обманки
        player_chars = all_chars[:room.num_players]
        room.decoy_characters = all_chars[room.num_players:]

        # Создаём черепа
        room.skulls.clear()
        for i, uid in enumerate(room.player_order):
            skull = Skull(character=player_chars[i], owner_id=uid)
            room.skulls[skull.skull_id] = skull

        # Все 8 персонажей для угадывания (перемешаны)
        room.all_characters = all_chars[:]
        random.shuffle(room.all_characters)

        # Стартовые жетоны кости
        bone_table = {4: 0, 5: 1, 6: 2, 7: 3, 8: 4}
        room.initial_bone_tokens = bone_table.get(room.num_players, 0)
        room.bone_tokens = room.initial_bone_tokens

        # Ограничения (уровни сложности)
        room.active_constraints.clear()
        if room.settings.difficulty_level > 0:
            all_constraints = list(ConstraintType)
            random.shuffle(all_constraints)
            room.active_constraints = all_constraints[:room.settings.difficulty_level]

        room.current_tooth = 0
        room.tooth_submitted.clear()
        room.state = GameState.WRITING
        room.last_activity = time.time()

        logger.info(
            f"Комната {room_id}: игра, {room.num_players} игроков, "
            f"персонажи: {player_chars}, обманки: {room.decoy_characters}"
        )
        return room

    # ─── Этап ассоциаций ───

    def _get_player_constraint(self, room: Room, user_id: int) -> ConstraintType | None:
        """Получить ограничение для конкретного игрока на текущем зубе.
        Каждый игрок получает своё ограничение из пула (детерминистично)."""
        if not room.active_constraints:
            return None
        pool = room.active_constraints
        try:
            player_idx = room.player_order.index(user_id)
        except ValueError:
            player_idx = 0
        # Разные ограничения для разных игроков на одном зубе
        idx = (room.current_tooth * 7 + player_idx) % len(pool)
        return pool[idx]

    def get_current_task(self, room: Room, user_id: int) -> dict | None:
        """Что должен сделать игрок на текущем зубе."""
        if room.state != GameState.WRITING:
            return None
        if user_id in room.tooth_submitted:
            return None

        skull = room.get_skull_for_writer(user_id, room.current_tooth)
        if not skull:
            return None

        is_first_tooth = room.current_tooth == 0

        # Ограничение — у каждого игрока своё (из пула, по индексу)
        constraint = self._get_player_constraint(room, user_id)

        return {
            "skull_id": skull.skull_id,
            "visible": skull.current_visible,
            "is_character": is_first_tooth,
            "tooth": room.current_tooth,
            "constraint": constraint,
            "character": skull.character if is_first_tooth else None,
        }

    def submit_word(self, room_id: str, user_id: int, word: str) -> dict:
        """Записать слово-ассоциацию."""
        room = self.rooms.get(room_id)
        if not room:
            raise GameError("Комната не найдена")
        if room.state != GameState.WRITING:
            raise GameError("Сейчас не этап ассоциаций")
        if user_id not in room.players:
            raise GameError("Ты не в комнате")
        if user_id in room.tooth_submitted:
            raise GameError("Ты уже написал слово на этом шаге")

        word = word.strip()

        skull = room.get_skull_for_writer(user_id, room.current_tooth)
        if not skull:
            raise GameError("Не найден череп для тебя")

        # Определяем предыдущее слово (для проверки однокоренных)
        previous = skull.last_word
        character = skull.character

        # Ограничение — у каждого игрока своё
        constraint = self._get_player_constraint(room, user_id)

        # Валидация
        err = validate_word(word, previous_word=previous,
                           character=character, constraint=constraint)
        if err:
            raise GameError(err)

        step = AssociationStep(
            author_id=user_id,
            word=word,
            step=room.current_tooth,
        )
        skull.steps.append(step)
        skull.teeth_filled = room.current_tooth + 1
        room.tooth_submitted.add(user_id)
        room.last_activity = time.time()

        logger.info(
            f"Комната {room_id}: {user_id} написал '{word}' на зубе {room.current_tooth} "
            f"(череп {skull.skull_id}, персонаж: {skull.character})"
        )

        tooth_complete = len(room.tooth_submitted) >= room.num_players
        game_phase_changed = False

        if tooth_complete:
            room.current_tooth += 1
            room.tooth_submitted.clear()

            if room.current_tooth >= room.total_teeth:
                room.state = GameState.GUESSING
                room.guesses.clear()
                room.guessing_done.clear()
                room.guessing_progress.clear()
                game_phase_changed = True
                logger.info(f"Комната {room_id}: все {room.total_teeth} зубов заполнены, переход к угадыванию")

        return {
            "tooth_complete": tooth_complete,
            "game_phase_changed": game_phase_changed,
        }

    def skip_player(self, room_id: str, user_id: int):
        """Пропустить по таймауту."""
        room = self.rooms.get(room_id)
        if not room or room.state != GameState.WRITING:
            return
        if user_id in room.tooth_submitted:
            return
        skull = room.get_skull_for_writer(user_id, room.current_tooth)
        if skull:
            step = AssociationStep(author_id=user_id, word="(пропуск)", step=room.current_tooth)
            skull.steps.append(step)
            skull.teeth_filled = room.current_tooth + 1
        room.tooth_submitted.add(user_id)
        logger.warning(f"Комната {room_id}: {user_id} пропущен по таймауту")

    # ─── Этап угадывания ───

    def get_guessing_data(self, room: Room) -> dict:
        """Данные для угадывания: последние слова на черепах + все 8 персонажей."""
        skulls_data = []
        for skull in room.skulls.values():
            skulls_data.append({
                "skull_id": skull.skull_id,
                "last_word": skull.last_word or "...",
            })
        random.shuffle(skulls_data)

        return {
            "skulls": skulls_data,
            "characters": room.all_characters[:],  # Уже перемешаны
        }

    def submit_guess(self, room_id: str, user_id: int,
                     skull_id: str, character: str) -> dict:
        room = self.rooms.get(room_id)
        if not room:
            raise GameError("Комната не найдена")
        if room.state != GameState.GUESSING:
            raise GameError("Сейчас не этап угадывания")
        if user_id not in room.players:
            raise GameError("Ты не в комнате")
        if user_id in room.guessing_done:
            raise GameError("Ты уже завершил угадывание")

        if user_id not in room.guesses:
            room.guesses[user_id] = {}

        room.guesses[user_id][skull_id] = character
        room.last_activity = time.time()

        guess_count = len(room.guesses[user_id])
        total = room.num_players  # Угадываем только черепа игроков

        if guess_count >= total:
            room.guessing_done.add(user_id)

        all_done = len(room.guessing_done) >= room.num_players

        if all_done:
            room.state = GameState.SCORING

        return {
            "guess_count": guess_count,
            "total": total,
            "all_done": all_done,
        }

    def force_finish_guessing(self, room_id: str):
        room = self.rooms.get(room_id)
        if not room or room.state != GameState.GUESSING:
            return
        room.state = GameState.SCORING

    # ─── Подсчёт результатов ───

    def calculate_results(self, room_id: str) -> dict:
        """
        Кооперативный подсчёт:
        - Для каждого черепа: сколько игроков угадали правильно
        - Порог упокоения = N-1
        - Если все N угадали — жетон кости
        - Жетоны кости компенсируют недобор
        """
        room = self.rooms.get(room_id)
        if not room:
            raise GameError("Комната не найдена")

        threshold = room.rest_threshold
        earned_bones = 0
        skull_results = []

        for skull in room.skulls.values():
            correct_count = 0
            player_guesses = []

            for uid in room.players:
                guesses = room.guesses.get(uid, {})
                guessed = guesses.get(skull.skull_id, "")
                is_correct = guessed == skull.character
                if is_correct:
                    correct_count += 1
                player_guesses.append({
                    "user_id": uid,
                    "name": room.players[uid].first_name,
                    "guessed": guessed,
                    "correct": is_correct,
                })

            # Бонусный жетон кости если ВСЕ угадали
            if correct_count >= room.num_players:
                earned_bones += 1

            room.skull_scores[skull.skull_id] = correct_count

            skull_results.append({
                "skull_id": skull.skull_id,
                "character": skull.character,
                "last_word": skull.last_word,
                "correct_count": correct_count,
                "threshold": threshold,
                "rested": correct_count >= threshold,
                "all_correct": correct_count >= room.num_players,
                "chain": [
                    {"author": room.players.get(s.author_id, Player(0, "", "???")).first_name,
                     "word": s.word}
                    for s in skull.steps
                ],
                "player_guesses": player_guesses,
            })

        room.bone_tokens += earned_bones
        total_bones = room.bone_tokens

        # Применяем жетоны кости к не-упокоенным
        not_rested = [s for s in skull_results if not s["rested"]]
        not_rested.sort(key=lambda s: s["threshold"] - s["correct_count"])

        bones_used = 0
        for s in not_rested:
            deficit = threshold - s["correct_count"]
            if deficit <= total_bones - bones_used:
                s["rested"] = True
                s["bones_used"] = deficit
                bones_used += deficit
            else:
                s["bones_used"] = 0

        rested_count = sum(1 for s in skull_results if s["rested"])
        total_skulls = len(skull_results)

        room.state = GameState.FINISHED

        logger.info(
            f"Комната {room_id}: упокоено {rested_count}/{total_skulls}, "
            f"жетонов кости: {room.initial_bone_tokens}+{earned_bones}, использовано: {bones_used}"
        )

        return {
            "skulls": skull_results,
            "rested_count": rested_count,
            "total_skulls": total_skulls,
            "initial_bones": room.initial_bone_tokens,
            "earned_bones": earned_bones,
            "bones_used": bones_used,
            "threshold": threshold,
        }

    # ─── Очистка ───

    def cleanup_stale_rooms(self, max_age: float = 3600):
        now = time.time()
        to_delete = [
            rid for rid, room in self.rooms.items()
            if now - room.last_activity > max_age
            and room.state in (GameState.LOBBY, GameState.FINISHED)
        ]
        for rid in to_delete:
            del self.rooms[rid]
