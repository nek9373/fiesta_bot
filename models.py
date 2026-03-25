"""
Модели данных для игры Fiesta.

Принятые допущения:
1. Карточка проходит через ВСЕХ остальных игроков (N-1 ассоциаций на карточку для N игроков)
2. Владелец НЕ пишет ассоциацию на свою карточку
3. На этапе угадывания каждый угадывает ВСЕ карточки (включая свою — это бесплатное очко)
4. Очки: 1 балл за правильное сопоставление
5. Минимум 3 игрока, максимум 10
6. Таймаут на ассоциацию: 120 сек, на угадывание: 300 сек
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class GameState(str, Enum):
    LOBBY = "lobby"
    COLLECTING_CARDS = "collecting_cards"  # Игроки добавляют своих персонажей
    DEALING = "dealing"
    WRITING = "writing"
    GUESSING = "guessing"
    REVEAL = "reveal"
    FINISHED = "finished"


class CardSource(str, Enum):
    DEFAULT = "default"          # Только встроенный банк
    CUSTOM = "custom"            # Только пользовательский
    MIXED = "mixed"              # Смешанный


@dataclass
class Player:
    user_id: int
    username: str
    first_name: str
    is_host: bool = False
    joined_at: float = field(default_factory=time.time)
    dm_available: bool = False   # Может ли бот писать в ЛС


@dataclass
class Association:
    author_id: int
    text: str
    step: int                    # Номер шага (0 = первая ассоциация)
    written_at: float = field(default_factory=time.time)


@dataclass
class Card:
    card_id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    character: str = ""
    owner_id: int = 0            # Кому изначально выдана
    associations: list[Association] = field(default_factory=list)

    @property
    def last_association(self) -> Optional[str]:
        """Последняя написанная ассоциация (видна следующему игроку)."""
        if self.associations:
            return self.associations[-1].text
        return None

    @property
    def visible_text(self) -> str:
        """Что видит следующий игрок: последнюю ассоциацию или имя персонажа (на шаге 0)."""
        if self.associations:
            return self.associations[-1].text
        return self.character


@dataclass
class RoomSettings:
    min_players: int = 3
    max_players: int = 10
    association_timeout: int = 120
    guessing_timeout: int = 300
    card_source: CardSource = CardSource.DEFAULT
    category: str = "mixed"      # books / movies / series / mixed


@dataclass
class Room:
    room_id: str
    host_id: int
    group_chat_id: Optional[int] = None
    settings: RoomSettings = field(default_factory=RoomSettings)
    state: GameState = GameState.LOBBY
    players: dict[int, Player] = field(default_factory=dict)
    cards: dict[str, Card] = field(default_factory=dict)
    player_order: list[int] = field(default_factory=list)
    current_step: int = 0
    custom_characters: list[str] = field(default_factory=list)
    # Кто уже написал ассоциацию на текущем шаге
    step_submitted: set[int] = field(default_factory=set)
    # Угадывания: user_id -> {card_id: guessed_character}
    guesses: dict[int, dict[str, str]] = field(default_factory=dict)
    # Кто завершил угадывание
    guessing_done: set[int] = field(default_factory=set)
    # Промежуточное состояние угадывания: user_id -> текущий индекс карточки
    guessing_progress: dict[int, int] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    # Сообщения статуса в групповом чате (для редактирования)
    status_message_id: Optional[int] = None

    @property
    def num_players(self) -> int:
        return len(self.players)

    @property
    def total_steps(self) -> int:
        """Сколько шагов ассоциаций (N-1 для N игроков)."""
        return max(0, self.num_players - 1)

    def get_writer_for_card(self, card: Card, step: int) -> int:
        """Кто пишет ассоциацию для карточки на данном шаге."""
        owner_idx = self.player_order.index(card.owner_id)
        writer_idx = (owner_idx + step + 1) % self.num_players
        return self.player_order[writer_idx]

    def get_card_for_writer(self, writer_id: int, step: int) -> Optional[Card]:
        """Какую карточку держит данный игрок на данном шаге."""
        writer_idx = self.player_order.index(writer_id)
        for card in self.cards.values():
            owner_idx = self.player_order.index(card.owner_id)
            expected_writer_idx = (owner_idx + step + 1) % self.num_players
            if expected_writer_idx == writer_idx:
                return card
        return None

    def transfer_host(self):
        """Передать хост следующему игроку."""
        if not self.players:
            return
        for uid, p in self.players.items():
            if uid != self.host_id:
                p.is_host = True
                self.host_id = uid
                return
