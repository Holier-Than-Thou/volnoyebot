import asyncio
import sqlite3
import time
import unittest

from telethon.tl.custom import Message
from telethon.tl.types import PeerUser, User

from games import emulation
from games.emulation_storage import (
    EmulationStoreMixin,
    initialize_emulation_schema,
)
from games.motovskikh_link import (
    Candidate,
    LinkAttempt,
    MotovskikhLinkManager,
    select_online_candidates,
)
from games.motovskikh_storage import (
    MotovskikhStoreMixin,
    initialize_motovskikh_schema,
)


ADMIN_ID = 777
GROUP_ID = -100500


class TestStore(EmulationStoreMixin, MotovskikhStoreMixin):
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
        initialize_emulation_schema(self.connection)
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


class FakeClient:
    """Клиент, записывающий адресатов исходящих сообщений."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **_kwargs) -> None:
        self.sent.append((chat_id, text))


class PrivateEvent:
    """Личное сообщение администратора, возможно от лица персоны."""

    def __init__(self, text: str, actor: User | None = None) -> None:
        self.message = Message(
            id=1, peer_id=PeerUser(ADMIN_ID), message=text
        )
        self.chat_id = ADMIN_ID
        self.is_private = True
        self.replies: list[str] = []
        if actor is not None:
            emulation.set_actor(self.message, actor)

    @property
    def raw_text(self) -> str:
        return self.message.message

    async def get_sender(self) -> User:
        return User(id=ADMIN_ID, bot=False, first_name="Админ")

    async def reply(self, text: str, **_kwargs) -> None:
        self.replies.append(text)


class LinkRoutingTests(unittest.IsolatedAsyncioTestCase):
    """Привязка от лица персоны идёт в личный чат администратора."""

    async def asyncSetUp(self) -> None:
        self.store = TestStore()
        self.client = FakeClient()
        self.manager = MotovskikhLinkManager(
            self.client, self.store, cookie_path=None, track_task=lambda _: None
        )
        self.started: list[tuple[int, int]] = []

        async def record_attempt(user_id: int, chat_id: int) -> None:
            self.started.append((user_id, chat_id))

        self.manager.start_attempt = record_attempt
        _, self.persona = await self.store.create_emulated_player(
            GROUP_ID, "Вася"
        )
        self.actor = emulation.emulated_user(
            self.persona["user_id"], self.persona["name"]
        )

    async def asyncTearDown(self) -> None:
        self.store.connection.close()

    async def test_attempt_uses_persona_identity_and_admin_chat(self) -> None:
        event = PrivateEvent("/motovskikh_auth", actor=self.actor)

        await self.manager.handle_command(event)

        self.assertEqual(
            self.started, [(self.persona["user_id"], ADMIN_ID)]
        )

    async def test_real_user_still_links_to_own_chat(self) -> None:
        event = PrivateEvent("/motovskikh_auth")

        await self.manager.handle_command(event)

        self.assertEqual(self.started, [(ADMIN_ID, ADMIN_ID)])

    async def test_existing_persona_link_offers_replacement(self) -> None:
        await self.store.save_motovskikh_link(
            self.persona["user_id"], 100, "Игрок"
        )
        event = PrivateEvent("/motovskikh_auth", actor=self.actor)

        await self.manager.handle_command(event)

        self.assertEqual(self.started, [])
        self.assertIn("персоне Вася", event.replies[0])
        (identity, chat_id, _expires), = self.manager.rebind_tokens.values()
        self.assertEqual((identity, chat_id), (self.persona["user_id"], ADMIN_ID))

    async def test_active_wager_blocks_persona_link(self) -> None:
        self.store.connection.execute(
            "INSERT INTO dice_challenges VALUES (?, ?, ?)",
            (self.persona["user_id"], ADMIN_ID, "playing"),
        )
        self.store.connection.commit()
        event = PrivateEvent("/motovskikh_auth", actor=self.actor)

        await self.manager.handle_command(event)

        self.assertEqual(self.started, [])
        self.assertIn("персоны Вася", event.replies[0])

    async def test_buttons_are_matched_by_chat_and_token(self) -> None:
        attempt = LinkAttempt(
            user_id=self.persona["user_id"],
            chat_id=ADMIN_ID,
            room_id="room",
            room_url="https://motovskikh.ru/moscow/?r=room",
            token="abc",
            expires_at=time.time() + 60,
        )
        self.manager.attempts[attempt.user_id] = attempt

        self.assertIs(
            self.manager.attempt_for_token(ADMIN_ID, "abc"), attempt
        )
        self.assertIsNone(self.manager.attempt_for_token(ADMIN_ID, "other"))
        self.assertIsNone(self.manager.attempt_for_token(12345, "abc"))


if __name__ == "__main__":
    unittest.main()
