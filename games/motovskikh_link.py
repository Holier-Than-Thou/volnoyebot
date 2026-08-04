"""Telegram flow for linking a Motovskikh Tests account."""

from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import websocket
from telethon import Button, events
from telethon.tl.types import User

from .motovskikh_client import (
    AuthenticationError,
    connect_room,
    create_private_lobby_url,
    create_private_room_id,
    initialize_room,
    load_session,
    refresh_authentication,
    send_action,
)


DEFAULT_TEST_SLUG = "moscow"
OBSERVER_NICKNAME = "Тестовый бот"
HELLO_INTERVAL_SECONDS = 15
CANDIDATE_STABILITY_SECONDS = 1


class AmbiguousRoomError(RuntimeError):
    """More than one candidate entered an account-linking room."""


class LinkAttemptCancelled(RuntimeError):
    """The Telegram user cancelled the linking attempt."""


@dataclass(frozen=True)
class Candidate:
    player_id: int
    nickname: str


@dataclass
class LinkAttempt:
    user_id: int
    room_id: str
    room_url: str
    token: str
    expires_at: float
    cancel: threading.Event = field(default_factory=threading.Event)
    candidate: Candidate | None = None
    task: asyncio.Task | None = None


def select_online_candidates(room: dict, own_player_id: int) -> list[Candidate]:
    candidates = {}
    for player in room.get("s", []):
        player_id = int(player["i"])
        if player_id == own_player_id or not player.get("o"):
            continue
        candidates[player_id] = Candidate(
            player_id=player_id,
            nickname=str(player.get("n") or f"ID {player_id}"),
        )
    return list(candidates.values())


