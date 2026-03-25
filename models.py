"""
Модели данных для Fiesta: Карнавал мёртвых.

Оригинальные правила (формализация):
1. Кооперативная игра, 4-8 игроков
2. Каждый берёт карточку персонажа, пишет имя на "черепе"
3. Пишет ОДНО слово-ассоциацию, закрашивает зуб, передаёт ВЛЕВО
4. Следующий СТИРАЕТ слово, пишет своё (ассоциация на прочитанное)
5. Ровно 4 круга (4 зуба)
6. После 4 кругов — черепа в центр, добавляют персонажей из колоды до 8
7. Каждый молча сопоставляет последние слова с персонажами
8. Подсчёт: для каждого черепа считают правильные ответы ВСЕХ игроков.
   Порог = N-1. Если все N угадали — бонусный жетон кости.
9. Жетоны кости компенсируют недобор по другим черепам
10. Запрещено: имена персонажей, однокоренные с полученным словом, >1 слова
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

TOTAL_TEETH = 4           # Фиксированное число кругов ассоциаций
TOTAL_CHARACTERS = 8      # Всегда 8 персонажей на столе при угадывании
MIN_PLAYERS = 4
MAX_PLAYERS = 8


class GameState(str, Enum):
    LOBBY = "lobby"
    COLLECTING_CARDS = "collecting_cards"
    WRITING = "writing"           # Этап ассоциаций
    GUESSING = "guessing"         # Этап угадывания
    SCORING = "scoring"           # Подсчёт (жетоны кости)
    FINISHED = "finished"


class CardSource(str, Enum):
    DEFAULT = "default"
    CUSTOM = "custom"
    MIXED = "mixed"


class ConstraintType(str, Enum):
    THEME_OBJECT = "предмет"
    THEME_PLACE = "место"
    THEME_NATURE = "природа"
    MAX_6_LETTERS = "не более 6 букв"
    NO_LETTER_E = "без буквы Е"
    ENDS_WITH_A = "на -А"
    STARTS_WITH_M = "на букву М"
    STARTS_WITH_P = "на букву П"
    STARTS_WITH_T = "на букву Т"
    STARTS_WITH_R = "на букву Р"
    STARTS_WITH_S = "на букву С"
    STARTS_WITH_D = "на букву Д"


@dataclass
class Player:
    user_id: int
    username: str
    first_name: str
    is_host: bool = False
    joined_at: float = field(default_factory=time.time)
    dm_available: bool = False


@dataclass
class AssociationStep:
    """Один шаг ассоциации на черепе."""
    author_id: int
    word: str                    # Одно слово
    step: int                    # 0..3 (номер зуба)
    written_at: float = field(default_factory=time.time)


@dataclass
class Skull:
    """Планшет черепа — аналог карточки."""
    skull_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    character: str = ""          # Имя персонажа (написано внутри)
    owner_id: int = 0            # Кто изначально получил
    steps: list[AssociationStep] = field(default_factory=list)
    teeth_filled: int = 0        # Сколько зубов закрашено (0..4)

    @property
    def last_word(self) -> Optional[str]:
        """Последнее написанное слово (видно при угадывании)."""
        if self.steps:
            return self.steps[-1].word
        return None

    @property
    def current_visible(self) -> str:
        """Что видит текущий игрок: последнее слово или имя персонажа."""
        if self.steps:
            return self.steps[-1].word
        return self.character


@dataclass
class RoomSettings:
    min_players: int = MIN_PLAYERS
    max_players: int = MAX_PLAYERS
    association_timeout: int = 90     # Сек на одно слово
    guessing_timeout: int = 300       # Сек на угадывание
    card_source: CardSource = CardSource.DEFAULT
    category: str = "mixed"
    difficulty_level: int = 0         # 0=без ограничений, 1-3=уровни


@dataclass
class Room:
    room_id: str
    host_id: int
    group_chat_id: Optional[int] = None
    settings: RoomSettings = field(default_factory=RoomSettings)
    state: GameState = GameState.LOBBY
    players: dict[int, Player] = field(default_factory=dict)
    # Порядок игроков (по кругу, передача ВЛЕВО = следующий в списке)
    player_order: list[int] = field(default_factory=list)
    # Черепа в игре (skull_id -> Skull)
    skulls: dict[str, Skull] = field(default_factory=dict)
    # Персонажи-обманки (добавлены из колоды до 8)
    decoy_characters: list[str] = field(default_factory=list)
    # Все 8 персонажей для угадывания (перемешаны)
    all_characters: list[str] = field(default_factory=list)
    # Текущий зуб (0..3)
    current_tooth: int = 0
    # Кто уже написал слово на текущем зубе
    tooth_submitted: set[int] = field(default_factory=set)
    # Угадывания: user_id -> {skull_id: guessed_character}
    guesses: dict[int, dict[str, str]] = field(default_factory=dict)
    guessing_done: set[int] = field(default_factory=set)
    guessing_progress: dict[int, int] = field(default_factory=dict)
    # Кастомные персонажи от игроков
    custom_characters: list[str] = field(default_factory=list)
    # Ограничения (для уровней сложности)
    active_constraints: list[ConstraintType] = field(default_factory=list)
    # Жетоны кости
    bone_tokens: int = 0
    initial_bone_tokens: int = 0     # Стартовые жетоны по числу игроков
    # Результаты подсчёта: skull_id -> correct_count
    skull_scores: dict[str, int] = field(default_factory=dict)
    # Метаданные
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    status_message_id: Optional[int] = None

    @property
    def num_players(self) -> int:
        return len(self.players)

    @property
    def rest_threshold(self) -> int:
        """Сколько правильных ответов нужно чтобы упокоить мёртвого."""
        return max(1, self.num_players - 1)

    def get_writer_for_skull(self, skull: Skull, tooth: int) -> int:
        """Кто пишет слово на черепе на данном зубе.
        Зуб 0: владелец. Зуб 1+: сосед слева (следующий по кругу)."""
        owner_idx = self.player_order.index(skull.owner_id)
        writer_idx = (owner_idx + tooth) % self.num_players
        return self.player_order[writer_idx]

    def get_skull_for_writer(self, writer_id: int, tooth: int) -> Optional[Skull]:
        """Какой череп держит данный игрок на данном зубе."""
        for skull in self.skulls.values():
            if self.get_writer_for_skull(skull, tooth) == writer_id:
                return skull
        return None

    def transfer_host(self):
        if not self.players:
            return
        for uid in self.players:
            if uid != self.host_id:
                self.players[uid].is_host = True
                self.host_id = uid
                return
