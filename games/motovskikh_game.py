"""Telegram challenges backed by private Motovskikh Tests rooms."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

import websocket
from telethon import Button, events, types
from telethon.tl.types import Channel, Chat, User

from . import emulation
from .casino import (
    fit_telegram_message,
    is_explicit_message_reply,
    message_topic_id,
)
from .motovskikh_client import (
    BASE_URL,
    SESSION_LOCK,
    connect_room,
    create_private_lobby_url,
    create_private_room_id,
    initialize_room,
    load_session,
    refresh_authentication,
    send_action,
)


CHALLENGE_TTL_SECONDS = 60
DEFAULT_MATCH_TTL_SECONDS = 60 * 60
HELLO_INTERVAL_SECONDS = 15
RECONNECT_DELAY_SECONDS = 3
MAX_CONNECTION_FAILURES = 5
OBSERVER_NICKNAME = "Тестовый бот"
RESERVED_ROOTS = {
    "api",
    "css",
    "hello",
    "img",
    "js",
    "tests",
    "verify",
}


@dataclass(frozen=True)
class ChallengeArguments:
    stake: int
    test_slug: str
    test_url: str


@dataclass(frozen=True)
class MatchOutcome:
    challenger_score: str
    opponent_score: str
    winner_id: int | None


def parse_test_url(value: str) -> tuple[str, str]:
    """Validate a public test URL and return its map slug and canonical URL."""
    parsed = urllib.parse.urlsplit(value.strip().strip("<>"))
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != "motovskikh.ru":
        raise ValueError("Ссылка должна вести на https://motovskikh.ru/.")
    slug = urllib.parse.unquote(parsed.path).strip("/")
    if (
        not slug
        or slug.casefold().split("/", 1)[0] in RESERVED_ROOTS
        or slug.startswith(".")
        or any(part in {".", ".."} for part in slug.split("/"))
    ):
        raise ValueError("Укажите ссылку именно на тест Motovskikh.")
    safe_slug = urllib.parse.quote(slug, safe="/")
    return slug, f"{BASE_URL}/{safe_slug}/"


def parse_challenge_arguments(command_line: str) -> ChallengeArguments:
    """Parse an optional integer stake and one mandatory test URL."""
    tokens = [token for token in command_line.split() if not token.startswith("@")]
    urls = [token for token in tokens if token.startswith(("https://", "http://"))]
    other = [token for token in tokens if token not in urls]
    if len(urls) != 1 or len(other) > 1:
        raise ValueError(
            "Формат: `мот [ставка] <ссылка на тест>` ответом игроку "
            "или с его @тегом."
        )
    if not other:
        stake = 0
    else:
        try:
            stake = int(other[0])
        except ValueError as error:
            raise ValueError("Ставка должна быть целым числом.") from error
        if stake <= 0:
            raise ValueError("Для игры без ставки просто не указывайте сумму.")
        if stake > 9_223_372_036_854_775_807:
            raise ValueError("Указана слишком большая ставка.")
    test_slug, test_url = parse_test_url(urls[0])
    return ChallengeArguments(stake, test_slug, test_url)


def determine_outcome(
    players: list[dict],
    challenger_motovskikh_id: int,
    opponent_motovskikh_id: int,
    challenger_telegram_id: int,
    opponent_telegram_id: int,
) -> MatchOutcome:
    """Use the server's final player order; equal displayed scores are a tie."""
    expected = {challenger_motovskikh_id, opponent_motovskikh_id}
    ordered = [player for player in players if int(player.get("i", -1)) in expected]
    if {int(player["i"]) for player in ordered} != expected:
        raise RuntimeError("В итоговой таблице отсутствует один из игроков")
    scores = {int(player["i"]): str(player.get("s", "")).strip() for player in ordered}
    challenger_score = scores[challenger_motovskikh_id]
    opponent_score = scores[opponent_motovskikh_id]
    if not challenger_score or not opponent_score:
        raise RuntimeError("Сайт не прислал итоговые очки игроков")
    if challenger_score == opponent_score:
        winner_id = None
    else:
        first_player_id = int(ordered[0]["i"])
        winner_id = (
            challenger_telegram_id
            if first_player_id == challenger_motovskikh_id
            else opponent_telegram_id
        )
    return MatchOutcome(challenger_score, opponent_score, winner_id)


def unexpected_online_player_ids(
    players: list[dict], own_player_id: int, expected_player_ids: set[int]
) -> list[int]:
    """Return online room occupants that are neither the host nor participants."""
    return [
        int(player["i"])
        for player in players
        if player.get("o")
        and int(player["i"]) != own_player_id
        and int(player["i"]) not in expected_player_ids
    ]


