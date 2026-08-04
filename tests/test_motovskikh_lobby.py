import unittest
from unittest.mock import patch

from scripts.create_motovskikh_lobby import create_private_lobby_url


class CreateMotovskikhLobbyTests(unittest.TestCase):
    @patch("scripts.create_motovskikh_lobby.secrets.choice", return_value="a")
    def test_creates_private_room_identifier(self, _choice) -> None:
        url = create_private_lobby_url("/moscow/")

        self.assertEqual(url, "https://motovskikh.ru/moscow/#aaaaaaaaaaa")

    def test_rejects_empty_test_slug(self) -> None:
        with self.assertRaises(ValueError):
            create_private_lobby_url(" / ")


if __name__ == "__main__":
    unittest.main()
