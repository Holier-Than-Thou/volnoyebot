import asyncio
import sqlite3
import time
import unittest

from games.motovskikh_game import (
    determine_outcome,
    parse_challenge_arguments,
    parse_test_url,
    unexpected_online_player_ids,
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
            CREATE TABLE balances (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                balance INTEGER NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
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


class ParsingTests(unittest.TestCase):
    def test_parses_free_match_and_discards_target_mention(self) -> None:
        parsed = parse_challenge_arguments(
            "https://motovskikh.ru/moscow/ @opponent"
        )

        self.assertEqual(parsed.stake, 0)
        self.assertEqual(parsed.test_slug, "moscow")
        self.assertEqual(parsed.test_url, "https://motovskikh.ru/moscow/")

    def test_parses_staked_workshop_match(self) -> None:
        parsed = parse_challenge_arguments(
            "250 https://motovskikh.ru/workshop/example/#old-room"
        )

        self.assertEqual(parsed.stake, 250)
        self.assertEqual(parsed.test_slug, "workshop/example")

    def test_rejects_service_page(self) -> None:
        with self.assertRaisesRegex(ValueError, "именно на тест"):
            parse_test_url("https://motovskikh.ru/tests/lobby/")

    def test_rejects_insecure_or_foreign_url(self) -> None:
        for url in (
            "http://motovskikh.ru/moscow/",
            "https://example.com/moscow/",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                parse_test_url(url)

    def test_rejects_zero_stake(self) -> None:
        with self.assertRaisesRegex(ValueError, "не указывайте"):
            parse_challenge_arguments("0 https://motovskikh.ru/moscow/")


class OutcomeTests(unittest.TestCase):
    def test_uses_server_order_for_winner(self) -> None:
        outcome = determine_outcome(
            [{"i": 22, "s": "95"}, {"i": 11, "s": "80"}],
            challenger_motovskikh_id=11,
            opponent_motovskikh_id=22,
            challenger_telegram_id=101,
            opponent_telegram_id=202,
        )

        self.assertEqual(outcome.winner_id, 202)
        self.assertEqual(outcome.challenger_score, "80")
        self.assertEqual(outcome.opponent_score, "95")

    def test_equal_scores_are_tie(self) -> None:
        outcome = determine_outcome(
            [{"i": 11, "s": 100}, {"i": 22, "s": 100}],
            11,
            22,
            101,
            202,
        )

        self.assertIsNone(outcome.winner_id)

    def test_requires_both_players_in_final_table(self) -> None:
        with self.assertRaises(RuntimeError):
            determine_outcome([{"i": 11, "s": 100}], 11, 22, 101, 202)

    def test_finds_only_online_outsiders_for_kick(self) -> None:
        players = [
            {"i": 99, "o": True},
            {"i": 11, "o": True},
            {"i": 22, "o": True},
            {"i": 33, "o": True},
            {"i": 44, "o": False},
        ]

        self.assertEqual(
            unexpected_online_player_ids(players, 99, {11, 22}),
            [33],
        )


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.store = TestStore()
        await self.store.save_motovskikh_link(101, 11, "First")
        await self.store.save_motovskikh_link(202, 22, "Second")

    async def asyncTearDown(self) -> None:
        self.store.connection.close()

    async def create_challenge(self, proposal_id: int, stake: int = 100) -> str:
        return await self.store.create_motovskikh_challenge(
            chat_id=-1,
            topic_id=7,
            proposal_message_id=proposal_id,
            challenger_id=101,
            challenger_name="First",
            challenger_motovskikh_id=11,
            opponent_id=202,
            opponent_name="Second",
            opponent_motovskikh_id=22,
            stake=stake,
            test_slug="moscow",
            test_url="https://motovskikh.ru/moscow/",
        )

    def balance(self, user_id: int) -> int:
        return int(
            self.store.connection.execute(
                "SELECT balance FROM balances WHERE chat_id = -1 AND user_id = ?",
                (user_id,),
            ).fetchone()["balance"]
        )

    async def test_accepts_reserves_and_pays_winner(self) -> None:
        self.assertEqual(await self.create_challenge(1), "ok")
        status, _challenge = await self.store.accept_motovskikh_challenge(
            -1, 1, 202, time.time() - 60, 1000
        )

        self.assertEqual(status, "ok")
        self.assertEqual(self.balance(101), 900)
        self.assertEqual(self.balance(202), 900)

        result = await self.store.finish_motovskikh_challenge(
            -1, 1, "90", "100", 202
        )

        self.assertEqual(result["winner_balance"], 1100)
        self.assertEqual(self.balance(101), 900)
        self.assertEqual(self.balance(202), 1100)
        stats = await self.store.get_motovskikh_stats(-1, 202)
        self.assertEqual(stats["wins"], 1)
        self.assertEqual(stats["win_rate"], 100.0)
        top = await self.store.top_motovskikh_players(-1)
        self.assertEqual(top[0]["user_id"], 202)
        self.assertEqual(top[0]["games"], 1)

    async def test_tie_refunds_both_stakes(self) -> None:
        await self.create_challenge(2)
        await self.store.accept_motovskikh_challenge(
            -1, 2, 202, time.time() - 60, 1000
        )

        await self.store.finish_motovskikh_challenge(
            -1, 2, "100", "100", None
        )

        self.assertEqual(self.balance(101), 1000)
        self.assertEqual(self.balance(202), 1000)
        stats = await self.store.get_motovskikh_stats(-1, 101)
        self.assertEqual(stats["draws"], 1)

    async def test_refund_is_idempotent(self) -> None:
        await self.create_challenge(3)
        await self.store.accept_motovskikh_challenge(
            -1, 3, 202, time.time() - 60, 1000
        )

        self.assertIsNotNone(await self.store.refund_motovskikh_challenge(-1, 3))
        self.assertIsNone(await self.store.refund_motovskikh_challenge(-1, 3))
        self.assertEqual(self.balance(101), 1000)
        self.assertEqual(self.balance(202), 1000)

    async def test_blocks_overlapping_challenge(self) -> None:
        self.assertEqual(await self.create_challenge(4), "ok")

        self.assertEqual(await self.create_challenge(5), "active")

    async def test_active_challenge_freezes_motovskikh_identity(self) -> None:
        await self.create_challenge(8)

        status = await self.store.save_motovskikh_link(101, 33, "Replacement")

        self.assertEqual(status, "active_wager")
        link = await self.store.get_motovskikh_link(101)
        self.assertEqual(link["motovskikh_player_id"], 11)

    async def test_free_match_never_changes_balances(self) -> None:
        await self.create_challenge(6, stake=0)
        await self.store.accept_motovskikh_challenge(
            -1, 6, 202, time.time() - 60, 1000
        )
        await self.store.finish_motovskikh_challenge(
            -1, 6, "80", "70", 101
        )

        self.assertEqual(self.balance(101), 1000)
        self.assertEqual(self.balance(202), 1000)

    async def test_restart_recovery_refunds_playing_match(self) -> None:
        await self.create_challenge(7)
        await self.store.accept_motovskikh_challenge(
            -1, 7, 202, time.time() - 60, 1000
        )

        recovered = await self.store.recover_interrupted_motovskikh_matches()

        self.assertEqual(len(recovered), 1)
        self.assertEqual(self.balance(101), 1000)
        self.assertEqual(self.balance(202), 1000)
        self.assertEqual(
            self.store.connection.execute(
                "SELECT status FROM motovskikh_challenges "
                "WHERE proposal_message_id = 7"
            ).fetchone()["status"],
            "interrupted",
        )


if __name__ == "__main__":
    unittest.main()
