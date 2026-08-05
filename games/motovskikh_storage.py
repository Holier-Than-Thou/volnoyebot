"""SQLite storage for Motovskikh account links and online matches."""

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
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS motovskikh_challenges (
            chat_id INTEGER NOT NULL,
            topic_id INTEGER NOT NULL DEFAULT 0,
            proposal_message_id INTEGER NOT NULL,
            challenger_id INTEGER NOT NULL,
            challenger_name TEXT NOT NULL,
            challenger_motovskikh_id INTEGER NOT NULL,
            opponent_id INTEGER NOT NULL,
            opponent_name TEXT NOT NULL,
            opponent_motovskikh_id INTEGER NOT NULL,
            stake INTEGER NOT NULL CHECK (stake >= 0),
            test_slug TEXT NOT NULL,
            test_url TEXT NOT NULL,
            room_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            challenger_score TEXT,
            opponent_score TEXT,
            winner_id INTEGER,
            created_at REAL NOT NULL,
            accepted_at REAL,
            finished_at REAL,
            PRIMARY KEY (chat_id, proposal_message_id)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_motovskikh_challenges_players
        ON motovskikh_challenges(status, challenger_id, opponent_id)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_motovskikh_challenges_chat_history
        ON motovskikh_challenges(chat_id, status, finished_at DESC)
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
            if row is not None:
                return True
            row = self.connection.execute(
                """
                SELECT 1
                FROM motovskikh_challenges
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
            if active_wager is None:
                active_wager = self.connection.execute(
                    """
                    SELECT 1
                    FROM motovskikh_challenges
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

    async def create_motovskikh_challenge(
        self,
        chat_id: int,
        topic_id: int,
        proposal_message_id: int,
        challenger_id: int,
        challenger_name: str,
        challenger_motovskikh_id: int,
        opponent_id: int,
        opponent_name: str,
        opponent_motovskikh_id: int,
        stake: int,
        test_slug: str,
        test_url: str,
    ) -> str:
        """Create a challenge while preventing either player from overlapping games."""
        async with self.lock:
            placeholders = (challenger_id, opponent_id, challenger_id, opponent_id)
            active = self.connection.execute(
                """
                SELECT 1 FROM motovskikh_challenges
                WHERE status IN ('pending', 'playing')
                  AND (
                    challenger_id IN (?, ?)
                    OR opponent_id IN (?, ?)
                  )
                LIMIT 1
                """,
                placeholders,
            ).fetchone()
            if active is not None:
                return "active"
            dice_active = self.connection.execute(
                """
                SELECT 1 FROM dice_challenges
                WHERE status IN ('pending', 'playing')
                  AND (
                    challenger_id IN (?, ?)
                    OR opponent_id IN (?, ?)
                  )
                LIMIT 1
                """,
                placeholders,
            ).fetchone()
            if dice_active is not None:
                return "active"
            self.connection.execute(
                """
                INSERT INTO motovskikh_challenges(
                    chat_id, topic_id, proposal_message_id,
                    challenger_id, challenger_name, challenger_motovskikh_id,
                    opponent_id, opponent_name, opponent_motovskikh_id,
                    stake, test_slug, test_url, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    chat_id,
                    topic_id,
                    proposal_message_id,
                    challenger_id,
                    challenger_name,
                    challenger_motovskikh_id,
                    opponent_id,
                    opponent_name,
                    opponent_motovskikh_id,
                    stake,
                    test_slug,
                    test_url,
                    time.time(),
                ),
            )
            self.connection.commit()
            return "ok"

    async def accept_motovskikh_challenge(
        self,
        chat_id: int,
        proposal_message_id: int,
        accepting_user_id: int,
        expires_before: float,
        initial_balance: int,
    ) -> tuple[str, dict | None]:
        """Accept a challenge and atomically reserve both stakes."""
        async with self.lock:
            row = self.connection.execute(
                """
                SELECT * FROM motovskikh_challenges
                WHERE chat_id = ? AND proposal_message_id = ?
                  AND status = 'pending'
                """,
                (chat_id, proposal_message_id),
            ).fetchone()
            if row is None:
                return "not_found", None
            challenge = dict(row)
            if challenge["opponent_id"] != accepting_user_id:
                return "wrong_user", None
            if challenge["created_at"] <= expires_before:
                self.connection.execute(
                    """
                    UPDATE motovskikh_challenges SET status = 'expired', finished_at = ?
                    WHERE chat_id = ? AND proposal_message_id = ?
                      AND status = 'pending'
                    """,
                    (time.time(), chat_id, proposal_message_id),
                )
                self.connection.commit()
                return "expired", challenge

            links = {
                item["telegram_user_id"]: item["motovskikh_player_id"]
                for item in self.connection.execute(
                    """
                    SELECT telegram_user_id, motovskikh_player_id
                    FROM motovskikh_links
                    WHERE telegram_user_id IN (?, ?)
                    """,
                    (challenge["challenger_id"], challenge["opponent_id"]),
                )
            }
            if (
                links.get(challenge["challenger_id"])
                != challenge["challenger_motovskikh_id"]
                or links.get(challenge["opponent_id"])
                != challenge["opponent_motovskikh_id"]
            ):
                return "link_changed", challenge

            for user_id, user_name in (
                (challenge["challenger_id"], challenge["challenger_name"]),
                (challenge["opponent_id"], challenge["opponent_name"]),
            ):
                self.connection.execute(
                    """
                    INSERT INTO balances(chat_id, user_id, display_name, balance)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(chat_id, user_id)
                    DO UPDATE SET display_name = excluded.display_name
                    """,
                    (chat_id, user_id, user_name, initial_balance),
                )
            balances = {
                item["user_id"]: int(item["balance"])
                for item in self.connection.execute(
                    """
                    SELECT user_id, balance FROM balances
                    WHERE chat_id = ? AND user_id IN (?, ?)
                    """,
                    (chat_id, challenge["challenger_id"], challenge["opponent_id"]),
                )
            }
            if balances[challenge["challenger_id"]] < challenge["stake"]:
                self.connection.commit()
                return "challenger_funds", challenge
            if balances[challenge["opponent_id"]] < challenge["stake"]:
                self.connection.commit()
                return "opponent_funds", challenge

            if challenge["stake"]:
                self.connection.execute(
                    """
                    UPDATE balances SET balance = balance - ?
                    WHERE chat_id = ? AND user_id IN (?, ?)
                    """,
                    (
                        challenge["stake"],
                        chat_id,
                        challenge["challenger_id"],
                        challenge["opponent_id"],
                    ),
                )
            self.connection.execute(
                """
                UPDATE motovskikh_challenges
                SET status = 'playing', accepted_at = ?
                WHERE chat_id = ? AND proposal_message_id = ?
                  AND status = 'pending'
                """,
                (time.time(), chat_id, proposal_message_id),
            )
            self.connection.commit()
            return "ok", challenge

    async def set_motovskikh_room(
        self, chat_id: int, proposal_message_id: int, room_id: str
    ) -> None:
        async with self.lock:
            self.connection.execute(
                """
                UPDATE motovskikh_challenges SET room_id = ?
                WHERE chat_id = ? AND proposal_message_id = ?
                  AND status = 'playing'
                """,
                (room_id, chat_id, proposal_message_id),
            )
            self.connection.commit()

    async def expire_motovskikh_challenge(
        self, chat_id: int, proposal_message_id: int
    ) -> bool:
        async with self.lock:
            cursor = self.connection.execute(
                """
                UPDATE motovskikh_challenges
                SET status = 'expired', finished_at = ?
                WHERE chat_id = ? AND proposal_message_id = ?
                  AND status = 'pending'
                """,
                (time.time(), chat_id, proposal_message_id),
            )
            self.connection.commit()
            return bool(cursor.rowcount)

    async def get_pending_motovskikh_challenges(self) -> list[sqlite3.Row]:
        async with self.lock:
            return self.connection.execute(
                """
                SELECT chat_id, proposal_message_id, created_at
                FROM motovskikh_challenges WHERE status = 'pending'
                """
            ).fetchall()

    async def refund_motovskikh_challenge(
        self,
        chat_id: int,
        proposal_message_id: int,
        status: str = "failed",
    ) -> dict | None:
        """Refund a playing match exactly once and close it."""
        async with self.lock:
            row = self.connection.execute(
                """
                SELECT * FROM motovskikh_challenges
                WHERE chat_id = ? AND proposal_message_id = ?
                  AND status = 'playing'
                """,
                (chat_id, proposal_message_id),
            ).fetchone()
            if row is None:
                return None
            challenge = dict(row)
            if challenge["stake"]:
                self.connection.execute(
                    """
                    UPDATE balances SET balance = balance + ?
                    WHERE chat_id = ? AND user_id IN (?, ?)
                    """,
                    (
                        challenge["stake"],
                        chat_id,
                        challenge["challenger_id"],
                        challenge["opponent_id"],
                    ),
                )
            self.connection.execute(
                """
                UPDATE motovskikh_challenges
                SET status = ?, finished_at = ?
                WHERE chat_id = ? AND proposal_message_id = ?
                  AND status = 'playing'
                """,
                (status, time.time(), chat_id, proposal_message_id),
            )
            self.connection.commit()
            return challenge

    async def finish_motovskikh_challenge(
        self,
        chat_id: int,
        proposal_message_id: int,
        challenger_score: str,
        opponent_score: str,
        winner_id: int | None,
    ) -> dict:
        """Close a match and pay the bank or refund a tie."""
        async with self.lock:
            row = self.connection.execute(
                """
                SELECT * FROM motovskikh_challenges
                WHERE chat_id = ? AND proposal_message_id = ?
                  AND status = 'playing'
                """,
                (chat_id, proposal_message_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("Motovskikh match is already closed")
            challenge = dict(row)
            if winner_id is None:
                if challenge["stake"]:
                    self.connection.execute(
                        """
                        UPDATE balances SET balance = balance + ?
                        WHERE chat_id = ? AND user_id IN (?, ?)
                        """,
                        (
                            challenge["stake"],
                            chat_id,
                            challenge["challenger_id"],
                            challenge["opponent_id"],
                        ),
                    )
                status = "tie"
                winner_balance = None
            else:
                if winner_id not in {
                    challenge["challenger_id"],
                    challenge["opponent_id"],
                }:
                    raise ValueError("winner is not a match participant")
                if challenge["stake"]:
                    self.connection.execute(
                        """
                        UPDATE balances SET balance = balance + ?
                        WHERE chat_id = ? AND user_id = ?
                        """,
                        (challenge["stake"] * 2, chat_id, winner_id),
                    )
                status = "completed"
                balance_row = self.connection.execute(
                    """
                    SELECT balance FROM balances
                    WHERE chat_id = ? AND user_id = ?
                    """,
                    (chat_id, winner_id),
                ).fetchone()
                winner_balance = int(balance_row["balance"])
            self.connection.execute(
                """
                UPDATE motovskikh_challenges
                SET status = ?, challenger_score = ?, opponent_score = ?,
                    winner_id = ?, finished_at = ?
                WHERE chat_id = ? AND proposal_message_id = ?
                  AND status = 'playing'
                """,
                (
                    status,
                    challenger_score,
                    opponent_score,
                    winner_id,
                    time.time(),
                    chat_id,
                    proposal_message_id,
                ),
            )
            self.connection.commit()
            challenge.update(
                status=status,
                challenger_score=challenger_score,
                opponent_score=opponent_score,
                winner_id=winner_id,
                winner_balance=winner_balance,
            )
            return challenge

    async def recover_interrupted_motovskikh_matches(self) -> list[dict]:
        """Refund matches whose WebSocket observer was lost on bot restart."""
        async with self.lock:
            rows = self.connection.execute(
                """
                SELECT * FROM motovskikh_challenges WHERE status = 'playing'
                """
            ).fetchall()
            recovered = [dict(row) for row in rows]
            for challenge in recovered:
                if challenge["stake"]:
                    self.connection.execute(
                        """
                        UPDATE balances SET balance = balance + ?
                        WHERE chat_id = ? AND user_id IN (?, ?)
                        """,
                        (
                            challenge["stake"],
                            challenge["chat_id"],
                            challenge["challenger_id"],
                            challenge["opponent_id"],
                        ),
                    )
            self.connection.execute(
                """
                UPDATE motovskikh_challenges
                SET status = 'interrupted', finished_at = ?
                WHERE status = 'playing'
                """,
                (time.time(),),
            )
            self.connection.commit()
            return recovered

    async def get_motovskikh_stats(
        self, chat_id: int, user_id: int
    ) -> dict | None:
        async with self.lock:
            row = self.connection.execute(
                """
                SELECT
                    COUNT(*) AS games,
                    SUM(CASE WHEN winner_id = ? THEN 1 ELSE 0 END) AS wins,
                    SUM(CASE WHEN status = 'tie' THEN 1 ELSE 0 END) AS draws
                FROM motovskikh_challenges
                WHERE chat_id = ? AND status IN ('completed', 'tie')
                  AND (challenger_id = ? OR opponent_id = ?)
                """,
                (user_id, chat_id, user_id, user_id),
            ).fetchone()
            if row is None or not row["games"]:
                return None
            games = int(row["games"])
            wins = int(row["wins"] or 0)
            draws = int(row["draws"] or 0)
            return {
                "games": games,
                "wins": wins,
                "draws": draws,
                "losses": games - wins - draws,
                "win_rate": 100.0 * wins / games,
            }

    async def top_motovskikh_players(
        self, chat_id: int, limit: int = 10
    ) -> list[sqlite3.Row]:
        async with self.lock:
            return self.connection.execute(
                """
                WITH participants AS (
                    SELECT challenger_id AS user_id, challenger_name AS player_name,
                           winner_id, status
                    FROM motovskikh_challenges
                    WHERE chat_id = ? AND status IN ('completed', 'tie')
                    UNION ALL
                    SELECT opponent_id AS user_id, opponent_name AS player_name,
                           winner_id, status
                    FROM motovskikh_challenges
                    WHERE chat_id = ? AND status IN ('completed', 'tie')
                )
                SELECT user_id, MAX(player_name) AS player_name,
                       COUNT(*) AS games,
                       SUM(CASE WHEN winner_id = user_id THEN 1 ELSE 0 END) AS wins,
                       SUM(CASE WHEN status = 'tie' THEN 1 ELSE 0 END) AS draws,
                       100.0 * SUM(CASE WHEN winner_id = user_id THEN 1 ELSE 0 END)
                           / COUNT(*) AS win_rate
                FROM participants
                GROUP BY user_id
                ORDER BY games DESC, win_rate DESC, wins DESC, player_name ASC
                LIMIT ?
                """,
                (chat_id, chat_id, limit),
            ).fetchall()
