"""Проверки правил и статистики игры «Угадай звук»."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from telethon.tl import types

from games.guess_sound.game import _topic_id
from games.guess_sound.freesound import _search_filter
from games.guess_sound.models import GuessOption
from games.guess_sound.rules import (
    normalize_guess,
    selected_poll_option_indexes,
    update_poll_vote,
    voters_grouped_by_option,
    winning_users,
)
from games.guess_sound.storage import GuessSoundStore


class GuessRulesTest(unittest.TestCase):
    def test_bst_category_is_added_to_freesound_filter(self) -> None:
        self.assertIn('category:"Sound effects"', _search_filter("fx-a"))
        self.assertIn('subcategory:"Animals"', _search_filter("fx-a"))
        self.assertNotIn("subcategory:", _search_filter(None))

    def test_forum_topic_id_is_preserved(self) -> None:
        message = SimpleNamespace(
            reply_to=SimpleNamespace(
                forum_topic=True,
                reply_to_top_id=77,
                reply_to_msg_id=91,
            )
        )
        self.assertEqual(_topic_id(message), 77)

    def test_regular_reply_is_not_treated_as_topic(self) -> None:
        message = SimpleNamespace(
            reply_to=SimpleNamespace(
                forum_topic=False,
                reply_to_top_id=None,
                reply_to_msg_id=91,
            )
        )
        self.assertIsNone(_topic_id(message))

    def test_normalization_combines_case_spaces_and_punctuation(self) -> None:
        self.assertEqual(
            normalize_guess("  Дизельный   двигатель?! "),
            normalize_guess("дизельный двигатель"),
        )

    def test_authors_votes_do_not_count(self) -> None:
        option = GuessOption(
            text="Трактор",
            author_ids={10, 20},
            author_names={10: "А", 20: "Б"},
        )
        self.assertEqual(
            winning_users([option], {0: {10, 20}}),
            set(),
        )
        self.assertEqual(
            winning_users([option], {0: {10, 30}}),
            {10, 20},
        )

    def test_multiple_poll_choices_and_vote_changes(self) -> None:
        votes_by_user: dict[int, set[int]] = {}
        update_poll_vote(votes_by_user, 10, {0, 2})
        update_poll_vote(votes_by_user, 20, {1, 2})
        self.assertEqual(
            voters_grouped_by_option(votes_by_user),
            {0: {10}, 1: {20}, 2: {10, 20}},
        )

        update_poll_vote(votes_by_user, 10, {1})
        self.assertEqual(
            voters_grouped_by_option(votes_by_user),
            {1: {10, 20}, 2: {20}},
        )

    def test_retracted_poll_vote_is_removed(self) -> None:
        votes_by_user = {10: {0, 1}}
        update_poll_vote(votes_by_user, 10, set())
        self.assertEqual(votes_by_user, {})

    def test_poll_positions_are_used_for_multiple_choice(self) -> None:
        self.assertEqual(
            selected_poll_option_indexes(
                positions=[0, 2],
                options=[b"unexpected"],
                option_indexes={b"\x00": 0, b"\x02": 2},
            ),
            {0, 2},
        )

    def test_poll_options_are_fallback_for_older_updates(self) -> None:
        self.assertEqual(
            selected_poll_option_indexes(
                positions=None,
                options=[b"\x00", b"\x02"],
                option_indexes={b"\x00": 0, b"\x02": 2},
            ),
            {0, 2},
        )

    def test_poll_shape_is_supported_by_installed_telethon(self) -> None:
        answer = types.PollAnswer(
            text=types.TextWithEntities("Вариант", []),
            option=b"\x00",
        )
        poll = types.Poll(
            id=1,
            question=types.TextWithEntities("Вопрос", []),
            answers=[answer, answer],
            hash=0,
            public_voters=True,
            multiple_choice=True,
            close_period=60,
        )
        self.assertTrue(types.InputMediaPoll(poll=poll)._bytes())


class GuessStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = GuessSoundStore(
            Path(self.temporary_directory.name) / "test.sqlite3"
        )

    async def asyncTearDown(self) -> None:
        await asyncio.sleep(0)
        self.store.connection.close()
        self.temporary_directory.cleanup()

    async def test_top_sorts_equal_wins_by_win_rate(self) -> None:
        await self.store.record_round(1, {1: "Частый", 2: "Точный"}, {1, 2})
        await self.store.record_round(1, {1: "Частый"}, set())
        rows = await self.store.top(1)
        self.assertEqual([row["display_name"] for row in rows], ["Точный", "Частый"])

    async def test_used_sounds_are_scoped_by_chat(self) -> None:
        await self.store.mark_used(1, "freesound", "42")
        self.assertEqual(await self.store.used_ids(1, "freesound"), {"42"})
        self.assertEqual(await self.store.used_ids(2, "freesound"), set())
