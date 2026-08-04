"""Undocumented Motovskikh Tests HTTP and WebSocket client."""

from __future__ import annotations

import http.cookiejar
import json
import secrets
import string
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import websocket


BASE_URL = "https://motovskikh.ru"
WS_BASE_URL = "wss://motovskikh.ru/api/wsup/v1/map"
WS_PATH = "/api/wsup/v1/map"
PRIVATE_ROOM_ID_LENGTH = 11
SESSION_LOCK = threading.Lock()


class AuthenticationError(RuntimeError):
    """The saved service session cannot be refreshed."""


def normalize_test_slug(test_slug: str) -> str:
    normalized_slug = test_slug.strip().strip("/")
    if not normalized_slug:
        raise ValueError("test slug must not be empty")
    return normalized_slug


def create_private_room_id() -> str:
    return "".join(
        secrets.choice(string.ascii_lowercase)
        for _ in range(PRIVATE_ROOM_ID_LENGTH)
    )


def create_private_lobby_url(test_slug: str, room_id: str | None = None) -> str:
    normalized_slug = normalize_test_slug(test_slug)
    safe_slug = urllib.parse.quote(normalized_slug, safe="/")
    room_id = room_id or create_private_room_id()
    return f"{BASE_URL}/{safe_slug}/#{room_id}"


def post_json(
    opener: urllib.request.OpenerDirector,
    path: str,
    payload: dict[str, object],
) -> dict[str, object]:
    request = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/hello/",
            "User-Agent": "volnoyebot-motovskikh-lobby/1.0",
        },
        method="POST",
    )
    with opener.open(request, timeout=20) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise RuntimeError(f"Motovskikh API rejected {path}")
    return result


def is_authenticated(opener: urllib.request.OpenerDirector) -> bool:
    try:
        post_json(opener, "/api/v1/auth/ok", {})
    except urllib.error.HTTPError as error:
        if error.code == 401:
            return False
        raise
    return True


def refresh_authentication(
    opener: urllib.request.OpenerDirector,
    cookies: http.cookiejar.MozillaCookieJar,
) -> None:
    if is_authenticated(opener):
        return
    try:
        post_json(opener, "/api/v1/auth/refresh", {})
    except (OSError, RuntimeError, urllib.error.HTTPError) as error:
        raise AuthenticationError(
            "Motovskikh service session needs manual authorization"
        ) from error
    if not is_authenticated(opener):
        raise AuthenticationError(
            "Motovskikh service session refresh was not accepted"
        )
    cookies.save(ignore_discard=True, ignore_expires=True)


def extract_verification_code(code_or_link: str) -> str:
    value = code_or_link.strip()
    if not value:
        raise ValueError("verification code must not be empty")
    if not value.startswith(("https://", "http://")):
        return value

    parsed = urllib.parse.urlsplit(value)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (
        hostname == "motovskikh.ru" or hostname.endswith(".motovskikh.ru")
    ):
        raise ValueError("magic link must be an HTTPS URL on motovskikh.ru")
    raw_code = next(
        (
            item.removeprefix("code=")
            for item in parsed.query.split("&")
            if item.startswith("code=")
        ),
        "",
    )
    value = urllib.parse.unquote(raw_code)
    if not value:
        raise ValueError("magic link does not contain a verification code")
    return value


def verify_or_register(
    opener: urllib.request.OpenerDirector,
    code: str,
    nickname: str,
) -> None:
    try:
        post_json(opener, "/api/v1/auth/verify", {"code": code})
        return
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise RuntimeError(
                "Код подтверждения недействителен или уже использован"
            ) from error
        if error.code == 500:
            raise RuntimeError(
                "Сайт вернул внутреннюю ошибку при проверке кода"
            ) from error
    post_json(
        opener,
        "/api/v1/auth/acquaint",
        {"code": code, "nickname": nickname},
    )


def load_session(
    cookie_path: Path,
) -> tuple[urllib.request.OpenerDirector, http.cookiejar.MozillaCookieJar]:
    cookies = http.cookiejar.MozillaCookieJar(cookie_path)
    if cookie_path.exists():
        cookies.load(ignore_discard=True, ignore_expires=True)
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookies)
    )
    return opener, cookies


def websocket_cookie_header(cookies: http.cookiejar.CookieJar) -> str:
    now = time.time()
    return "; ".join(
        f"{cookie.name}={cookie.value}"
        for cookie in cookies
        if cookie.domain.lstrip(".") == "motovskikh.ru"
        and cookie.secure
        and not cookie.is_expired(now)
        and WS_PATH.startswith(cookie.path)
    )


def send_action(socket: websocket.WebSocket, action: str, data: object = None) -> None:
    message: dict[str, object] = {"a": action}
    if data is not None:
        message["d"] = data
    socket.send(json.dumps(message, ensure_ascii=False))


def initialize_room(
    opener: urllib.request.OpenerDirector,
    test_slug: str,
    room_id: str,
) -> None:
    post_json(
        opener,
        "/api/v2/get_map",
        {
            "name": test_slug,
            "language": "ru",
            "room": room_id,
            "workshop": test_slug.startswith("workshop/"),
        },
    )


def connect_room(
    cookies: http.cookiejar.CookieJar,
    test_slug: str,
    room_id: str,
    timeout: float = 2,
) -> websocket.WebSocket:
    query = urllib.parse.urlencode({"r": room_id, "g": test_slug})
    return websocket.create_connection(
        f"{WS_BASE_URL}?{query}",
        cookie=websocket_cookie_header(cookies),
        origin=BASE_URL,
        timeout=timeout,
    )
