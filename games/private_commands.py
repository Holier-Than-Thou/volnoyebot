"""Private-chat command menu, greeting, and user help."""

from __future__ import annotations

import re
from dataclasses import dataclass

from telethon import events, functions, types


@dataclass(frozen=True)
class SlashCommand:
    name: str
    description: str


# This is the single source for both Telegram's slash menu and /help.
SLASH_COMMANDS = (
    SlashCommand("start", "Приветствие и краткое описание бота"),
    SlashCommand("help", "Список пользовательских команд"),
    SlashCommand("motovskikh_auth", "Привязать аккаунт Motovskikh Tests"),
)

GROUP_COMMAND_SECTIONS = (
    (
        "🎰 Казино и баланс",
        (
            "каз помощь — подробная справка",
            "каз баланс — баланс и ресурсы",
            "каз <сумма> / каз ва-банк — ставка в слоте",
            "каз макс <сумма> / каз макс нет — личный предел ставки",
            "каз дать <сумма> — перевод ответом на сообщение",
            "каз призы / каз топ / каз лог / каз аналитика",
        ),
    ),
    (
        "🎲 Игры между пользователями",
        (
            "кости <ставка> — вызов ответом на сообщение соперника",
            "+ или да — принять адресованный вам вызов",
        ),
    ),
    (
        "🏛 Музей",
        (
            "музей — показать экспозицию",
            "музей помощь — правила и формулы",
            "музей создать <золото> Б/Г/В — создать статую",
        ),
    ),
    (
        "🐾 Ферма",
        (
            "ферма — показать питомцев",
            "ферма собрать — забрать накопленный доход",
            "ферма дать / приют / переименовать / скрестить",
        ),
    ),
    (
        "🔊 Угадай звук",
        (
            "зг — справка по игре",
            "зг старт — начать раунд",
            "зг инфо / зг топ — статистика",
        ),
    ),
)


def is_private_slash_command(text: str) -> bool:
    return bool(
        re.match(
            r"(?i)^(?:/start(?:@\w+)?(?:\s+.*)?|"
            r"/(?:help|motovskikh_auth)(?:@\w+)?\s*)$",
            text.strip(),
        )
    )


def welcome_text() -> str:
    return (
        "👋 Привет! Я игровой бот сообщества.\n\n"
        "В групповых чатах я веду баланс, казино, игры между пользователями, "
        "музей, ферму и игру «Угадай звук».\n\n"
        "В личном чате можно привязать аккаунт Motovskikh Tests для будущих "
        "онлайн-соревнований.\n\n"
        "Используйте /help, чтобы открыть список пользовательских команд."
    )


def help_text() -> str:
    lines = ["📖 Пользовательские команды", "", "Личный чат"]
    lines.extend(
        f"/{command.name} — {command.description}"
        for command in SLASH_COMMANDS
    )
    lines.extend(("", "Групповые чаты"))
    for title, commands in GROUP_COMMAND_SECTIONS:
        lines.extend(("", title))
        lines.extend(commands)
    lines.extend(
        (
            "",
            "Команды групповых игр работают только после активации бота "
            "администратором чата.",
        )
    )
    return "\n".join(lines)


def telegram_commands() -> list[types.BotCommand]:
    return [
        types.BotCommand(command.name, command.description)
        for command in SLASH_COMMANDS
    ]


async def install_command_menu(client) -> None:
    """Install commands only for one-to-one chats with the bot."""
    await client(
        functions.bots.SetBotCommandsRequest(
            scope=types.BotCommandScopeUsers(),
            lang_code="",
            commands=telegram_commands(),
        )
    )


def register(client) -> None:
    @client.on(
        events.NewMessage(pattern=r"(?i)^/start(?:@\w+)?(?:\s+.*)?$")
    )
    async def start_command(event) -> None:
        if event.is_private:
            await event.reply(welcome_text(), parse_mode=None)

    @client.on(events.NewMessage(pattern=r"(?i)^/help(?:@\w+)?\s*$"))
    async def help_command(event) -> None:
        if event.is_private:
            await event.reply(help_text(), parse_mode=None)
