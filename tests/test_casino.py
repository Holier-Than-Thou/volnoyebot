"""Проверки независимых от Telegram правил слота."""

import unittest
from types import SimpleNamespace

from games import casino


class CasinoRulesTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
