"""Проверки независимых от Telegram правил слота."""

import unittest
from types import SimpleNamespace

from games import casino


class CasinoRulesTest(unittest.TestCase):
    def test_explicit_bet_above_personal_limit_is_rejected(self) -> None:
        self.assertEqual(
            casino.resolve_bet_amount(10_000, 2_000, 1_000),
            ("limit", 1_000),
        )

    def test_all_in_above_personal_limit_is_rejected(self) -> None:
        self.assertEqual(
            casino.resolve_bet_amount(10_000, None, 1_000),
            ("limit", 1_000),
        )
        self.assertEqual(
            casino.resolve_bet_amount(500, None, 1_000),
            ("ok", 500),
        )

    def test_bet_is_unchanged_without_personal_limit(self) -> None:
        self.assertEqual(
            casino.resolve_bet_amount(10_000, None, None),
            ("ok", 10_000),
        )

    def test_forwarded_messages_are_detected_from_telegram_metadata(self) -> None:
        self.assertTrue(
            casino.is_forwarded_message(
                SimpleNamespace(fwd_from=object(), forward=None)
            )
        )
        self.assertTrue(
            casino.is_forwarded_message(
                SimpleNamespace(fwd_from=None, forward=object())
            )
        )
        self.assertFalse(
            casino.is_forwarded_message(
                SimpleNamespace(fwd_from=None, forward=None)
            )
        )

    def test_decode_slot_boundaries(self) -> None:
        self.assertEqual(casino.decode_slot(1), ("BAR", "BAR", "BAR"))
        self.assertEqual(casino.decode_slot(64), ("7️⃣", "7️⃣", "7️⃣"))

    def test_triple_and_two_sevens_prizes(self) -> None:
        self.assertEqual(casino.get_prize(("7️⃣", "7️⃣", "7️⃣")), (30, "Джекпот"))
        self.assertEqual(
            casino.get_prize(("7️⃣", "🍒", "7️⃣")),
            (1, "Две семёрки — ставка возвращена"),
        )

    def test_theoretical_rtp_is_98_44_percent(self) -> None:
        total_payout = sum(
            casino.get_prize(casino.decode_slot(value))[0]
            for value in range(1, 65)
        )
        self.assertEqual(total_payout, 63)
        self.assertAlmostEqual(total_payout / 64 * 100, 98.4375)

    def test_losing_combination(self) -> None:
        self.assertEqual(casino.get_prize(("BAR", "🍒", "🍋")), (0, ""))

    def test_invalid_slot_value(self) -> None:
        with self.assertRaises(ValueError):
            casino.decode_slot(0)

    def test_topic_id_uses_top_id_for_replies(self) -> None:
        message = SimpleNamespace(
            reply_to=SimpleNamespace(
                forum_topic=True,
                reply_to_top_id=77,
                reply_to_msg_id=91,
            )
        )
        self.assertEqual(casino.message_topic_id(message), 77)

    def test_topic_id_falls_back_to_reply_message_id(self) -> None:
        message = SimpleNamespace(
            reply_to=SimpleNamespace(
                forum_topic=True,
                reply_to_top_id=None,
                reply_to_msg_id=77,
            )
        )
        self.assertEqual(casino.message_topic_id(message), 77)

    def test_general_topic_is_canonical_zero(self) -> None:
        message = SimpleNamespace(
            reply_to=SimpleNamespace(
                forum_topic=True,
                reply_to_top_id=None,
                reply_to_msg_id=1,
            )
        )
        self.assertEqual(casino.message_topic_id(message), 0)

    def test_topic_threading_is_not_an_explicit_reply(self) -> None:
        message = SimpleNamespace(
            reply_to=SimpleNamespace(
                forum_topic=True,
                reply_to_top_id=None,
                reply_to_msg_id=77,
            )
        )
        self.assertFalse(casino.is_explicit_message_reply(message))

    def test_reply_to_user_inside_topic_is_explicit(self) -> None:
        message = SimpleNamespace(
            reply_to=SimpleNamespace(
                forum_topic=True,
                reply_to_top_id=77,
                reply_to_msg_id=91,
            )
        )
        self.assertTrue(casino.is_explicit_message_reply(message))

    def test_reply_in_regular_chat_is_explicit(self) -> None:
        message = SimpleNamespace(
            reply_to=SimpleNamespace(
                forum_topic=False,
                reply_to_top_id=None,
                reply_to_msg_id=91,
            )
        )
        self.assertTrue(casino.is_explicit_message_reply(message))

    def test_rtp_sorting_formula(self) -> None:
        players = [
            {"name": "Средний", "stakes": 200, "payouts": 100},
            {"name": "Высокий", "stakes": 100, "payouts": 200},
            {"name": "Низкий", "stakes": 100, "payouts": 0},
        ]
        ascending = sorted(
            players,
            key=lambda player: player["payouts"] / player["stakes"],
        )
        descending = sorted(
            players,
            key=lambda player: player["payouts"] / player["stakes"],
            reverse=True,
        )
        self.assertEqual(
            [player["name"] for player in ascending],
            ["Низкий", "Средний", "Высокий"],
        )
        self.assertEqual(
            [player["name"] for player in descending],
            ["Высокий", "Средний", "Низкий"],
        )

    def test_leaderboard_includes_every_row_that_fits(self) -> None:
        message = casino.fit_telegram_message(
            "Топ",
            ["1. Первый", "2. Второй"],
        )
        self.assertEqual(message, "Топ\n1. Первый\n2. Второй")

    def test_leaderboard_stays_within_limit_and_reports_omitted_rows(
        self,
    ) -> None:
        message = casino.fit_telegram_message(
            "Топ",
            [f"{index}. Игрок" for index in range(1, 101)],
            max_length=80,
        )
        self.assertLessEqual(len(message), 80)
        self.assertIn("…ещё игроков:", message)
        self.assertNotIn("100. Игрок", message)


if __name__ == "__main__":
    unittest.main()
