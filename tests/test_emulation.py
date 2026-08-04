"""Проверки эмуляции дополнительных игроков в тестовом контуре."""

from __future__ import annotations

import asyncio
import sqlite3
import unittest

from telethon.tl.custom import Message
from telethon.tl.types import MessageEntityMention, PeerUser, User

from games import emulation
from games.emulation_storage import (
    PLAYER_ID_BASE,
    EmulationStoreMixin,
    initialize_emulation_schema,
)


CHAT_ID = -100500
ADMIN_ID = 777


class TestStore(EmulationStoreMixin):
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
            CREATE TABLE player_assets (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
        initialize_emulation_schema(self.connection)
        self.connection.commit()


def make_message(text: str, entities=None, message_id: int = 1) -> Message:
    return Message(
        id=message_id,
        peer_id=PeerUser(ADMIN_ID),
        message=text,
        entities=entities,
    )


class FakeEvent:
    """Минимальный аналог события Telethon для проверки перехвата."""

    def __init__(self, text: str, entities=None, message_id: int = 1) -> None:
        self.message = make_message(text, entities, message_id)
        self.chat_id = CHAT_ID
        self.replies: list[str] = []

    @property
    def raw_text(self) -> str:
        return self.message.message

    async def reply(self, text: str, **_kwargs) -> None:
        self.replies.append(text)


def admin() -> User:
    return User(id=ADMIN_ID, bot=False, first_name="Админ")


class StoreTestCase(unittest.IsolatedAsyncioTestCase):
    """Общая база для проверок, которым нужно хранилище персон."""

    async def asyncSetUp(self) -> None:
        self.store = TestStore()

    async def asyncTearDown(self) -> None:
        self.store.connection.close()


class ParseCommandTests(unittest.TestCase):
    def test_ordinary_message_is_not_emulation(self) -> None:
        self.assertIsNone(emulation.parse_command("каз баланс"))
        self.assertIsNone(emulation.parse_command("эмуляция чего-то"))

    def test_bare_prefix_shows_help(self) -> None:
        self.assertEqual(emulation.parse_command("эмул").kind, "help")
        self.assertEqual(emulation.parse_command("  эмул  ").kind, "help")
        self.assertEqual(emulation.parse_command("эмул помощь").kind, "help")

    def test_management_subcommands(self) -> None:
        self.assertEqual(emulation.parse_command("эмул список").kind, "list")
        created = emulation.parse_command("эмул создать Вася")
        self.assertEqual((created.kind, created.name), ("create", "Вася"))
        deleted = emulation.parse_command("эмул удалить Вася")
        self.assertEqual((deleted.kind, deleted.name), ("delete", "Вася"))

    def test_management_subcommands_require_name(self) -> None:
        parsed = emulation.parse_command("эмул создать")
        self.assertEqual(parsed.kind, "error")
        self.assertIn("имя персоны", parsed.error.casefold())

    def test_invalid_names_are_rejected(self) -> None:
        for text in ("эмул @вася каз баланс", "эмул /вася каз баланс"):
            with self.subTest(text=text):
                parsed = emulation.parse_command(text)
                self.assertEqual(parsed.kind, "error")
                self.assertEqual(parsed.error, emulation.NAME_REQUIREMENTS)

    def test_persona_without_command_is_rejected(self) -> None:
        parsed = emulation.parse_command("эмул Вася")
        self.assertEqual(parsed.kind, "error")
        self.assertIn("Укажите команду", parsed.error)

    def test_persona_command_keeps_offset(self) -> None:
        text = "эмул Вася каз ставка 100"
        parsed = emulation.parse_command(text)
        self.assertEqual(parsed.kind, "act")
        self.assertEqual(parsed.name, "Вася")
        self.assertEqual(parsed.rest, "каз ставка 100")
        self.assertEqual(text[parsed.offset :], parsed.rest)

    def test_case_of_prefix_and_subcommand_is_ignored(self) -> None:
        parsed = emulation.parse_command("ЭМУЛ СОЗДАТЬ Вася")
        self.assertEqual((parsed.kind, parsed.name), ("create", "Вася"))


class RewriteMessageTests(unittest.TestCase):
    def test_prefix_is_removed(self) -> None:
        event = FakeEvent("эмул Вася каз ставка 100")
        parsed = emulation.parse_command(event.raw_text)
        emulation.rewrite_message(event.message, parsed)
        self.assertEqual(event.message.message, "каз ставка 100")
        self.assertEqual(event.raw_text, "каз ставка 100")

    def test_entities_are_shifted(self) -> None:
        text = "эмул Вася мот 100 https://motovskikh.ru/тест/ @сопер"
        mention_offset = text.index("@сопер")
        event = FakeEvent(
            text,
            entities=[MessageEntityMention(offset=mention_offset, length=6)],
        )
        parsed = emulation.parse_command(event.raw_text)
        emulation.rewrite_message(event.message, parsed)
        self.assertEqual(event.message.message, parsed.rest)
        self.assertEqual(len(event.message.entities), 1)
        entity = event.message.entities[0]
        self.assertEqual(
            parsed.rest[entity.offset : entity.offset + entity.length],
            "@сопер",
        )

    def test_entities_inside_prefix_are_dropped(self) -> None:
        text = "эмул Вася каз баланс"
        event = FakeEvent(
            text,
            entities=[MessageEntityMention(offset=0, length=4)],
        )
        parsed = emulation.parse_command(event.raw_text)
        emulation.rewrite_message(event.message, parsed)
        self.assertIsNone(event.message.entities)

    def test_offsets_use_utf16_units(self) -> None:
        # Эмодзи занимает две единицы UTF-16 и одну позицию в строке Python.
        text = "эмул Вася 🎰 каз баланс"
        self.assertEqual(emulation.utf16_length("🎰"), 2)
        event = FakeEvent(text)
        parsed = emulation.parse_command(event.raw_text)
        emulation.rewrite_message(event.message, parsed)
        self.assertEqual(event.message.message, "🎰 каз баланс")


class EmulatedIdentityTests(unittest.TestCase):
    def test_real_identifiers_are_not_emulated(self) -> None:
        self.assertFalse(emulation.is_emulated(ADMIN_ID))
        self.assertFalse(emulation.is_emulated(702747511))

    def test_generated_identifiers_are_emulated(self) -> None:
        self.assertTrue(emulation.is_emulated(PLAYER_ID_BASE - 1))

    def test_emulated_user_is_a_regular_player(self) -> None:
        user = emulation.emulated_user(PLAYER_ID_BASE - 1, "Вася")
        self.assertIsInstance(user, User)
        self.assertFalse(user.bot)
        self.assertEqual(user.first_name, "Вася")


class EmulationStorageTests(StoreTestCase):

    async def test_players_get_unique_negative_identifiers(self) -> None:
        _, first = await self.store.create_emulated_player(CHAT_ID, "Вася")
        _, second = await self.store.create_emulated_player(CHAT_ID, "Петя")
        self.assertEqual(first["user_id"], PLAYER_ID_BASE - 1)
        self.assertEqual(second["user_id"], PLAYER_ID_BASE - 2)
        self.assertTrue(emulation.is_emulated(first["user_id"]))

    async def test_duplicate_name_is_reported(self) -> None:
        await self.store.create_emulated_player(CHAT_ID, "Вася")
        status, row = await self.store.create_emulated_player(CHAT_ID, "вАся")
        self.assertEqual(status, "exists")
        self.assertEqual(row["name"], "Вася")

    async def test_lookup_by_name_ignores_case(self) -> None:
        await self.store.create_emulated_player(CHAT_ID, "Вася")
        row = await self.store.get_emulated_player_by_name(CHAT_ID, "вася")
        self.assertIsNotNone(row)
        self.assertEqual(row["name"], "Вася")

    async def test_same_name_allowed_in_another_chat(self) -> None:
        await self.store.create_emulated_player(CHAT_ID, "Вася")
        status, _ = await self.store.create_emulated_player(-42, "Вася")
        self.assertEqual(status, "created")

    async def test_delete_removes_balance_and_bindings(self) -> None:
        _, row = await self.store.create_emulated_player(CHAT_ID, "Вася")
        user_id = row["user_id"]
        self.store.connection.execute(
            "INSERT INTO balances VALUES (?, ?, ?, ?)",
            (CHAT_ID, user_id, "Вася", 500),
        )
        self.store.connection.execute(
            "INSERT INTO player_assets VALUES (?, ?)", (CHAT_ID, user_id)
        )
        self.store.connection.commit()
        await self.store.bind_emulated_message(CHAT_ID, 10, user_id)

        deleted = await self.store.delete_emulated_player(CHAT_ID, "вася")
        self.assertIsNotNone(deleted)
        self.assertEqual(await self.store.list_emulated_players(CHAT_ID), [])
        self.assertIsNone(
            await self.store.get_emulated_player_by_message(CHAT_ID, 10)
        )
        remaining = self.store.connection.execute(
            "SELECT COUNT(*) AS total FROM balances WHERE user_id = ?",
            (user_id,),
        ).fetchone()["total"]
        self.assertEqual(remaining, 0)

    async def test_delete_unknown_player_returns_none(self) -> None:
        self.assertIsNone(
            await self.store.delete_emulated_player(CHAT_ID, "Вася")
        )

    async def test_message_binding_resolves_player(self) -> None:
        _, row = await self.store.create_emulated_player(CHAT_ID, "Вася")
        await self.store.bind_emulated_message(CHAT_ID, 25, row["user_id"])
        found = await self.store.get_emulated_player_by_message(CHAT_ID, 25)
        self.assertEqual(found["user_id"], row["user_id"])
        self.assertEqual(found["name"], "Вася")
        self.assertIsNone(
            await self.store.get_emulated_player_by_message(CHAT_ID, 26)
        )

    async def test_list_reports_balance_presence(self) -> None:
        _, row = await self.store.create_emulated_player(CHAT_ID, "Вася")
        listed = await self.store.list_emulated_players(CHAT_ID)
        self.assertEqual(len(listed), 1)
        self.assertFalse(listed[0]["has_balance"])
        self.store.connection.execute(
            "INSERT INTO balances VALUES (?, ?, ?, ?)",
            (CHAT_ID, row["user_id"], "Вася", 700),
        )
        self.store.connection.commit()
        listed = await self.store.list_emulated_players(CHAT_ID)
        self.assertTrue(listed[0]["has_balance"])
        self.assertEqual(listed[0]["balance"], 700)


class HandlePrefixTests(StoreTestCase):

    async def test_message_without_prefix_passes_through(self) -> None:
        event = FakeEvent("каз баланс")
        result = await emulation.handle_prefix(
            event, self.store, ADMIN_ID, admin()
        )
        self.assertFalse(result.stop)
        self.assertIsNone(result.actor)
        self.assertEqual(event.replies, [])

    async def test_regular_user_is_rejected(self) -> None:
        event = FakeEvent("эмул создать Вася")
        stranger = User(id=ADMIN_ID + 1, bot=False, first_name="Гость")
        result = await emulation.handle_prefix(
            event, self.store, ADMIN_ID, stranger
        )
        self.assertTrue(result.stop)
        self.assertEqual(await self.store.list_emulated_players(CHAT_ID), [])
        self.assertIn("только администратору", event.replies[0])

    async def test_create_and_list(self) -> None:
        create_event = FakeEvent("эмул создать Вася")
        result = await emulation.handle_prefix(
            create_event, self.store, ADMIN_ID, admin()
        )
        self.assertTrue(result.stop)
        list_event = FakeEvent("эмул список")
        await emulation.handle_prefix(
            list_event, self.store, ADMIN_ID, admin()
        )
        self.assertIn("Вася", list_event.replies[0])

    async def test_unknown_persona_is_reported(self) -> None:
        event = FakeEvent("эмул Вася каз баланс")
        result = await emulation.handle_prefix(
            event, self.store, ADMIN_ID, admin()
        )
        self.assertTrue(result.stop)
        self.assertIsNone(result.actor)
        self.assertIn("не найдена", event.replies[0])
        self.assertEqual(event.raw_text, "эмул Вася каз баланс")

    async def test_persona_command_is_rewritten_and_bound(self) -> None:
        await self.store.create_emulated_player(CHAT_ID, "Вася")
        event = FakeEvent("эмул Вася каз ставка 100", message_id=77)
        result = await emulation.handle_prefix(
            event, self.store, ADMIN_ID, admin()
        )

        self.assertFalse(result.stop)
        self.assertIsNotNone(result.actor)
        self.assertEqual(result.actor.first_name, "Вася")
        self.assertTrue(emulation.is_emulated(result.actor.id))
        self.assertEqual(event.raw_text, "каз ставка 100")
        self.assertEqual(event.replies, [])

        # Обработчики игр получают персону вместо администратора.
        self.assertIs(emulation.get_actor(event.message), result.actor)
        self.assertIs(await emulation.event_sender(event), result.actor)

        # Ответ на это сообщение адресует команду персоне.
        bound = await emulation.message_sender(
            self.store, CHAT_ID, event.message
        )
        self.assertEqual(bound.id, result.actor.id)
        self.assertEqual(bound.first_name, "Вася")

    async def test_event_sender_falls_back_to_real_author(self) -> None:
        event = FakeEvent("каз баланс")
        event.get_sender = _returning(admin())
        self.assertEqual((await emulation.event_sender(event)).id, ADMIN_ID)

    async def test_message_sender_falls_back_to_real_author(self) -> None:
        message = make_message("привет", message_id=91)
        message.get_sender = _returning(admin())
        found = await emulation.message_sender(self.store, CHAT_ID, message)
        self.assertEqual(found.id, ADMIN_ID)


class ResolvePersonaTests(StoreTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        _, self.local = await self.store.create_emulated_player(
            CHAT_ID, "Вася"
        )

    async def test_name_is_resolved_inside_the_chat(self) -> None:
        status, rows = await emulation.resolve_persona(
            self.store, CHAT_ID, "вася"
        )
        self.assertEqual(status, "found")
        self.assertEqual(rows[0]["user_id"], self.local["user_id"])

    async def test_identifier_is_resolved(self) -> None:
        status, rows = await emulation.resolve_persona(
            self.store, CHAT_ID, str(self.local["user_id"])
        )
        self.assertEqual(status, "found")
        self.assertEqual(rows[0]["user_id"], self.local["user_id"])

    async def test_identifier_of_another_chat_is_not_visible(self) -> None:
        _, other = await self.store.create_emulated_player(-77, "Петя")
        status, _rows = await emulation.resolve_persona(
            self.store, CHAT_ID, str(other["user_id"])
        )
        self.assertEqual(status, "missing")

    async def test_identifier_works_without_chat_context(self) -> None:
        _, other = await self.store.create_emulated_player(-77, "Петя")
        status, rows = await emulation.resolve_persona(
            self.store, None, str(other["user_id"])
        )
        self.assertEqual(status, "found")
        self.assertEqual(rows[0]["user_id"], other["user_id"])

    async def test_unique_name_resolves_without_chat_context(self) -> None:
        status, rows = await emulation.resolve_persona(
            self.store, None, "Вася"
        )
        self.assertEqual(status, "found")
        self.assertEqual(rows[0]["user_id"], self.local["user_id"])

    async def test_name_used_in_two_chats_is_ambiguous(self) -> None:
        await self.store.create_emulated_player(-77, "Вася")
        status, rows = await emulation.resolve_persona(
            self.store, None, "Вася"
        )
        self.assertEqual(status, "ambiguous")
        self.assertEqual(len(rows), 2)

    async def test_unknown_name_is_missing(self) -> None:
        status, _rows = await emulation.resolve_persona(
            self.store, CHAT_ID, "Петя"
        )
        self.assertEqual(status, "missing")

    async def test_real_identifier_is_treated_as_a_name(self) -> None:
        self.assertIsNone(emulation.parse_player_id("702747511"))
        self.assertIsNone(emulation.parse_player_id("Вася"))
        self.assertEqual(
            emulation.parse_player_id(str(PLAYER_ID_BASE - 5)),
            PLAYER_ID_BASE - 5,
        )


class MentionedPersonaTests(StoreTestCase):
    async def asyncSetUp(self) -> None:
        await super().asyncSetUp()
        _, self.persona = await self.store.create_emulated_player(
            CHAT_ID, "Петя"
        )

    async def test_cyrillic_mention_is_resolved(self) -> None:
        found = await emulation.mentioned_persona(
            self.store, CHAT_ID, "мот 100 @Петя https://motovskikh.ru/тест/"
        )
        self.assertIsNotNone(found)
        self.assertEqual(found.id, self.persona["user_id"])
        self.assertEqual(found.first_name, "Петя")

    async def test_unknown_mention_is_ignored(self) -> None:
        self.assertIsNone(
            await emulation.mentioned_persona(self.store, CHAT_ID, "мот @Вася")
        )

    async def test_at_sign_inside_a_word_is_not_a_mention(self) -> None:
        self.assertIsNone(
            await emulation.mentioned_persona(
                self.store, CHAT_ID, "почта Петя@Петя"
            )
        )

    async def test_persona_of_another_chat_is_ignored(self) -> None:
        self.assertIsNone(
            await emulation.mentioned_persona(self.store, -77, "мот @Петя")
        )


class PrivatePrefixTests(StoreTestCase):

    async def test_registry_commands_are_group_only(self) -> None:
        for text in ("эмул список", "эмул создать Вася", "эмул удалить Вася"):
            with self.subTest(text=text):
                event = FakeEvent(text)
                result = await emulation.handle_prefix(
                    event, self.store, ADMIN_ID, admin(), private=True
                )
                self.assertTrue(result.stop)
                self.assertIn("групповом чате", event.replies[0])

    async def test_persona_is_resolved_across_chats(self) -> None:
        _, row = await self.store.create_emulated_player(-77, "Вася")
        event = FakeEvent("эмул Вася /motovskikh_auth", message_id=12)
        result = await emulation.handle_prefix(
            event, self.store, ADMIN_ID, admin(), private=True
        )

        self.assertFalse(result.stop)
        self.assertEqual(result.actor.id, row["user_id"])
        self.assertEqual(event.raw_text, "/motovskikh_auth")
        # Личный чат не принадлежит персоне, привязка сообщения не нужна.
        self.assertIsNone(
            await self.store.get_emulated_player_by_message(CHAT_ID, 12)
        )

    async def test_ambiguous_name_asks_for_identifier(self) -> None:
        _, first = await self.store.create_emulated_player(-77, "Вася")
        await self.store.create_emulated_player(-78, "Вася")
        event = FakeEvent("эмул Вася /motovskikh_auth")
        result = await emulation.handle_prefix(
            event, self.store, ADMIN_ID, admin(), private=True
        )

        self.assertTrue(result.stop)
        self.assertIn(str(first["user_id"]), event.replies[0])

    async def test_persona_can_be_named_by_identifier(self) -> None:
        _, row = await self.store.create_emulated_player(-77, "Вася")
        event = FakeEvent(f"эмул {row['user_id']} /motovskikh_auth")
        result = await emulation.handle_prefix(
            event, self.store, ADMIN_ID, admin(), private=True
        )

        self.assertFalse(result.stop)
        self.assertEqual(result.actor.id, row["user_id"])

    async def test_has_prefix_detects_emulation_messages(self) -> None:
        self.assertTrue(emulation.has_prefix("эмул Вася /motovskikh_auth"))
        self.assertTrue(emulation.has_prefix("эмул"))
        self.assertFalse(emulation.has_prefix("/motovskikh_auth"))


def _returning(value):
    async def call():
        return value

    return call


class FormatPlayersTests(StoreTestCase):

    async def test_empty_list_suggests_creating(self) -> None:
        rows = await self.store.list_emulated_players(CHAT_ID)
        self.assertIn("создать", emulation.format_players(rows))

    async def test_players_are_listed_with_identifiers(self) -> None:
        _, row = await self.store.create_emulated_player(CHAT_ID, "Вася")
        rows = await self.store.list_emulated_players(CHAT_ID)
        text = emulation.format_players(rows)
        self.assertIn("Вася", text)
        self.assertIn(str(row["user_id"]), text)


if __name__ == "__main__":
    unittest.main()
