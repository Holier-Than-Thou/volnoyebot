import unittest

from telethon import types

from games import private_commands


class PrivateCommandsTests(unittest.TestCase):
    def test_menu_and_help_use_same_slash_commands(self) -> None:
        menu_names = [command.command for command in private_commands.telegram_commands()]
        help_message = private_commands.help_text()

        self.assertEqual(
            menu_names,
            ["start", "help", "motovskikh_auth"],
        )
        for name in menu_names:
            self.assertIn(f"/{name} —", help_message)

    def test_telegram_commands_have_valid_type(self) -> None:
        self.assertTrue(
            all(
                isinstance(command, types.BotCommand)
                for command in private_commands.telegram_commands()
            )
        )

    def test_recognizes_only_supported_private_commands(self) -> None:
        self.assertTrue(private_commands.is_private_slash_command("/start"))
        self.assertTrue(
            private_commands.is_private_slash_command("/start payload")
        )
        self.assertTrue(
            private_commands.is_private_slash_command("/help@test_volnoyebot")
        )
        self.assertFalse(
            private_commands.is_private_slash_command("/help unexpected")
        )
        self.assertFalse(private_commands.is_private_slash_command("/unknown"))

    def test_help_contains_group_command_sections(self) -> None:
        message = private_commands.help_text()

        self.assertIn("каз баланс", message)
        self.assertIn("кости <ставка>", message)
        self.assertIn("музей помощь", message)
        self.assertIn("ферма собрать", message)
        self.assertIn("зг старт", message)


class InstallCommandMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_installs_menu_for_private_chats(self) -> None:
        requests = []

        async def client(request):
            requests.append(request)

        await private_commands.install_command_menu(client)

        self.assertEqual(len(requests), 1)
        self.assertIsInstance(requests[0].scope, types.BotCommandScopeUsers)
        self.assertEqual(
            [command.command for command in requests[0].commands],
            ["start", "help", "motovskikh_auth"],
        )


if __name__ == "__main__":
    unittest.main()
