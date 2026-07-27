"""SQLite-хранилище статистики и истории звуков."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path


class GuessSoundStore:
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.lock = asyncio.Lock()
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS used_sounds (
                chat_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                external_id TEXT NOT NULL,
                used_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, provider, external_id)
            );
            CREATE TABLE IF NOT EXISTS player_stats (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                games INTEGER NOT NULL DEFAULT 0 CHECK (games >= 0),
                wins INTEGER NOT NULL DEFAULT 0 CHECK (wins >= 0),
                PRIMARY KEY (chat_id, user_id)
            );
            """
        )
        self.connection.commit()

    async def used_ids(self, chat_id: int, provider: str) -> set[str]:
        async with self.lock:
            rows = self.connection.execute(
                """
                SELECT external_id FROM used_sounds
                WHERE chat_id = ? AND provider = ?
                """,
                (chat_id, provider),
            ).fetchall()
            return {str(row["external_id"]) for row in rows}

    async def mark_used(
        self, chat_id: int, provider: str, external_id: str
    ) -> None:
        async with self.lock:
            self.connection.execute(
                """
                INSERT OR IGNORE INTO used_sounds(chat_id, provider, external_id)
                VALUES (?, ?, ?)
                """,
                (chat_id, provider, external_id),
            )
            self.connection.commit()

    async def record_round(
        self,
        chat_id: int,
        players: dict[int, str],
        winners: set[int],
    ) -> None:
        async with self.lock:
            for user_id, name in players.items():
                self.connection.execute(
                    """
                    INSERT INTO player_stats(
                        chat_id, user_id, display_name, games, wins
                    ) VALUES (?, ?, ?, 1, ?)
                    ON CONFLICT(chat_id, user_id) DO UPDATE SET
                        display_name = excluded.display_name,
                        games = player_stats.games + 1,
                        wins = player_stats.wins + excluded.wins
                    """,
                    (chat_id, user_id, name, int(user_id in winners)),
                )
            self.connection.commit()

    async def info(self, chat_id: int, user_id: int) -> sqlite3.Row | None:
        async with self.lock:
            return self.connection.execute(
                """
                SELECT display_name, games, wins
                FROM player_stats WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, user_id),
            ).fetchone()

    async def top(self, chat_id: int, limit: int = 10) -> list[sqlite3.Row]:
        async with self.lock:
            return self.connection.execute(
                """
                SELECT display_name, games, wins,
                       CASE WHEN games = 0 THEN 0.0
                            ELSE CAST(wins AS REAL) / games END AS win_rate
                FROM player_stats WHERE chat_id = ?
                ORDER BY wins DESC, win_rate DESC, games DESC,
                         display_name COLLATE NOCASE ASC
                LIMIT ?
                """,
                (chat_id, limit),
            ).fetchall()
