import asyncio
import sqlite3
import unittest

from games.motovskikh_link import (
    Candidate,
    select_online_candidates,
)
from games.motovskikh_storage import (
    MotovskikhStoreMixin,
    initialize_motovskikh_schema,
)


class TestStore(MotovskikhStoreMixin):
    def __init__(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.lock = asyncio.Lock()
        self.connection.execute(
            """
            CREATE TABLE dice_challenges (
                challenger_id INTEGER NOT NULL,
                opponent_id INTEGER NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        initialize_motovskikh_schema(self.connection)


class CandidateSelectionTests(unittest.TestCase):
    def test_ignores_observer_and_offline_players(self) -> None:
        room = {
            "s": [
                {"i": 1081, "n": "Bot", "o": True},
                {"i": 2001, "n": "Player", "o": True},
                {"i": 2002, "n": "Offline", "o": False},
            ]
        }

        self.assertEqual(
            select_online_candidates(room, 1081),
            [Candidate(2001, "Player")],
        )

    def test_deduplicates_player_id(self) -> None:
        room = {
            "s": [
                {"i": 2001, "n": "Old", "o": True},
                {"i": 2001, "n": "New", "o": True},
            ]
        }

        self.assertEqual(
            select_online_candidates(room, 1081),
            [Candidate(2001, "New")],
        )


class MotovskikhStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = TestStore()

    async def asyncTearDown(self) -> None:
        self.store.connection.close()

    async def test_saves_one_to_one_link(self) -> None:
        result = await self.store.save_motovskikh_link(10, 100, "Player")

        self.assertEqual(result, "linked")
        row = await self.store.get_motovskikh_link(10)
        self.assertEqual(row["motovskikh_player_id"], 100)

    async def test_rejects_player_linked_to_another_telegram_user(self) -> None:
        await self.store.save_motovskikh_link(10, 100, "Player")

        result = await self.store.save_motovskikh_link(20, 100, "Player")

        self.assertEqual(result, "conflict")
        self.assertIsNone(await self.store.get_motovskikh_link(20))

    async def test_replacement_keeps_old_link_during_active_wager(self) -> None:
        await self.store.save_motovskikh_link(10, 100, "Old")
        self.store.connection.execute(
            "INSERT INTO dice_challenges VALUES (?, ?, ?)",
            (10, 20, "playing"),
        )
        self.store.connection.commit()

        result = await self.store.save_motovskikh_link(10, 200, "New")

        self.assertEqual(result, "active_wager")
        row = await self.store.get_motovskikh_link(10)
        self.assertEqual(row["motovskikh_player_id"], 100)

    async def test_replaces_link_after_wager_finishes(self) -> None:
        await self.store.save_motovskikh_link(10, 100, "Old")
        self.store.connection.execute(
            "INSERT INTO dice_challenges VALUES (?, ?, ?)",
            (10, 20, "completed"),
        )
        self.store.connection.commit()

        result = await self.store.save_motovskikh_link(10, 200, "New")

        self.assertEqual(result, "linked")
        row = await self.store.get_motovskikh_link(10)
        self.assertEqual(row["motovskikh_player_id"], 200)


if __name__ == "__main__":
    unittest.main()
