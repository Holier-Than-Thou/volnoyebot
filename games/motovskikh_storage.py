"""SQLite storage for Telegram to Motovskikh account links."""

from __future__ import annotations

import sqlite3
import time


def initialize_motovskikh_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS motovskikh_links (
            telegram_user_id INTEGER PRIMARY KEY,
            motovskikh_player_id INTEGER NOT NULL UNIQUE,
            last_nickname TEXT NOT NULL,
            linked_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )


class MotovskikhStoreMixin:
    async def get_motovskikh_link(self, telegram_user_id: int) -> sqlite3.Row | None:
        async with self.lock:
            return self.connection.execute(
                """
                SELECT telegram_user_id, motovskikh_player_id, last_nickname,
                       linked_at, updated_at
                FROM motovskikh_links
                WHERE telegram_user_id = ?
                """,
                (telegram_user_id,),
            ).fetchone()

    async def has_active_wager(self, telegram_user_id: int) -> bool:
        """Check wagers whose identity must remain frozen."""
        async with self.lock:
            row = self.connection.execute(
                """
                SELECT 1
                FROM dice_challenges
                WHERE status IN ('pending', 'playing')
                  AND (challenger_id = ? OR opponent_id = ?)
                LIMIT 1
                """,
                (telegram_user_id, telegram_user_id),
            ).fetchone()
            return row is not None

    async def save_motovskikh_link(
        self,
        telegram_user_id: int,
        motovskikh_player_id: int,
        nickname: str,
    ) -> str:
        """Atomically save a one-to-one account link."""
        async with self.lock:
            active_wager = self.connection.execute(
                """
                SELECT 1
                FROM dice_challenges
                WHERE status IN ('pending', 'playing')
                  AND (challenger_id = ? OR opponent_id = ?)
                LIMIT 1
                """,
                (telegram_user_id, telegram_user_id),
            ).fetchone()
            if active_wager is not None:
                return "active_wager"

            owner = self.connection.execute(
                """
                SELECT telegram_user_id
                FROM motovskikh_links
                WHERE motovskikh_player_id = ?
                """,
                (motovskikh_player_id,),
            ).fetchone()
            if owner is not None and owner["telegram_user_id"] != telegram_user_id:
                return "conflict"

            now = time.time()
            current = self.connection.execute(
                """
                SELECT motovskikh_player_id
                FROM motovskikh_links
                WHERE telegram_user_id = ?
                """,
                (telegram_user_id,),
            ).fetchone()
            if (
                current is not None
                and current["motovskikh_player_id"] == motovskikh_player_id
            ):
                self.connection.execute(
                    """
                    UPDATE motovskikh_links
                    SET last_nickname = ?, updated_at = ?
                    WHERE telegram_user_id = ?
                    """,
                    (nickname, now, telegram_user_id),
                )
                self.connection.commit()
                return "already_linked"

            linked_at = now if current is None else None
            self.connection.execute(
                """
                INSERT INTO motovskikh_links(
                    telegram_user_id, motovskikh_player_id, last_nickname,
                    linked_at, updated_at
                )
                VALUES (?, ?, ?, COALESCE(?, ?), ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    motovskikh_player_id = excluded.motovskikh_player_id,
                    last_nickname = excluded.last_nickname,
                    updated_at = excluded.updated_at
                """,
                (
                    telegram_user_id,
                    motovskikh_player_id,
                    nickname,
                    linked_at,
                    now,
                    now,
                ),
            )
            self.connection.commit()
            return "linked"