async def resolve_mentioned_user(client, event) -> User | None:
    """Resolve the first Telegram user mentioned in a command."""
    for entity, text in event.message.get_entities_text():
        try:
            if isinstance(entity, types.MessageEntityMentionName):
                candidate = await client.get_entity(entity.user_id)
            elif isinstance(entity, types.MessageEntityMention):
                candidate = await client.get_entity(text)
            else:
                continue
        except Exception:
            continue
        if isinstance(candidate, User):
            return candidate
    return None


async def resolve_target_user(
    client, store, event, default_to_sender: bool = False
) -> User | None:
    if is_explicit_message_reply(event.message):
        replied = await event.get_reply_message()
        candidate = await emulation.message_sender(
            store, event.chat_id, replied
        )
        return candidate if isinstance(candidate, User) else None
    mentioned = await resolve_mentioned_user(client, event)
    if mentioned is not None:
        return mentioned
    # Персона упоминается «@Именем» и не является пользователем Telegram.
    persona = await emulation.mentioned_persona(
        store, event.chat_id, event.raw_text or ""
    )
    if persona is not None:
        return persona
    if default_to_sender:
        sender = await emulation.event_sender(event)
        return sender if isinstance(sender, User) else None
    return None


class MatchCancelled(RuntimeError):
    pass


