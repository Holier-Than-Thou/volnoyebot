import unittest
from http.cookiejar import Cookie, CookieJar
from unittest.mock import patch

from scripts.create_motovskikh_lobby import (
    create_private_lobby_url,
    extract_verification_code,
    websocket_cookie_header,
)


class CreateMotovskikhLobbyTests(unittest.TestCase):
    @patch("scripts.create_motovskikh_lobby.secrets.choice", return_value="a")
    def test_creates_private_room_identifier(self, _choice) -> None:
        url = create_private_lobby_url("/moscow/")

        self.assertEqual(url, "https://motovskikh.ru/moscow/#aaaaaaaaaaa")

    def test_rejects_empty_test_slug(self) -> None:
        with self.assertRaises(ValueError):
            create_private_lobby_url(" / ")

    def test_accepts_code_with_special_characters(self) -> None:
        code = extract_verification_code("abc+/= &")

        self.assertEqual(code, "abc+/= &")

    def test_extracts_code_from_full_magic_link_without_losing_plus(self) -> None:
        code = extract_verification_code(
            "https://motovskikh.ru/verify/?code=abc+def%2Fghi%3D"
        )

        self.assertEqual(code, "abc+def/ghi=")

    def test_rejects_magic_link_from_another_host(self) -> None:
        with self.assertRaises(ValueError):
            extract_verification_code("https://example.com/verify/?code=secret")

    def test_websocket_cookie_header_respects_cookie_path(self) -> None:
        cookies = CookieJar()
        for name, path in (("access", "/"), ("refresh", "/api/v1/auth/refresh")):
            cookies.set_cookie(
                Cookie(
                    version=0,
                    name=name,
                    value="value",
                    port=None,
                    port_specified=False,
                    domain="motovskikh.ru",
                    domain_specified=False,
                    domain_initial_dot=False,
                    path=path,
                    path_specified=True,
                    secure=True,
                    expires=None,
                    discard=True,
                    comment=None,
                    comment_url=None,
                    rest={},
                )
            )

        self.assertEqual(websocket_cookie_header(cookies), "access=value")


if __name__ == "__main__":
    unittest.main()
