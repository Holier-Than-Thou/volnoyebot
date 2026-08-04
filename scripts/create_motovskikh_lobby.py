"""Create a private Motovskikh Tests lobby and keep a spectator connected."""

from __future__ import annotations

import argparse
import getpass
import http.cookiejar
import json
import time
import urllib.request
from pathlib import Path

import websocket

from games.motovskikh_client import (
    connect_room,
    create_private_lobby_url,
    create_private_room_id,
    extract_verification_code,
    initialize_room,
    is_authenticated,
    load_session,
    normalize_test_slug,
    post_json,
    refresh_authentication,
    send_action,
    verify_or_register,
)


DEFAULT_EMAIL = "evilomom@gmail.com"
DEFAULT_NICKNAME = "Тестовый бот"
HELLO_INTERVAL_SECONDS = 15
RECONNECT_DELAY_SECONDS = 5
COOKIE_FILE = Path(__file__).resolve().parents[1] / "data" / "motovskikh.cookies.txt"


def authenticate(
    opener: urllib.request.OpenerDirector,
    cookies: http.cookiejar.MozillaCookieJar,
    email: str,
    nickname: str,
) -> None:
    if is_authenticated(opener):
        print("Сохранённая сессия авторизации действительна.")
        return
    try:
        refresh_authentication(opener, cookies)
    except RuntimeError:
        pass
    else:
        print("Сохранённая сессия авторизации обновлена.")
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


def observe_room(
    test_slug: str,
    room_id: str,
    nickname: str,
    cookies: http.cookiejar.CookieJar,
) -> None:
    while True:
        socket: websocket.WebSocket | None = None
        try:
            socket = connect_room(cookies, test_slug, room_id, timeout=5)
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
    opener, cookies = load_session(COOKIE_FILE)
    authenticate(opener, cookies, args.email, args.nickname)

    room_id = create_private_room_id()
    room_url = create_private_lobby_url(test_slug, room_id)
    initialize_room(opener, test_slug, room_id)
    print(f"Адрес приватной комнаты: {room_url}")
    observe_room(test_slug, room_id, args.nickname, cookies)


if __name__ == "__main__":
    main()
