"""Create an invitation link for a private Motovskikh Tests lobby."""

from __future__ import annotations

import argparse
import secrets
import string
from urllib.parse import quote


BASE_URL = "https://motovskikh.ru"
PRIVATE_ROOM_ID_LENGTH = 11


def create_private_lobby_url(test_slug: str) -> str:
    normalized_slug = test_slug.strip().strip("/")
    if not normalized_slug:
        raise ValueError("test slug must not be empty")

    safe_slug = quote(normalized_slug, safe="/")
    room_id = "".join(
        secrets.choice(string.ascii_lowercase)
        for _ in range(PRIVATE_ROOM_ID_LENGTH)
    )
    return f"{BASE_URL}/{safe_slug}/#{room_id}"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a private invitation link for Motovskikh Tests."
    )
    parser.add_argument(
        "test_slug",
        nargs="?",
        default="moscow",
        help="test path, for example 'moscow' or 'anatomy/brain' (default: moscow)",
    )
    args = parser.parse_args()
    print(create_private_lobby_url(args.test_slug))


if __name__ == "__main__":
    main()
