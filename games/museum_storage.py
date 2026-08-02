"""SQLite-хранилище золота, статуй и суточного дохода музея."""

from __future__ import annotations

import sqlite3
import time

from . import museum


MUSEUM_INCOME_INTERVAL_SECONDS = 24 * 60 * 60
MAX_MUSEUM_DAILY_INCOME = 300_000


def _create_museum_statues_table(
    connection: sqlite3.Connection,
    table_name: str,
) -> None:
    """Создать таблицу статуй в актуальной суточной схеме."""
    if table_name not in {"museum_statues", "museum_statues_v2"}:
        raise ValueError("Некорректное внутреннее имя таблицы статуй")
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
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
            income_per_day INTEGER NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )


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
    _create_museum_statues_table(connection, "museum_statues")
    statue_columns = {
        row[1]
        for row in connection.execute("PRAGMA table_info(museum_statues)")
    }
    if "income_per_hour" in statue_columns:
        income_expression = (
            "COALESCE(income_per_day, income_per_hour)"
            if "income_per_day" in statue_columns
            else "income_per_hour"
        )
        connection.execute("SAVEPOINT migrate_museum_statues_daily")
        try:
            connection.execute("DROP TABLE IF EXISTS museum_statues_v2")
            _create_museum_statues_table(connection, "museum_statues_v2")
            connection.execute(
                f"""
                INSERT INTO museum_statues_v2(
                    id, chat_id, user_id, size_code, quality, color,
                    gold_spent, base_roll, bonus, score,
                    income_per_day, created_at
                )
                SELECT id, chat_id, user_id, size_code, quality, color,
                       gold_spent, base_roll, bonus, score,
                       {income_expression}, created_at
                FROM museum_statues
                """
            )
            connection.execute("DROP TABLE museum_statues")
            connection.execute(
                "ALTER TABLE museum_statues_v2 RENAME TO museum_statues"
            )
            connection.execute("RELEASE migrate_museum_statues_daily")
        except Exception:
            connection.execute("ROLLBACK TO migrate_museum_statues_daily")
            connection.execute("RELEASE migrate_museum_statues_daily")
            raise
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

    def _museum_daily_income_unlocked(
        self,
        chat_id: int,
        user_id: int,
    ) -> tuple[int, int]:
        raw_income = int(
            self.connection.execute(
                """
                SELECT COALESCE(SUM(income_per_day), 0)
                FROM museum_statues
                WHERE chat_id = ? AND user_id = ? AND score >= 71
                """,
                (chat_id, user_id),
            ).fetchone()[0]
        )
        return raw_income, min(MAX_MUSEUM_DAILY_INCOME, max(0, raw_income))

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
        elapsed_days = int(
            (now - float(account["income_updated_at"]))
            // MUSEUM_INCOME_INTERVAL_SECONDS
        )
        if elapsed_days <= 0:
            return 0
        daily_income = self._museum_daily_income_unlocked(
            chat_id,
            user_id,
        )[1]
        payout = daily_income * elapsed_days
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
                elapsed_days * MUSEUM_INCOME_INTERVAL_SECONDS,
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
        """Потратить золото и добавить статую, если заготовка не сломалась."""
        async with self.lock:
            try:
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
                if roll.is_broken:
                    self.connection.commit()
                    return "broken", roll, available_gold - gold
                self.connection.execute(
                    """
                    INSERT INTO museum_statues(
                        chat_id, user_id, size_code, quality, color,
                        gold_spent, base_roll, bonus, score,
                        income_per_day, created_at
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
                        roll.income_per_day,
                        now,
                    ),
                )
                self.connection.commit()
                return "ok", roll, available_gold - gold
            except Exception:
                self.connection.rollback()
                raise

    async def get_museum(
        self,
        chat_id: int,
        user_id: int,
    ) -> dict:
        """Начислить завершённые сутки и вернуть экспозицию."""
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
                WHERE chat_id = ? AND user_id = ? AND score >= 71
                ORDER BY id
                """,
                (chat_id, user_id),
            ).fetchall()
            raw_income, daily_income = self._museum_daily_income_unlocked(
                chat_id,
                user_id,
            )
            self.connection.commit()
            return {
                "gold": int(account["gold"]),
                "statues": [dict(row) for row in rows],
                "raw_daily_income": raw_income,
                "daily_income": daily_income,
                "accrued_payout": payout,
            }

    async def accrue_all_museum_income(self) -> tuple[int, int]:
        """Начислить завершённые сутки всем владельцам музеев."""
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
