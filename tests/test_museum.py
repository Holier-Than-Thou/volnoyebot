import asyncio
import random
import sqlite3
import time
import unittest

from games import museum
from games.museum_storage import (
    MUSEUM_INCOME_INTERVAL_SECONDS,
    MuseumStoreMixin,
    initialize_museum_schema,
)


class MuseumRulesTests(unittest.TestCase):
    def test_help_contains_core_rules_and_example(self) -> None:
        text = museum.help_text()
        self.assertIn("ровно две 7️⃣", text)
        self.assertIn("Ужасное: ×−2", text)
        self.assertIn("9 000 × 2 = 18 000 очков/ч", text)
        self.assertIn("музей создать 10з Б", text)

    def test_all_in_gold_except_exactly_two_sevens(self) -> None:
        self.assertEqual(
            museum.all_in_gold_reward(250_000, ("BAR", "🍒", "🍋")),
            2,
        )
        self.assertEqual(
            museum.all_in_gold_reward(250_000, ("7️⃣", "🍒", "7️⃣")),
            0,
        )
        self.assertEqual(
            museum.all_in_gold_reward(250_000, ("7️⃣", "7️⃣", "7️⃣")),
            2,
        )

    def test_integer_bonus_for_great_statue(self) -> None:
        roll = museum.create_statue_roll("В", 10, random.Random(1))
        self.assertEqual(roll.bonus, 4)
        self.assertIsInstance(roll.score, int)

    def test_quality_thresholds(self) -> None:
        self.assertEqual(museum.quality_for_score(40)[0], "Ужасное")
        self.assertEqual(museum.quality_for_score(41)[0], "Плохое")
        self.assertEqual(museum.quality_for_score(100)[0], "Шедевр")

    def test_both_create_argument_orders(self) -> None:
        self.assertEqual(
            museum.parse_create_arguments(["10", "золота", "Б"]),
            (10, "Б"),
        )
        self.assertEqual(
            museum.parse_create_arguments(["в", "10", "золото"]),
            (10, "В"),
        )

    def test_short_gold_forms(self) -> None:
        self.assertEqual(
            museum.parse_create_arguments(["10з", "Б"]),
            (10, "Б"),
        )
        self.assertEqual(
            museum.parse_create_arguments(["Г", "10", "з"]),
            (10, "Г"),
        )


class TestStore(MuseumStoreMixin):
    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.lock = asyncio.Lock()
        self.connection.execute(
            """
            CREATE TABLE balances (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                balance INTEGER NOT NULL,
                PRIMARY KEY(chat_id, user_id)
            )
            """
        )
        self.connection.execute("INSERT INTO balances VALUES (1, 10, 1000)")
        initialize_museum_schema(self.connection)
        self.connection.commit()


class MuseumStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = TestStore()

    async def asyncTearDown(self) -> None:
        self.store.connection.close()

    async def test_gold_is_spent_when_statue_is_created(self) -> None:
        self.assertEqual(await self.store.award_museum_gold(1, 10, 7), 7)
        status, roll, gold_left = await self.store.create_museum_statue(
            1, 10, 5, "Б"
        )
        self.assertEqual(status, "ok")
        self.assertIsNotNone(roll)
        self.assertEqual(gold_left, 2)

    async def test_completed_hour_is_added_to_balance(self) -> None:
        await self.store.award_museum_gold(1, 10, 100)
        status, _roll, _gold = await self.store.create_museum_statue(
            1, 10, 100, "Б"
        )
        self.assertEqual(status, "ok")
        self.store.connection.execute(
            """
            UPDATE museum_accounts SET income_updated_at = ?
            WHERE chat_id = 1 AND user_id = 10
            """,
            (time.time() - MUSEUM_INCOME_INTERVAL_SECONDS - 1,),
        )
        snapshot = await self.store.get_museum(1, 10)
        balance = self.store.connection.execute(
            "SELECT balance FROM balances WHERE chat_id = 1 AND user_id = 10"
        ).fetchone()["balance"]
        self.assertEqual(
            balance,
            1000 + snapshot["hourly_income"],
        )

    async def test_negative_museum_pays_zero(self) -> None:
        now = time.time()
        self.store._ensure_museum_account_unlocked(1, 10, now)
        self.store.connection.execute(
            """
            INSERT INTO museum_statues(
                chat_id, user_id, size_code, quality, color,
                gold_spent, base_roll, bonus, score,
                income_per_hour, created_at
            )
            VALUES (1, 10, 'Б', 'Ужасное', '🟥', 1, 1, 0, 1, -18000, ?)
            """,
            (now,),
        )
        self.store.connection.execute(
            """
            UPDATE museum_accounts SET income_updated_at = ?
            WHERE chat_id = 1 AND user_id = 10
            """,
            (now - MUSEUM_INCOME_INTERVAL_SECONDS - 1,),
        )
        self.store.connection.commit()
        snapshot = await self.store.get_museum(1, 10)
        self.assertEqual(snapshot["raw_hourly_income"], -18_000)
        self.assertEqual(snapshot["hourly_income"], 0)
        balance = self.store.connection.execute(
            "SELECT balance FROM balances WHERE chat_id = 1 AND user_id = 10"
        ).fetchone()["balance"]
        self.assertEqual(balance, 1000)


if __name__ == "__main__":
    unittest.main()
