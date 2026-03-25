"""
SQLite хранилище для Fiesta.
Сохраняет комнаты, игроков, карточки, ассоциации, результаты.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

from models import (
    Association, Card, CardSource, GameState, Player, Room, RoomSettings,
)

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "fiesta.db"


class FiestaStore:
    def __init__(self, db_path: str | Path = DB_PATH):
        self.db_path = str(db_path)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        conn = self._conn()
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS rooms (
            room_id TEXT PRIMARY KEY,
            host_id INTEGER NOT NULL,
            group_chat_id INTEGER,
            state TEXT NOT NULL DEFAULT 'lobby',
            settings_json TEXT NOT NULL DEFAULT '{}',
            player_order_json TEXT NOT NULL DEFAULT '[]',
            custom_characters_json TEXT NOT NULL DEFAULT '[]',
            current_step INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            last_activity REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS players (
            user_id INTEGER NOT NULL,
            room_id TEXT NOT NULL,
            username TEXT NOT NULL DEFAULT '',
            first_name TEXT NOT NULL DEFAULT '',
            is_host INTEGER NOT NULL DEFAULT 0,
            dm_available INTEGER NOT NULL DEFAULT 0,
            joined_at REAL NOT NULL,
            PRIMARY KEY (user_id, room_id),
            FOREIGN KEY (room_id) REFERENCES rooms(room_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS cards (
            card_id TEXT PRIMARY KEY,
            room_id TEXT NOT NULL,
            character TEXT NOT NULL,
            owner_id INTEGER NOT NULL,
            FOREIGN KEY (room_id) REFERENCES rooms(room_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS associations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            card_id TEXT NOT NULL,
            author_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            step INTEGER NOT NULL,
            written_at REAL NOT NULL,
            FOREIGN KEY (card_id) REFERENCES cards(card_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS guesses (
            user_id INTEGER NOT NULL,
            room_id TEXT NOT NULL,
            card_id TEXT NOT NULL,
            guessed_character TEXT NOT NULL,
            PRIMARY KEY (user_id, room_id, card_id),
            FOREIGN KEY (room_id) REFERENCES rooms(room_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS game_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            score INTEGER NOT NULL,
            total INTEGER NOT NULL,
            played_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_players_room ON players(room_id);
        CREATE INDEX IF NOT EXISTS idx_cards_room ON cards(room_id);
        CREATE INDEX IF NOT EXISTS idx_assoc_card ON associations(card_id);
        CREATE INDEX IF NOT EXISTS idx_results_user ON game_results(user_id);
        """)
        conn.commit()
        conn.close()
        logger.info(f"БД инициализирована: {self.db_path}")

    # ─── Сохранение / загрузка комнат ───

    def save_room(self, room: Room):
        """Сохранить полное состояние комнаты."""
        conn = self._conn()
        try:
            settings = {
                "min_players": room.settings.min_players,
                "max_players": room.settings.max_players,
                "association_timeout": room.settings.association_timeout,
                "guessing_timeout": room.settings.guessing_timeout,
                "card_source": room.settings.card_source.value,
                "category": room.settings.category,
            }

            conn.execute("""
                INSERT OR REPLACE INTO rooms
                (room_id, host_id, group_chat_id, state, settings_json,
                 player_order_json, custom_characters_json, current_step,
                 created_at, last_activity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                room.room_id, room.host_id, room.group_chat_id,
                room.state.value, json.dumps(settings),
                json.dumps(room.player_order),
                json.dumps(room.custom_characters),
                room.current_step,
                room.created_at, room.last_activity,
            ))

            # Игроки
            conn.execute("DELETE FROM players WHERE room_id = ?", (room.room_id,))
            for p in room.players.values():
                conn.execute("""
                    INSERT INTO players (user_id, room_id, username, first_name,
                                        is_host, dm_available, joined_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (p.user_id, room.room_id, p.username, p.first_name,
                      int(p.is_host), int(p.dm_available), p.joined_at))

            # Карточки и ассоциации
            conn.execute(
                "DELETE FROM associations WHERE card_id IN "
                "(SELECT card_id FROM cards WHERE room_id = ?)",
                (room.room_id,)
            )
            conn.execute("DELETE FROM cards WHERE room_id = ?", (room.room_id,))

            for card in room.cards.values():
                conn.execute("""
                    INSERT INTO cards (card_id, room_id, character, owner_id)
                    VALUES (?, ?, ?, ?)
                """, (card.card_id, room.room_id, card.character, card.owner_id))

                for assoc in card.associations:
                    conn.execute("""
                        INSERT INTO associations (card_id, author_id, text, step, written_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (card.card_id, assoc.author_id, assoc.text,
                          assoc.step, assoc.written_at))

            # Угадывания
            conn.execute("DELETE FROM guesses WHERE room_id = ?", (room.room_id,))
            for uid, guesses in room.guesses.items():
                for card_id, character in guesses.items():
                    conn.execute("""
                        INSERT INTO guesses (user_id, room_id, card_id, guessed_character)
                        VALUES (?, ?, ?, ?)
                    """, (uid, room.room_id, card_id, character))

            conn.commit()
        finally:
            conn.close()

    def load_room(self, room_id: str) -> Optional[Room]:
        """Загрузить комнату из БД."""
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM rooms WHERE room_id = ?", (room_id,)).fetchone()
            if not row:
                return None

            settings_data = json.loads(row["settings_json"])
            settings = RoomSettings(
                min_players=settings_data.get("min_players", 3),
                max_players=settings_data.get("max_players", 10),
                association_timeout=settings_data.get("association_timeout", 120),
                guessing_timeout=settings_data.get("guessing_timeout", 300),
                card_source=CardSource(settings_data.get("card_source", "default")),
                category=settings_data.get("category", "mixed"),
            )

            room = Room(
                room_id=row["room_id"],
                host_id=row["host_id"],
                group_chat_id=row["group_chat_id"],
                state=GameState(row["state"]),
                settings=settings,
                player_order=json.loads(row["player_order_json"]),
                custom_characters=json.loads(row["custom_characters_json"]),
                current_step=row["current_step"],
                created_at=row["created_at"],
                last_activity=row["last_activity"],
            )

            # Игроки
            for p_row in conn.execute("SELECT * FROM players WHERE room_id = ?", (room_id,)):
                room.players[p_row["user_id"]] = Player(
                    user_id=p_row["user_id"],
                    username=p_row["username"],
                    first_name=p_row["first_name"],
                    is_host=bool(p_row["is_host"]),
                    dm_available=bool(p_row["dm_available"]),
                    joined_at=p_row["joined_at"],
                )

            # Карточки
            for c_row in conn.execute("SELECT * FROM cards WHERE room_id = ?", (room_id,)):
                card = Card(
                    card_id=c_row["card_id"],
                    character=c_row["character"],
                    owner_id=c_row["owner_id"],
                )
                # Ассоциации
                for a_row in conn.execute(
                    "SELECT * FROM associations WHERE card_id = ? ORDER BY step",
                    (c_row["card_id"],)
                ):
                    card.associations.append(Association(
                        author_id=a_row["author_id"],
                        text=a_row["text"],
                        step=a_row["step"],
                        written_at=a_row["written_at"],
                    ))
                room.cards[card.card_id] = card

            # Угадывания
            for g_row in conn.execute("SELECT * FROM guesses WHERE room_id = ?", (room_id,)):
                uid = g_row["user_id"]
                if uid not in room.guesses:
                    room.guesses[uid] = {}
                room.guesses[uid][g_row["card_id"]] = g_row["guessed_character"]

            return room
        finally:
            conn.close()

    def save_result(self, room_id: str, user_id: int, score: int, total: int):
        """Сохранить результат игры."""
        conn = self._conn()
        try:
            conn.execute("""
                INSERT INTO game_results (room_id, user_id, score, total, played_at)
                VALUES (?, ?, ?, ?, ?)
            """, (room_id, user_id, score, total, time.time()))
            conn.commit()
        finally:
            conn.close()

    def get_player_stats(self, user_id: int) -> dict:
        """Статистика игрока."""
        conn = self._conn()
        try:
            rows = conn.execute("""
                SELECT COUNT(*) as games, SUM(score) as total_score,
                       SUM(total) as total_possible,
                       AVG(CAST(score AS REAL) / total) as avg_rate
                FROM game_results WHERE user_id = ?
            """, (user_id,)).fetchone()

            return {
                "games": rows["games"] or 0,
                "total_score": rows["total_score"] or 0,
                "total_possible": rows["total_possible"] or 0,
                "avg_rate": round(rows["avg_rate"] or 0, 2),
            }
        finally:
            conn.close()

    def delete_room(self, room_id: str):
        """Удалить комнату."""
        conn = self._conn()
        try:
            conn.execute("DELETE FROM rooms WHERE room_id = ?", (room_id,))
            conn.commit()
        finally:
            conn.close()
