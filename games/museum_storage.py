"""SQLite-хранилище золота, статуй и почасового дохода музея."""

from __future__ import annotations

import sqlite3
import time

from . import museum


MUSEUM_INCOME_INTERVAL_SECONDS = 60 * 60


def initialize_museum_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS museum_accounts (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            gold INTEGER NOT NULL DEFAULT 0 CHECK (gold >= 0),
            income_updated_at REAL NOT NULL,
            PRIMARY KEY (chat_id, user_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS museum_statues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            size_code TEXT NOT NULL CHECK (size_code IN ('Б', 'Г', 'В')),
            quality TEXT NOT NULL,
            color TEXT NOT NULL,
            gold_spent INTEGER NOT NULL CHECK (gold_spent > 0),
            base_roll INTEGER NOT NULL,
            bonus INTEGER NOT NULL,
            score INTEGER NOT NULL,
            income_per_hour INTEGER NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_museum_statues_owner
        ON museum_statues(chat_id, user_id, id)
        """
    )


class MuseumStoreMixin:
    """Методы музея, использующие общую блокировку BalanceStore."""

    def _ensure_museum_account_unlocked(
        self,
        chat_id: int,
        user_id: int,
        now: float,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO museum_accounts(
                chat_id, user_id, gold, income_updated_at
            )
            VALUES (?, ?, 0, ?)
            ON CONFLICT(chat_id, user_id) DO NOTHING
            """,
            (chat_id, user_id, now),
        )

    def _museum_hourly_income_unlocked(
        self,
        chat_id: int,
        user_id: int,
    ) -> tuple[int, int]:
        raw_income = int(
            self.connection.execute(
                """
                SELECT COALESCE(SUM(income_per_hour), 0)
                FROM museum_statues
                WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, user_id),
            ).fetchone()[0]
        )
        return raw_income, max(0, raw_income)

    def _accrue_museum_unlocked(
        self,
        chat_id: int,
        user_id: int,
        now: float,
    ) -> int:
        self._ensure_museum_account_unlocked(chat_id, user_id, now)
        account = self.connection.execute(
            """
            SELECT income_updated_at
            FROM museum_accounts
            WHERE chat_id = ? AND user_id = ?
            """,
            (chat_id, user_id),
        ).fetchone()
        elapsed_hours = int(
            (now - float(account["income_updated_at"]))
            // MUSEUM_INCOME_INTERVAL_SECONDS
        )
        if elapsed_hours <= 0:
            return 0
        _raw_income, hourly_income = self._museum_hourly_income_unlocked(
            chat_id,
            user_id,
        )
        payout = hourly_income * elapsed_hours
        if payout:
            self.connection.execute(
                """
                UPDATE balances
                SET balance = balance + ?
                WHERE chat_id = ? AND user_id = ?
                """,
                (payout, chat_id, user_id),
            )
        self.connection.execute(
            """
            UPDATE museum_accounts
            SET income_updated_at = income_updated_at + ?
            WHERE chat_id = ? AND user_id = ?
            """,
            (
                elapsed_hours * MUSEUM_INCOME_INTERVAL_SECONDS,
                chat_id,
                user_id,
            ),
        )
        return payout

    async def award_museum_gold(
        self,
        chat_id: int,
        user_id: int,
        amount: int,
    ) -> int:
        """Начислить золото и вернуть новый запас."""
        if amount <= 0:
            return await self.get_museum_gold(chat_id, user_id)
        async with self.lock:
            now = time.time()
            self._ensure_museum_account_unlocked(chat_id, user_id, now)
            self.connection.execute(
                """
                UPDATE museum_accounts SET gold = gold + ?
                WHERE chat_id = ? AND user_id = ?
                """,
                (amount, chat_id, user_id),
            )
            self.connection.commit()
            return int(
                self.connection.execute(
                    """
                    SELECT gold FROM museum_accounts
                    WHERE chat_id = ? AND user_id = ?
                    """,
                    (chat_id, user_id),
                ).fetchone()["gold"]
            )

    async def get_museum_gold(self, chat_id: int, user_id: int) -> int:
        async with self.lock:
            self._ensure_museum_account_unlocked(
                chat_id,
                user_id,
                time.time(),
            )
            self.connection.commit()
            return int(
                self.connection.execute(
                    """
                    SELECT gold FROM museum_accounts
                    WHERE chat_id = ? AND user_id = ?
                    """,
                    (chat_id, user_id),
                ).fetchone()["gold"]
            )

    async def create_museum_statue(
        self,
        chat_id: int,
        user_id: int,
        gold: int,
        size_code: str,
    ) -> tuple[str, museum.StatueRoll | None, int]:
        """Потратить золото и навсегда добавить статую."""
        async with self.lock:
            now = time.time()
            self._accrue_museum_unlocked(chat_id, user_id, now)
            account = self.connection.execute(
                """
                SELECT gold FROM museum_accounts
                WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, user_id),
            ).fetchone()
            available_gold = int(account["gold"])
            if gold < 1:
                self.connection.commit()
                return "invalid", None, available_gold
            if available_gold < gold:
                self.connection.commit()
                return "insufficient", None, available_gold
            roll = museum.create_statue_roll(size_code, gold)
            self.connection.execute(
                """
                UPDATE museum_accounts SET gold = gold - ?
                WHERE chat_id = ? AND user_id = ?
                """,
                (gold, chat_id, user_id),
            )
            self.connection.execute(
                """
                INSERT INTO museum_statues(
                    chat_id, user_id, size_code, quality, color,
                    gold_spent, base_roll, bonus, score,
                    income_per_hour, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    user_id,
                    roll.size.code,
                    roll.quality,
                    roll.color,
                    roll.gold_spent,
                    roll.base_roll,
                    roll.bonus,
                    roll.score,
                    roll.income_per_hour,
                    now,
                ),
            )
            self.connection.commit()
            return "ok", roll, available_gold - gold

    async def get_museum(
        self,
        chat_id: int,
        user_id: int,
    ) -> dict:
        """Начислить завершённые часы и вернуть экспозицию."""
        async with self.lock:
            now = time.time()
            payout = self._accrue_museum_unlocked(chat_id, user_id, now)
            account = self.connection.execute(
                """
                SELECT gold FROM museum_accounts
                WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, user_id),
            ).fetchone()
            rows = self.connection.execute(
                """
                SELECT * FROM museum_statues
                WHERE chat_id = ? AND user_id = ?
                ORDER BY id
                """,
                (chat_id, user_id),
            ).fetchall()
            raw_income, hourly_income = self._museum_hourly_income_unlocked(
                chat_id,
                user_id,
            )
            self.connection.commit()
            return {
                "gold": int(account["gold"]),
                "statues": [dict(row) for row in rows],
                "raw_hourly_income": raw_income,
                "hourly_income": hourly_income,
                "accrued_payout": payout,
            }

    async def accrue_all_museum_income(self) -> tuple[int, int]:
        """Начислить завершённые часы всем владельцам музеев."""
        async with self.lock:
            now = time.time()
            accounts = self.connection.execute(
                "SELECT chat_id, user_id FROM museum_accounts"
            ).fetchall()
            paid_accounts = 0
            total = 0
            for account in accounts:
                payout = self._accrue_museum_unlocked(
                    int(account["chat_id"]),
                    int(account["user_id"]),
                    now,
                )
                if payout:
                    paid_accounts += 1
                    total += payout
            self.connection.commit()
            return paid_accounts, total