class MotovskikhLinkManager:
    def __init__(
        self,
        client,
        store,
        cookie_path: Path,
        track_task,
        attempt_ttl_seconds: int = 300,
        max_attempts: int = 10,
    ) -> None:
        self.client = client
        self.store = store
        self.cookie_path = cookie_path
        self.track_task = track_task
        self.attempt_ttl_seconds = attempt_ttl_seconds
        self.max_attempts = max_attempts
        self.attempts: dict[int, LinkAttempt] = {}
        self.rebind_tokens: dict[str, tuple[int, float]] = {}
        self.session_lock = threading.Lock()

    async def handle_command(self, event) -> None:
        if not event.is_private:
            await event.reply("Эта команда работает только в личном чате с ботом.")
            return
        sender = await event.get_sender()
        if not isinstance(sender, User) or sender.bot:
            return
        if await self.store.has_active_wager(sender.id):
            await event.reply(
                "Сейчас у вас есть активный игровой вызов или ставка. "
                "Завершите её перед привязкой аккаунта."
            )
            return

        existing = await self.store.get_motovskikh_link(sender.id)
        if existing is None:
            await self.start_attempt(sender.id)
            return

        token = secrets.token_urlsafe(8)
        self.rebind_tokens[token] = (sender.id, time.time() + 120)
        await event.reply(
            "К вашему Telegram уже привязан аккаунт Motovskikh "
            f"«{existing['last_nickname']}». Сменить привязанный аккаунт?",
            buttons=[
                Button.inline("Сменить", data=f"mv:replace:{token}"),
                Button.inline("Отмена", data=f"mv:cancel_replace:{token}"),
            ],
            parse_mode=None,
        )

    async def handle_callback(self, event) -> None:
        parts = event.data.decode("utf-8").split(":", 2)
        if len(parts) != 3:
            await event.answer("Некорректная кнопка.", alert=True)
            return
        _, action, token = parts
        user_id = event.sender_id

        if action in {"replace", "cancel_replace"}:
            owner = self.rebind_tokens.pop(token, None)
            if owner is None or owner[0] != user_id or owner[1] < time.time():
                await event.answer("Кнопка устарела.", alert=True)
                return
            await event.answer()
            if action == "cancel_replace":
                await event.edit("Привязка не изменена.", buttons=None)
                return
            await event.edit("Создаю приватную комнату…", buttons=None)
            await self.start_attempt(user_id)
            return

        attempt = self.attempts.get(user_id)
        if attempt is None or attempt.token != token:
            await event.answer("Попытка привязки уже завершена.", alert=True)
            return
        if attempt.expires_at < time.time():
            self.finish_attempt(attempt)
            await event.answer("Время привязки истекло.", alert=True)
            await event.edit(
                "Время привязки истекло. Запустите /motovskikh_auth ещё раз.",
                buttons=None,
            )
            return
        if action == "cancel":
            await event.answer()
            self.finish_attempt(attempt)
            await event.edit("Привязка отменена. Комнату можно закрыть.", buttons=None)
            return
        if action != "confirm" or attempt.candidate is None:
            await event.answer("Аккаунт ещё не обнаружен.", alert=True)
            return

        await event.answer()
        result = await self.store.save_motovskikh_link(
            user_id,
            attempt.candidate.player_id,
            attempt.candidate.nickname,
        )
        candidate = attempt.candidate
        self.finish_attempt(attempt)
        if result == "active_wager":
            await event.edit(
                "Появилась активная ставка. Привязка не изменена; "
                "комнату можно закрыть.",
                buttons=None,
            )
        elif result == "conflict":
            await event.edit(
                "Этот аккаунт Motovskikh уже привязан к другому "
                "Telegram-пользователю. Привязка не изменена; комнату "
                "можно закрыть.",
                buttons=None,
            )
        else:
            await event.edit(
                "✅ Аккаунт Motovskikh "
                f"«{candidate.nickname}» (ID {candidate.player_id}) привязан.\n"
                "Комнату можно закрыть.",
                buttons=None,
                parse_mode=None,
            )

    async def start_attempt(self, user_id: int) -> None:
        existing_attempt = self.attempts.get(user_id)
        if existing_attempt is not None:
            await self.client.send_message(
                user_id,
                "У вас уже есть активная попытка привязки.",
                buttons=[Button.url("Открыть комнату", existing_attempt.room_url)],
            )
            return
        if len(self.attempts) >= self.max_attempts:
            await self.client.send_message(
                user_id,
                "Сейчас слишком много одновременных привязок. "
                "Попробуйте через несколько минут.",
            )
            return

        room_id = create_private_room_id()
        attempt = LinkAttempt(
            user_id=user_id,
            room_id=room_id,
            room_url=create_private_lobby_url(DEFAULT_TEST_SLUG, room_id),
            token=secrets.token_urlsafe(8),
            expires_at=time.time() + self.attempt_ttl_seconds,
        )
        self.attempts[user_id] = attempt
        await self.client.send_message(
            user_id,
            "Откройте приватную комнату и войдите в свой аккаунт Motovskikh.\n\n"
            "Если сайт попросит подтвердить email, после подтверждения снова "
            "откройте эту ссылку. Не пересылайте её другим людям.",
            buttons=[
                Button.url("Открыть комнату", attempt.room_url),
                Button.inline("Отмена", data=f"mv:cancel:{attempt.token}"),
            ],
            parse_mode=None,
        )
        attempt.task = asyncio.create_task(self.run_attempt(attempt))
        self.track_task(attempt.task)

    async def run_attempt(self, attempt: LinkAttempt) -> None:
        try:
            candidate = await asyncio.to_thread(self.wait_for_candidate, attempt)
        except LinkAttemptCancelled:
            return
        except TimeoutError:
            if self.attempts.get(attempt.user_id) is attempt:
                self.finish_attempt(attempt)
                await self.client.send_message(
                    attempt.user_id,
                    "Время привязки истекло. Запустите /motovskikh_auth ещё раз.",
                )
            return
        except AmbiguousRoomError:
            if self.attempts.get(attempt.user_id) is attempt:
                self.finish_attempt(attempt)
                await self.client.send_message(
                    attempt.user_id,
                    "В комнату вошло несколько аккаунтов. Из соображений "
                    "безопасности привязка отменена; создайте новую комнату.",
                )
            return
        except AuthenticationError:
            if self.attempts.get(attempt.user_id) is attempt:
                self.finish_attempt(attempt)
                await self.client.send_message(
                    attempt.user_id,
                    "Сервисная сессия сайта истекла. Привязка временно "
                    "недоступна; сообщите администратору бота.",
                )
            return
        except Exception as error:
            print(f"Ошибка привязки Motovskikh для {attempt.user_id}: {error}")
            if self.attempts.get(attempt.user_id) is attempt:
                self.finish_attempt(attempt)
                await self.client.send_message(
                    attempt.user_id,
                    "Не удалось подключиться к комнате Motovskikh. "
                    "Попробуйте позднее.",
                )
            return

        if self.attempts.get(attempt.user_id) is not attempt:
            return
        attempt.candidate = candidate
        await self.client.send_message(
            attempt.user_id,
            "Обнаружен аккаунт Motovskikh "
            f"«{candidate.nickname}» (ID {candidate.player_id}). Это ваш аккаунт?",
            buttons=[
                Button.inline("Привязать", data=f"mv:confirm:{attempt.token}"),
                Button.inline("Отмена", data=f"mv:cancel:{attempt.token}"),
            ],
            parse_mode=None,
        )
        await asyncio.sleep(max(0, attempt.expires_at - time.time()))
        if self.attempts.get(attempt.user_id) is attempt:
            self.finish_attempt(attempt)
            await self.client.send_message(
                attempt.user_id,
                "Время подтверждения истекло. Привязка не изменена; "
                "комнату можно закрыть.",
            )

    def wait_for_candidate(self, attempt: LinkAttempt) -> Candidate:
        socket: websocket.WebSocket | None = None
        with self.session_lock:
            opener, cookies = load_session(self.cookie_path)
            refresh_authentication(opener, cookies)
            initialize_room(opener, DEFAULT_TEST_SLUG, attempt.room_id)
        try:
            socket = connect_room(
                cookies,
                DEFAULT_TEST_SLUG,
                attempt.room_id,
            )
            send_action(socket, "join")
            own_player_id = None
            nickname_sent = False
            spectator_sent = False
            candidate = None
            candidate_since = None
            last_hello = time.monotonic()

            while time.time() < attempt.expires_at:
                if attempt.cancel.is_set():
                    raise LinkAttemptCancelled
                try:
                    raw_message = socket.recv()
                except websocket.WebSocketTimeoutException:
                    raw_message = None

                if raw_message:
                    message = json.loads(raw_message)
                    if message.get("a") == "kick":
                        raise RuntimeError("observer was kicked from room")
                    if message.get("a") == "room":
                        room = message.get("d", {})
                        own_player_id = int(room["i"])
                        if not nickname_sent:
                            send_action(socket, "greet", OBSERVER_NICKNAME)
                            nickname_sent = True
                        if not spectator_sent and room.get("c") != "":
                            send_action(socket, "colour", "spectator")
                            spectator_sent = True
                        candidates = select_online_candidates(room, own_player_id)
                        if len(candidates) > 1:
                            raise AmbiguousRoomError
                        if candidates:
                            if candidate != candidates[0]:
                                candidate = candidates[0]
                                candidate_since = time.monotonic()
                        else:
                            candidate = None
                            candidate_since = None

                if (
                    candidate is not None
                    and candidate_since is not None
                    and time.monotonic() - candidate_since
                    >= CANDIDATE_STABILITY_SECONDS
                ):
                    return candidate
                if time.monotonic() - last_hello >= HELLO_INTERVAL_SECONDS:
                    send_action(socket, "hello")
                    last_hello = time.monotonic()
            raise TimeoutError
        finally:
            if socket is not None:
                try:
                    send_action(socket, "bye")
                except websocket.WebSocketException:
                    pass
                socket.close()

    def finish_attempt(self, attempt: LinkAttempt) -> None:
        attempt.cancel.set()
        if self.attempts.get(attempt.user_id) is attempt:
            del self.attempts[attempt.user_id]


def register(
    client,
    store,
    cookie_path: Path,
    track_task,
    attempt_ttl_seconds: int = 300,
    max_attempts: int = 10,
) -> MotovskikhLinkManager:
    manager = MotovskikhLinkManager(
        client,
        store,
        cookie_path,
        track_task,
        attempt_ttl_seconds,
        max_attempts,
    )

    @client.on(
        events.NewMessage(pattern=r"(?i)^/motovskikh_auth(?:@\w+)?\s*$")
    )
    async def motovskikh_auth_command(event) -> None:
        await manager.handle_command(event)

    @client.on(events.CallbackQuery(pattern=b"^mv:"))
    async def motovskikh_auth_callback(event) -> None:
        await manager.handle_callback(event)

    return manager
