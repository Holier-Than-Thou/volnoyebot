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
        parent = pets.Pet(0, "A", 20, 1, 1, 0, False, 0, 0)
        hybrid = pets.Pet(1, "B", 1, 20, 1, 1, False, 0, 0)
        with self.assertRaisesRegex(ValueError, "Гибриды стерильны"):
            pets.breed(parent, hybrid, 0)

    @patch("games.pets.random.gauss", return_value=0)
    def test_breed_uses_average_and_marks_different_specs_hybrid(
        self, _gauss
    ) -> None:
        first = pets.Pet(0, "A", 21, 1, 1, 0, False, 0, 0)
        second = pets.Pet(1, "B", 1, 21, 1, 0, False, 0, 0)
        child = pets.breed(first, second, 0, now=100)
        self.assertEqual((child.stench, child.ugliness, child.stickiness), (11, 11, 1))
        self.assertEqual(child.generation, 1)
        self.assertTrue(child.is_egg)
        self.assertEqual(child.egg_hatch_at, 100 + pets.EGG_HATCH_SECONDS)


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
    async def test_egg_hatches_automatically_and_claims_income(
        self, _egg_value, _spec
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

    async def test_four_slots_limit(self) -> None:
        for expected_slot in range(4):
            status, _spec, egg = await self.store.award_pet_egg(1, 10)
            self.assertEqual(status, "ok")
            self.assertEqual(egg.slot_index, expected_slot)
        status, _spec, egg = await self.store.award_pet_egg(1, 10)
        self.assertEqual(status, "full")
        self.assertIsNone(egg)

    async def test_shelter_removes_pet_after_accruing_income(self) -> None:
        now = time.time()
        pet = pets.Pet(0, "Лишний", 10, 1, 1, 0, False, 0, now)
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

        self.assertTrue(await self.store.shelter_pet(1, 10, 0))
        self.assertFalse(await self.store.shelter_pet(1, 10, 0))
        snapshot = await self.store.get_farm(1, 10)
        self.assertEqual(snapshot["pets"], [])
        self.assertGreaterEqual(snapshot["accumulated"], 1)

    @patch("games.pets.random.gauss", return_value=0)
    async def test_breeding_replaces_parents_atomically(self, _gauss) -> None:
        now = time.time()
        first = pets.Pet(0, "A", 20, 1, 1, 0, False, 0, now)
        second = pets.Pet(1, "B", 10, 1, 1, 0, False, 0, now)
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