class MotovskikhGameManager:
    def __init__(
        self,
        client,
        store,
        cookie_path: Path,
        display_name,
        track_task,
        get_bot_username,
        initial_balance: int,
        match_ttl_seconds: int = DEFAULT_MATCH_TTL_SECONDS,
    ) -> None:
        self.client = client
        self.store = store
        self.cookie_path = cookie_path
        self.display_name = display_name
        self.track_task = track_task
        self.get_bot_username = get_bot_username
        self.initial_balance = initial_balance
        self.match_ttl_seconds = match_ttl_seconds
        self.expiration_tasks: set[asyncio.Task] = set()

    def schedule_expiration(
        self, chat_id: int, proposal_message_id: int, delay: float
    ) -> None:
        task = asyncio.create_task(
            self.expire_challenge_later(chat_id, proposal_message_id, delay)
        )
        self.expiration_tasks.add(task)
        task.add_done_callback(self.expiration_tasks.discard)
        self.track_task(task)

    async def expire_challenge_later(
        self, chat_id: int, proposal_message_id: int, delay: float
    ) -> None:
        await asyncio.sleep(max(0, delay))
        if not await self.store.expire_motovskikh_challenge(
            chat_id, proposal_message_id
        ):
            return
        try:
            await self.client.delete_messages(chat_id, [proposal_message_id])
        except Exception:
            pass

    async def restore(self) -> None:
        now = time.time()
        for challenge in await self.store.get_pending_motovskikh_challenges():
            self.schedule_expiration(
                challenge["chat_id"],
                challenge["proposal_message_id"],
                CHALLENGE_TTL_SECONDS - (now - challenge["created_at"]),
            )
        for match in await self.store.recover_interrupted_motovskikh_matches():
            stake_text = (
                f" Ставки по {match['stake']} очков возвращены."
                if match["stake"]
                else ""
            )
            try:
                await self.client.send_message(
                    match["chat_id"],
                    "Матч Motovskikh прерван перезапуском бота."
                    f"{stake_text}",
                    reply_to=match["topic_id"] or None,
                    parse_mode=None,
                )
            except Exception:
                pass

    async def prompt_link(self, event, user: User) -> None:
        if emulation.is_emulated(user.id):
            name = self.display_name(user)
            await event.reply(
                f"У персоны {name} нет привязанного аккаунта Motovskikh. "
                "Привяжите его в личном чате с ботом командой "
                f"«эмул {name} /motovskikh_auth».",
                parse_mode=None,
            )
            return
        text = (
            "Для участия привяжите аккаунт Motovskikh в личном чате: "
            "отправьте /motovskikh_auth."
        )
        try:
            await self.client.send_message(user, text, parse_mode=None)
        except Exception:
            username = self.get_bot_username()
            buttons = (
                [
                    Button.url(
                        "Открыть личный чат",
                        f"https://t.me/{username}?start=motovskikh_auth",
                    )
                ]
                if username
                else None
            )
            await event.reply(
                f"{self.display_name(user)}, сначала привяжите аккаунт "
                "Motovskikh в личном чате с ботом.",
                buttons=buttons,
                parse_mode=None,
            )
            return
        await event.reply(
            f"{self.display_name(user)}, я отправил инструкцию по привязке "
            "в личные сообщения.",
            parse_mode=None,
        )

    async def handle_command(self, event) -> None:
        if not event.is_group:
            return
        sender = await emulation.event_sender(event)
        chat = await event.get_chat()
        if not isinstance(sender, User) or not isinstance(chat, (Chat, Channel)):
            return
        if not await self.store.is_topic_enabled(
            event.chat_id, message_topic_id(event.message)
        ):
            return

        command_line = (event.pattern_match.group(1) or "").strip()
        first_word = command_line.split(maxsplit=1)[0].casefold() if command_line else ""
        if first_word == "инфо":
            await self.show_info(event)
            return
        if first_word == "топ":
            await self.show_top(event, command_line)
            return

        challenge_line = command_line
        for entity, text in event.message.get_entities_text():
            if isinstance(
                entity,
                (types.MessageEntityMention, types.MessageEntityMentionName),
            ):
                challenge_line = challenge_line.replace(text, " ", 1)
        try:
            arguments = parse_challenge_arguments(challenge_line)
        except ValueError as error:
            await event.reply(str(error))
            return
        opponent = await resolve_target_user(self.client, self.store, event)
        if opponent is None:
            await event.reply(
                "Ответьте командой на сообщение соперника или укажите его @тег."
            )
            return
        if opponent.bot:
            await event.reply("Вызвать на игру можно только обычного пользователя.")
            return
        if opponent.id == sender.id:
            await event.reply("Нельзя вызвать на игру самого себя.")
            return

        challenger_link = await self.store.get_motovskikh_link(sender.id)
        if challenger_link is None:
            await self.prompt_link(event, sender)
            return
        opponent_link = await self.store.get_motovskikh_link(opponent.id)
        if opponent_link is None:
            await self.prompt_link(event, opponent)
            return

        challenger_name = self.display_name(sender)
        opponent_name = self.display_name(opponent)
        if arguments.stake:
            balance = await self.store.get_or_create(
                event.chat_id, sender.id, challenger_name
            )
            if balance < arguments.stake:
                await event.reply(
                    f"Недостаточно очков. Текущий баланс: {balance}."
                )
                return
        stake_text = (
            f" на {arguments.stake} очков" if arguments.stake else " без ставки"
        )
        anchor = event.message.id
        if is_explicit_message_reply(event.message):
            replied = await event.get_reply_message()
            anchor = replied.id
        proposal = await self.client.send_message(
            chat,
            (
                f"🗺 {challenger_name} вызывает {opponent_name} на тест"
                f"{stake_text}.\n{arguments.test_url}\n"
                "Чтобы принять вызов, ответьте на это сообщение «+» или «да»."
            ),
            reply_to=anchor,
            parse_mode=None,
        )
        status = await self.store.create_motovskikh_challenge(
            event.chat_id,
            message_topic_id(event.message),
            proposal.id,
            sender.id,
            challenger_name,
            int(challenger_link["motovskikh_player_id"]),
            opponent.id,
            opponent_name,
            int(opponent_link["motovskikh_player_id"]),
            arguments.stake,
            arguments.test_slug,
            arguments.test_url,
        )
        if status == "active":
            try:
                await self.client.delete_messages(chat, [proposal.id])
            except Exception:
                pass
            await event.reply(
                "Один из игроков уже участвует в активном вызове или матче."
            )
            return
        self.schedule_expiration(
            event.chat_id, proposal.id, CHALLENGE_TTL_SECONDS
        )

    async def show_info(self, event) -> None:
        target = await resolve_target_user(
            self.client, self.store, event, default_to_sender=True
        )
        if target is None or target.bot:
            await event.reply(
                "Формат: `мот инфо` либо эта команда ответом игроку или с @тегом."
            )
            return
        stats = await self.store.get_motovskikh_stats(event.chat_id, target.id)
        if stats is None:
            await event.reply(
                f"У игрока {self.display_name(target)} пока нет завершённых матчей."
            )
            return
        await event.reply(
            "🗺 Статистика Motovskikh — "
            f"{self.display_name(target)}\n"
            f"Игр: {stats['games']}\n"
            f"Побед: {stats['wins']} · поражений: {stats['losses']} · "
            f"ничьих: {stats['draws']}\n"
            f"Процент побед: {stats['win_rate']:.1f}%",
            parse_mode=None,
        )

    async def show_top(self, event, command_line: str) -> None:
        if command_line.casefold() != "топ":
            await event.reply("Формат: `мот топ`.")
            return
        rows = await self.store.top_motovskikh_players(event.chat_id)
        if not rows:
            await event.reply("Статистика матчей Motovskikh пока пуста.")
            return
        lines = [
            f"{index}. {row['player_name']} — {row['games']} игр, "
            f"{row['win_rate']:.1f}% побед"
            for index, row in enumerate(rows, start=1)
        ]
        await event.reply(
            fit_telegram_message("🏆 Топ Motovskikh:", lines),
            parse_mode=None,
        )

    async def handle_accept(self, event) -> None:
        if not event.is_group or not is_explicit_message_reply(event.message):
            return
        sender = await emulation.event_sender(event)
        chat = await event.get_chat()
        if not isinstance(sender, User) or not isinstance(chat, (Chat, Channel)):
            return
        if not await self.store.is_topic_enabled(
            event.chat_id, message_topic_id(event.message)
        ):
            return
        proposal = await event.get_reply_message()
        status, challenge = await self.store.accept_motovskikh_challenge(
            event.chat_id,
            proposal.id,
            sender.id,
            time.time() - CHALLENGE_TTL_SECONDS,
            self.initial_balance,
        )
        if status in {"not_found", "wrong_user"}:
            return
        if status == "expired":
            try:
                await self.client.delete_messages(chat, [proposal.id])
            except Exception:
                pass
            await event.reply("Время принятия вызова истекло.")
            return
        if status == "challenger_funds":
            await event.reply(
                f"У игрока {challenge['challenger_name']} уже недостаточно очков."
            )
            return
        if status == "opponent_funds":
            await event.reply(
                f"У игрока {challenge['opponent_name']} недостаточно очков."
            )
            return
        if status == "link_changed":
            await event.reply(
                "Привязка одного из аккаунтов изменилась. Создайте новый вызов."
            )
            return

        room_id = create_private_room_id()
        room_url = create_private_lobby_url(challenge["test_slug"], room_id)
        try:
            cookies = await asyncio.to_thread(
                self.prepare_room, challenge["test_slug"], room_id
            )
            await self.store.set_motovskikh_room(
                event.chat_id, proposal.id, room_id
            )
        except Exception as error:
            print(f"Не удалось создать комнату Motovskikh: {error}")
            await self.store.refund_motovskikh_challenge(
                event.chat_id, proposal.id
            )
            await event.reply(
                "Не удалось создать комнату Motovskikh. "
                "Если была ставка, она возвращена."
            )
            return

        bank_text = (
            f" Банк: {challenge['stake'] * 2} очков."
            if challenge["stake"]
            else ""
        )
        try:
            room_message = await event.reply(
                "✅ Вызов принят. Оба игрока должны открыть приватную комнату, "
                "выбрать цвет и нажать «Поехали!»."
                f"{bank_text}\n{room_url}",
                parse_mode=None,
            )
        except Exception:
            await self.store.refund_motovskikh_challenge(
                event.chat_id, proposal.id
            )
            raise
        task = asyncio.create_task(
            self.run_match(challenge, cookies, room_id, room_message.id)
        )
        self.track_task(task)

    def prepare_room(self, test_slug: str, room_id: str):
        with SESSION_LOCK:
            opener, cookies = load_session(self.cookie_path)
            refresh_authentication(opener, cookies)
            initialize_room(opener, test_slug, room_id)
        return cookies

    async def run_match(
        self, challenge: dict, cookies, room_id: str, room_message_id: int
    ) -> None:
        cancel = threading.Event()
        try:
            outcome = await asyncio.to_thread(
                self.observe_match, challenge, cookies, room_id, cancel
            )
            result = await self.store.finish_motovskikh_challenge(
                challenge["chat_id"],
                challenge["proposal_message_id"],
                outcome.challenger_score,
                outcome.opponent_score,
                outcome.winner_id,
            )
            score_text = (
                f"{challenge['challenger_name']}: {outcome.challenger_score}; "
                f"{challenge['opponent_name']}: {outcome.opponent_score}."
            )
            if outcome.winner_id is None:
                text = f"🤝 Победила дружба!\n{score_text}"
                if challenge["stake"]:
                    text += "\nСтавки возвращены игрокам."
            else:
                winner_name = (
                    challenge["challenger_name"]
                    if outcome.winner_id == challenge["challenger_id"]
                    else challenge["opponent_name"]
                )
                text = f"🏆 Победитель: {winner_name}!\n{score_text}"
                if challenge["stake"]:
                    text += (
                        f"\nВыигрыш: {challenge['stake'] * 2} очков. "
                        f"Баланс победителя: {result['winner_balance']}."
                    )
            await self.client.send_message(
                challenge["chat_id"],
                text,
                reply_to=room_message_id,
                parse_mode=None,
            )
        except asyncio.CancelledError:
            cancel.set()
            raise
        except Exception as error:
            print(
                "Матч Motovskikh завершён технической ошибкой "
                f"({challenge['chat_id']}/{challenge['proposal_message_id']}): {error}"
            )
            refunded = await self.store.refund_motovskikh_challenge(
                challenge["chat_id"], challenge["proposal_message_id"]
            )
            if refunded is not None:
                try:
                    await self.client.send_message(
                        challenge["chat_id"],
                        "Матч Motovskikh не удалось завершить. "
                        "Если была ставка, она возвращена.",
                        reply_to=room_message_id,
                        parse_mode=None,
                    )
                except Exception:
                    pass
        finally:
            cancel.set()

    def observe_match(
        self, challenge: dict, cookies, room_id: str, cancel: threading.Event
    ) -> MatchOutcome:
        deadline = time.time() + self.match_ttl_seconds
        latest_players: list[dict] = []
        failures = 0
        expected = {
            int(challenge["challenger_motovskikh_id"]),
            int(challenge["opponent_motovskikh_id"]),
        }
        while time.time() < deadline and not cancel.is_set():
            socket: websocket.WebSocket | None = None
            try:
                socket = connect_room(
                    cookies, challenge["test_slug"], room_id, timeout=2
                )
                send_action(socket, "join")
                own_player_id = None
                nickname_sent = False
                spectator_sent = False
                last_hello = time.monotonic()
                score_received_at = None
                while time.time() < deadline and not cancel.is_set():
                    try:
                        raw_message = socket.recv()
                    except websocket.WebSocketTimeoutException:
                        raw_message = None
                    if raw_message == "":
                        raise websocket.WebSocketConnectionClosedException(
                            "Motovskikh WebSocket closed"
                        )
                    if raw_message:
                        message = json.loads(raw_message)
                        action = message.get("a")
                        if action == "kick":
                            raise RuntimeError("Сервисный наблюдатель исключён")
                        if action == "room":
                            room = message.get("d") or {}
                            failures = 0
                            own_player_id = int(room["i"])
                            if not room.get("h"):
                                raise RuntimeError("Сервисный аккаунт перестал быть хостом")
                            if not nickname_sent:
                                send_action(socket, "greet", OBSERVER_NICKNAME)
                                nickname_sent = True
                            if not spectator_sent and room.get("c") != "":
                                send_action(socket, "colour", "spectator")
                                spectator_sent = True
                            latest_players = list(room.get("s") or [])
                            for player_id in unexpected_online_player_ids(
                                latest_players, own_player_id, expected
                            ):
                                send_action(socket, "kick", player_id)
                        elif action == "score":
                            score_received_at = time.monotonic()
                    if score_received_at is not None and (
                        time.monotonic() - score_received_at >= 1
                    ):
                        return determine_outcome(
                            latest_players,
                            int(challenge["challenger_motovskikh_id"]),
                            int(challenge["opponent_motovskikh_id"]),
                            int(challenge["challenger_id"]),
                            int(challenge["opponent_id"]),
                        )
                    if time.monotonic() - last_hello >= HELLO_INTERVAL_SECONDS:
                        send_action(socket, "hello")
                        last_hello = time.monotonic()
            except (OSError, ValueError, json.JSONDecodeError, websocket.WebSocketException):
                failures += 1
                if failures >= MAX_CONNECTION_FAILURES:
                    raise
                if cancel.wait(RECONNECT_DELAY_SECONDS):
                    raise MatchCancelled
            finally:
                if socket is not None:
                    try:
                        send_action(socket, "bye")
                    except websocket.WebSocketException:
                        pass
                    try:
                        socket.close()
                    except (OSError, websocket.WebSocketException):
                        pass
        if cancel.is_set():
            raise MatchCancelled
        raise TimeoutError("Время матча Motovskikh истекло")


def register(
    client,
    store,
    cookie_path: Path,
    display_name,
    track_task,
    get_bot_username,
    initial_balance: int,
    match_ttl_seconds: int = DEFAULT_MATCH_TTL_SECONDS,
) -> MotovskikhGameManager:
    manager = MotovskikhGameManager(
        client,
        store,
        cookie_path,
        display_name,
        track_task,
        get_bot_username,
        initial_balance,
        match_ttl_seconds,
    )

    @client.on(
        events.NewMessage(pattern=r"(?iu)^(?:каз\s+)?мот(?:\s+(.*?))?\s*$")
    )
    async def motovskikh_command(event) -> None:
        await manager.handle_command(event)

    @client.on(events.NewMessage(pattern=r"(?iu)^(?:\+|да)\s*$"))
    async def motovskikh_accept(event) -> None:
        await manager.handle_accept(event)

    return manager
