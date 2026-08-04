"""Create a private Motovskikh Tests lobby and keep a spectator connected."""

from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import secrets
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import websocket


BASE_URL = "https://motovskikh.ru"
WS_BASE_URL = "wss://motovskikh.ru/api/wsup/v1/map"
WS_PATH = "/api/wsup/v1/map"
DEFAULT_EMAIL = "evilomom@gmail.com"
DEFAULT_NICKNAME = "Тестовый бот"
PRIVATE_ROOM_ID_LENGTH = 11
HELLO_INTERVAL_SECONDS = 15
RECONNECT_DELAY_SECONDS = 5
COOKIE_FILE = Path(__file__).resolve().parents[1] / "data" / "motovskikh.cookies.txt"


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


def extract_verification_code(code_or_link: str) -> str:
    value = code_or_link.strip()
    if not value:
        raise ValueError("verification code must not be empty")

    if value.startswith(("https://", "http://")):
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
            raise RuntimeError("Код подтверждения недействителен или уже использован") from error
        if error.code == 500:
            raise RuntimeError("Сайт вернул внутреннюю ошибку при проверке кода") from error

    post_json(
        opener,
        "/api/v1/auth/acquaint",
        {"code": code, "nickname": nickname},
    )


def authenticate(
    opener: urllib.request.OpenerDirector,
    cookies: http.cookiejar.MozillaCookieJar,
    email: str,
    nickname: str,
) -> None:
    if is_authenticated(opener):
        print("Сохранённая сессия авторизации действительна.")
        return

    post_json(opener, "/api/v1/auth/hello", {"email": email})
    print(f"Волшебная ссылка отправлена на {email}.")
    code_or_link = getpass.getpass(
        "Вставьте код из письма или полную ссылку (ввод скрыт): "
    )
    code = extract_verification_code(code_or_link)
    verify_or_register(opener, code, nickname)
    if not is_authenticated(opener):
        raise RuntimeError("Сайт не подтвердил авторизацию после проверки кода")

    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    cookies.save(ignore_discard=True, ignore_expires=True)
    print(f"Сессия сохранена в {COOKIE_FILE}")


def load_session() -> tuple[urllib.request.OpenerDirector, http.cookiejar.MozillaCookieJar]:
    cookies = http.cookiejar.MozillaCookieJar(COOKIE_FILE)
    if COOKIE_FILE.exists():
        cookies.load(ignore_discard=True, ignore_expires=True)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
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


def observe_room(
    test_slug: str,
    room_id: str,
    nickname: str,
    cookies: http.cookiejar.CookieJar,
) -> None:
    query = urllib.parse.urlencode({"r": room_id, "g": test_slug})
    ws_url = f"{WS_BASE_URL}?{query}"

    while True:
        socket: websocket.WebSocket | None = None
        try:
            socket = websocket.create_connection(
                ws_url,
                cookie=websocket_cookie_header(cookies),
                origin=BASE_URL,
                timeout=5,
            )
            send_action(socket, "join")
            last_hello = time.monotonic()
            nickname_sent = False
            spectator_sent = False
            print("Наблюдатель подключён. Для остановки нажмите Ctrl+C.")

            while True:
                try:
                    raw_message = socket.recv()
                except websocket.WebSocketTimeoutException:
                    raw_message = None

                if raw_message:
                    message = json.loads(raw_message)
                    if message.get("a") == "room":
                        room = message.get("d", {})
                        if not nickname_sent:
                            send_action(socket, "greet", nickname)
                            nickname_sent = True
                        if not spectator_sent and room.get("c") != "":
                            send_action(socket, "colour", "spectator")
                            spectator_sent = True
                    elif message.get("a") == "score":
                        print("Игра окончена. Итоговое время:", message.get("d"))

                if time.monotonic() - last_hello >= HELLO_INTERVAL_SECONDS:
                    send_action(socket, "hello")
                    last_hello = time.monotonic()
        except KeyboardInterrupt:
            if socket is not None:
                socket.close()
            print("Наблюдатель остановлен.")
            return
        except (OSError, ValueError, websocket.WebSocketException) as error:
            print(
                f"Соединение потеряно ({error}). Повтор через "
                f"{RECONNECT_DELAY_SECONDS} секунд..."
            )
            time.sleep(RECONNECT_DELAY_SECONDS)
        finally:
            if socket is not None:
                socket.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a private Motovskikh lobby and join as a spectator."
    )
    parser.add_argument("test_slug", nargs="?", default="moscow")
    parser.add_argument("--email", default=DEFAULT_EMAIL)
    parser.add_argument("--nickname", default=DEFAULT_NICKNAME)
    args = parser.parse_args()

    test_slug = normalize_test_slug(args.test_slug)
    opener, cookies = load_session()
    authenticate(opener, cookies, args.email, args.nickname)

    room_id = create_private_room_id()
    room_url = create_private_lobby_url(test_slug, room_id)
    initialize_room(opener, test_slug, room_id)
    print(f"Адрес приватной комнаты: {room_url}")
    observe_room(test_slug, room_id, args.nickname, cookies)


if __name__ == "__main__":
    main()
