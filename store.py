"""
SQLite хранилище для Fiesta: Карнавал мёртвых.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

from models import (
    AssociationStep, CardSource, GameState, Player, Room, RoomSettings, Skull,
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
            all_characters_json TEXT NOT NULL DEFAULT '[]',
            decoy_characters_json TEXT NOT NULL DEFAULT '[]',
            current_tooth INTEGER NOT NULL DEFAULT 0,
            bone_tokens INTEGER NOT NULL DEFAULT 0,
            initial_bone_tokens INTEGER NOT NULL DEFAULT 0,
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

        CREATE TABLE IF NOT EXISTS skulls (
            skull_id TEXT PRIMARY KEY,
            room_id TEXT NOT NULL,
            character TEXT NOT NULL,
            owner_id INTEGER NOT NULL,
            teeth_filled INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (room_id) REFERENCES rooms(room_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            skull_id TEXT NOT NULL,
            author_id INTEGER NOT NULL,
            word TEXT NOT NULL,
            step INTEGER NOT NULL,
            written_at REAL NOT NULL,
            FOREIGN KEY (skull_id) REFERENCES skulls(skull_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS guesses (
            user_id INTEGER NOT NULL,
            room_id TEXT NOT NULL,
            skull_id TEXT NOT NULL,
            guessed_character TEXT NOT NULL,
            PRIMARY KEY (user_id, room_id, skull_id),
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
        CREATE INDEX IF NOT EXISTS idx_skulls_room ON skulls(room_id);
        CREATE INDEX IF NOT EXISTS idx_steps_skull ON steps(skull_id);
        CREATE INDEX IF NOT EXISTS idx_results_user ON game_results(user_id);
        """)
        conn.commit()
        conn.close()
        logger.info(f"БД инициализирована: {self.db_path}")

    def save_room(self, room: Room):
        conn = self._conn()
        try:
            settings = {
                "min_players": room.settings.min_players,
                "max_players": room.settings.max_players,
                "association_timeout": room.settings.association_timeout,
                "guessing_timeout": room.settings.guessing_timeout,
                "card_source": room.settings.card_source.value,
                "category": room.settings.category,
                "difficulty_level": room.settings.difficulty_level,
            }

            conn.execute("""
                INSERT OR REPLACE INTO rooms
                (room_id, host_id, group_chat_id, state, settings_json,
                 player_order_json, custom_characters_json, all_characters_json,
                 decoy_characters_json, current_tooth, bone_tokens,
                 initial_bone_tokens, created_at, last_activity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                room.room_id, room.host_id, room.group_chat_id,
                room.state.value, json.dumps(settings),
                json.dumps(room.player_order),
                json.dumps(room.custom_characters),
                json.dumps(room.all_characters),
                json.dumps(room.decoy_characters),
                room.current_tooth, room.bone_tokens,
                room.initial_bone_tokens,
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

            # Черепа и шаги
            conn.execute(
                "DELETE FROM steps WHERE skull_id IN "
                "(SELECT skull_id FROM skulls WHERE room_id = ?)",
                (room.room_id,)
            )
            conn.execute("DELETE FROM skulls WHERE room_id = ?", (room.room_id,))

            for skull in room.skulls.values():
                conn.execute("""
                    INSERT INTO skulls (skull_id, room_id, character, owner_id, teeth_filled)
                    VALUES (?, ?, ?, ?, ?)
                """, (skull.skull_id, room.room_id, skull.character,
                      skull.owner_id, skull.teeth_filled))

                for step in skull.steps:
                    conn.execute("""
                        INSERT INTO steps (skull_id, author_id, word, step, written_at)
                        VALUES (?, ?, ?, ?, ?)
                    """, (skull.skull_id, step.author_id, step.word,
                          step.step, step.written_at))

            # Угадывания
            conn.execute("DELETE FROM guesses WHERE room_id = ?", (room.room_id,))
            for uid, guesses in room.guesses.items():
                for skull_id, character in guesses.items():
                    conn.execute("""
                        INSERT INTO guesses (user_id, room_id, skull_id, guessed_character)
                        VALUES (?, ?, ?, ?)
                    """, (uid, room.room_id, skull_id, character))

            conn.commit()
        finally:
            conn.close()

    def save_result(self, room_id: str, user_id: int, score: int, total: int):
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
        conn = self._conn()
        try:
            conn.execute("DELETE FROM rooms WHERE room_id = ?", (room_id,))
            conn.commit()
        finally:
            conn.close()
