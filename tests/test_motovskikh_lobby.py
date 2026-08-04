import unittest
from unittest.mock import patch

from scripts.create_motovskikh_lobby import (
    build_magic_link,
    create_private_lobby_url,
)


class CreateMotovskikhLobbyTests(unittest.TestCase):
    @patch("scripts.create_motovskikh_lobby.secrets.choice", return_value="a")
    def test_creates_private_room_identifier(self, _choice) -> None:
        url = create_private_lobby_url("/moscow/")

        self.assertEqual(url, "https://motovskikh.ru/moscow/#aaaaaaaaaaa")

    def test_rejects_empty_test_slug(self) -> None:
        with self.assertRaises(ValueError):
            create_private_lobby_url(" / ")

    def test_builds_magic_link_from_code_with_special_characters(self) -> None:
        link = build_magic_link("abc+/= &")

        self.assertEqual(
            link,
            "https://motovskikh.ru/verify/?code=abc%2B%2F%3D%20%26",
        )

    def test_extracts_code_from_full_magic_link_without_losing_plus(self) -> None:
        link = build_magic_link(
            "https://motovskikh.ru/verify/?code=abc+def%2Fghi%3D"
        )

        self.assertEqual(
            link,
            "https://motovskikh.ru/verify/?code=abc%2Bdef%2Fghi%3D",
        )

    def test_rejects_magic_link_from_another_host(self) -> None:
        with self.assertRaises(ValueError):
            build_magic_link("https://example.com/verify/?code=secret")


if __name__ == "__main__":
    unittest.main()
