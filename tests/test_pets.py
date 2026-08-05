"""Проверки правил и SQLite-операций фермы."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from games import farm, pets
from games.farm_storage import FarmStoreMixin, initialize_farm_schema


class PetRulesTest(unittest.TestCase):
    def test_exactly_two_cherries_earn_egg(self) -> None:
        self.assertTrue(farm.earns_pet_egg(("🍒", "BAR", "🍒")))
        self.assertFalse(farm.earns_pet_egg(("🍒", "🍒", "🍒")))
        self.assertFalse(farm.earns_pet_egg(("🍒", "BAR", "7️⃣")))

    def test_breed_rejects_hybrid(self) -> None:
        parent = pets.Pet(0, "A", 20, 0, 0, 0, False, 0, 0)
        hybrid = pets.Pet(1, "B", 20, 20, 0, 1, False, 0, 0)
        with self.assertRaisesRegex(ValueError, "Гибриды стерильны"):
            pets.breed(parent, hybrid, 0)

    @patch("games.pets.random.gauss", return_value=0)
    @patch("games.pets.roll_hatch_seconds", return_value=600)
    def test_breed_uses_average_and_marks_different_specs_hybrid(
        self, _hatch, _gauss
    ) -> None:
        first = pets.Pet(0, "A", 21, 0, 0, 0, False, 0, 0)
        second = pets.Pet(1, "B", 0, 21, 0, 0, False, 0, 0)
        child = pets.breed(first, second, 0, now=100)
        self.assertEqual(
            (child.stench, child.ugliness, child.stickiness),
            (21, 21, 0),
        )
        self.assertEqual(child.generation, 1)
        self.assertTrue(child.is_egg)
        self.assertEqual(child.egg_hatch_at, 700)

    @patch("games.pets.random.gauss", return_value=1.5)
    def test_pure_breeding_keeps_zero_genes_and_has_progress(
        self, _gauss
    ) -> None:
        first = pets.Pet(0, "A", 20, 0, 0, 0, False, 0, 0)
        second = pets.Pet(1, "B", 10, 0, 0, 0, False, 0, 0)
        child = pets.breed(first, second, 0, now=100)
        self.assertEqual(
            (child.stench, child.ugliness, child.stickiness),
            (16, 0, 0),
        )
        self.assertEqual(child.generation, 0)

    def test_multiple_genes_receive_income_synergy(self) -> None:
        pure = pets.Pet(0, "A", 20, 0, 0, 0, False, 0, 0)
        double = pets.Pet(0, "B", 20, 20, 0, 1, False, 0, 0)
        triple = pets.Pet(0, "C", 20, 20, 20, 1, False, 0, 0)
        self.assertAlmostEqual(pure.income_per_second(), 0.12)
        self.assertAlmostEqual(double.income_per_second(), 0.1944)
        self.assertAlmostEqual(triple.income_per_second(), 0.31104)

    @patch("games.pets.random.randint", return_value=5)
    def test_hatch_time_uses_inclusive_minute_range(self, _randint) -> None:
        self.assertEqual(pets.roll_hatch_seconds(), 300)
        _randint.assert_called_once_with(5, 60)

    def test_migration_sums_pure_genes_into_player_spec(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE player_specs (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                spec TEXT NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
        connection.execute(
            "INSERT INTO player_specs VALUES (1, 10, 'stench')"
        )
        connection.execute(
            """
            CREATE TABLE pets (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                slot_index INTEGER NOT NULL CHECK (slot_index BETWEEN 0 AND 3),
                name TEXT NOT NULL,
                stench INTEGER NOT NULL CHECK (stench BETWEEN 1 AND 100),
                ugliness INTEGER NOT NULL CHECK (ugliness BETWEEN 1 AND 100),
                stickiness INTEGER NOT NULL CHECK (stickiness BETWEEN 1 AND 100),
                generation INTEGER NOT NULL,
                is_egg INTEGER NOT NULL,
                egg_hatch_at REAL NOT NULL,
                created_at REAL NOT NULL,
                PRIMARY KEY (chat_id, user_id, slot_index)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO pets VALUES
                (1, 10, 0, 'Чистый', 9, 2, 10, 0, 0, 0, 1),
                (1, 10, 1, 'Гибрид', 5, 7, 1, 1, 0, 0, 1)
            """
        )
        initialize_farm_schema(connection)
        rows = connection.execute(
            "SELECT * FROM pets ORDER BY slot_index"
        ).fetchall()
        self.assertEqual(
            (rows[0]["stench"], rows[0]["ugliness"], rows[0]["stickiness"]),
            (21, 0, 0),
        )
        self.assertEqual(
            (rows[1]["stench"], rows[1]["ugliness"], rows[1]["stickiness"]),
            (5, 7, 1),
        )
        connection.execute(
            """
            INSERT INTO pets VALUES
                (1, 10, 5, '', 1, 0, 0, 0, 1, 100, 1)
            """
        )
        connection.close()


class TestFarmStore(FarmStoreMixin):
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.lock = asyncio.Lock()
        self.connection.execute(
            """
            CREATE TABLE balances (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                balance INTEGER NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
        initialize_farm_schema(self.connection)
        self.connection.commit()


class FarmStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = TestFarmStore(Path(self.directory.name) / "farm.sqlite3")

    async def asyncTearDown(self) -> None:
        self.store.connection.close()
        self.directory.cleanup()

    @patch("games.pets.roll_spec", return_value="stench")
    @patch("games.pets.roll_egg_value", return_value=10)
    @patch("games.pets.random_pet_name", return_value="Бугор")
    async def test_egg_hatches_automatically_and_claims_income(
        self, _name, _egg_value, _spec
    ) -> None:
        status, spec, egg = await self.store.award_pet_egg(1, 10)
        self.assertEqual((status, spec, egg.slot_index), ("ok", "stench", 0))
        now = time.time()
        self.store.connection.execute(
            """
            UPDATE pets SET egg_hatch_at = ?
            WHERE chat_id = 1 AND user_id = 10 AND slot_index = 0
            """,
            (now - 10,),
        )
        self.store.connection.execute(
            """
            UPDATE pet_income SET updated_at = ?
            WHERE chat_id = 1 AND user_id = 10
            """,
            (now - 20,),
        )
        self.store.connection.commit()

        hatched = await self.store.hatch_due_pet_eggs()
        self.assertEqual(hatched, 1)
        snapshot = await self.store.get_farm(1, 10)
        self.assertFalse(snapshot["pets"][0].is_egg)
        self.assertEqual(snapshot["pets"][0].name, "Бугор")
        self.assertEqual(
            (
                snapshot["pets"][0].stench,
                snapshot["pets"][0].ugliness,
                snapshot["pets"][0].stickiness,
            ),
            (10, 0, 0),
        )
        amount, balance = await asyncio.wait_for(
            self.store.claim_pet_income(1, 10, "Игрок", 1000),
            timeout=1,
        )
        self.assertEqual(amount, 1)
        self.assertEqual(balance, 1000 + amount)
        remainder = self.store.connection.execute(
            """
            SELECT accumulated FROM pet_income
            WHERE chat_id = 1 AND user_id = 10
            """
        ).fetchone()["accumulated"]
        self.assertGreaterEqual(remainder, 0)
        self.assertLess(remainder, 1)

    async def test_six_slots_limit(self) -> None:
        for expected_slot in range(6):
            status, _spec, egg = await self.store.award_pet_egg(1, 10)
            self.assertEqual(status, "ok")
            self.assertEqual(egg.slot_index, expected_slot)
        status, _spec, egg = await self.store.award_pet_egg(1, 10)
        self.assertEqual(status, "full")
        self.assertIsNone(egg)

    async def test_shelter_removes_pet_after_accruing_income(self) -> None:
        now = time.time()
        pet = pets.Pet(0, "Лишний", 10, 0, 0, 0, False, 0, now)
        self.store._insert_pet_unlocked(1, 10, pet)
        await self.store.get_farm(1, 10)
        self.store.connection.execute(
            """
            UPDATE pet_income SET updated_at = ?
            WHERE chat_id = 1 AND user_id = 10
            """,
            (time.time() - 10,),
        )
        self.store.connection.commit()

        self.assertEqual(await self.store.shelter_pet(1, 10, 0), "ok")
        self.assertEqual(await self.store.shelter_pet(1, 10, 0), "missing")
        snapshot = await self.store.get_farm(1, 10)
        self.assertEqual(snapshot["pets"], [])
        self.assertGreaterEqual(snapshot["accumulated"], 1)

    @patch("games.pets.random.gauss", return_value=0)
    async def test_breeding_replaces_parents_atomically(self, _gauss) -> None:
        now = time.time()
        first = pets.Pet(0, "A", 20, 0, 0, 0, False, 0, now)
        second = pets.Pet(1, "B", 10, 0, 0, 0, False, 0, now)
        self.store._insert_pet_unlocked(1, 10, first)
        self.store._insert_pet_unlocked(1, 10, second)
        self.store.connection.commit()

        status, child = await self.store.breed_pets(1, 10, 0, 1)
        self.assertEqual(status, "ok")
        self.assertEqual(child.slot_index, 0)
        rows = self.store.connection.execute(
            """
            SELECT * FROM pets
            WHERE chat_id = 1 AND user_id = 10
            """
        ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["is_egg"])

    async def test_transfer_requires_free_slot_and_moves_pet(self) -> None:
        now = time.time()
        pet = pets.Pet(0, "Подарок", 20, 0, 0, 0, False, 0, now)
        self.store._insert_pet_unlocked(1, 10, pet)
        self.store.connection.commit()
        status = await self.store.create_pet_transfer(
            1, 500, 10, "Первый", 20, "Второй", 0
        )
        self.assertEqual(status, "ok")
        self.assertEqual(await self.store.shelter_pet(1, 10, 0), "reserved")

        status, transfer = await self.store.accept_pet_transfer(
            1, 500, 20, expires_before=0
        )
        self.assertEqual(status, "ok")
        self.assertEqual(transfer["recipient_slot"], 0)
        sender_pets = await self.store.get_farm(1, 10)
        recipient_pets = await self.store.get_farm(1, 20)
        self.assertEqual(sender_pets["pets"], [])
        self.assertEqual(recipient_pets["pets"][0].name, "Подарок")


if __name__ == "__main__":
    unittest.main()
