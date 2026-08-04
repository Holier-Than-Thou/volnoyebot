"""Эмуляция дополнительных игроков без отдельных Telegram-аккаунтов.

Администратор выполняет обычную команду от лица персоны, добавляя префикс
`эмул <имя>`. Личность подменяется только на время этой команды: все игры
хранят данные по паре `(chat_id, user_id)`, поэтому персона получает свой
баланс, ферму, музей и журнал. Ответ на сообщение персоны адресует команду
именно ей, что позволяет проверять парные сценарии в одиночку.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from telethon.tl.types import User

from .emulation_storage import PLAYER_ID_BASE


PREFIX = "эмул"
# Персона выполняет команду: «эмул Вася каз ставка 100».
COMMAND_PATTERN = re.compile(
    rf"(?iu)^\s*{PREFIX}\s+(?P<head>\S+)(?:\s+(?P<rest>\S.*?))?\s*$",
    re.DOTALL,
)
BARE_PATTERN = re.compile(rf"(?iu)^\s*{PREFIX}\s*$")
NAME_PATTERN = re.compile(r"(?u)^[^\s@/]{1,32}$")
MENTION_PATTERN = re.compile(r"(?u)(?<!\S)@([^\s@/]{1,32})")

HELP_SUBCOMMANDS = {"помощь", "справка"}
LIST_SUBCOMMANDS = {"список", "игроки"}
CREATE_SUBCOMMANDS = {"создать", "добавить"}
DELETE_SUBCOMMANDS = {"удалить", "убрать"}

NAME_REQUIREMENTS = (
    "Имя персоны — одно слово до 32 символов без пробелов, «@» и «/»."
)

HELP_TEXT = (
    "🧪 Эмуляция игроков (только тестовый контур, только администратор)\n\n"
    f"`{PREFIX} список` — персоны чата с балансами;\n"
    f"`{PREFIX} создать Вася` — создать персону;\n"
    f"`{PREFIX} удалить Вася` — удалить персону, её баланс и ресурсы;\n"
    f"`{PREFIX} Вася <команда>` — выполнить команду от лица персоны.\n\n"
    "Примеры:\n"
    f"`{PREFIX} Вася каз ставка 100`\n"
    f"`{PREFIX} Вася ферма собрать`\n\n"
    "Ответ на сообщение с командой персоны адресует ей другую команду: "
    "ответьте на него `кости 500`, чтобы вызвать персону на игру, или "
    "`каз дать 100`, чтобы передать ей очки.\n\n"
    "Дуэль между персонами вызывается по имени соперника:\n"
    f"`{PREFIX} Вася мот 100 @Петя <ссылка на тест>`\n\n"
    "Привязка аккаунта Motovskikh для персоны — в личном чате с ботом:\n"
    f"`{PREFIX} Вася /motovskikh_auth`\n"
    "Там же можно указать персону её идентификатором, если одно имя занято "
    "в нескольких чатах."
)


@dataclass(frozen=True)
class ParsedCommand:
    """Разобранная команда префикса «эмул»."""

    kind: str
    name: str = ""
    rest: str = ""
    offset: int = 0
    error: str = ""


@dataclass(frozen=True)
class InterceptResult:
    """Что делать с сообщением после разбора префикса."""

    stop: bool = False
    actor: User | None = None


def is_emulated(user_id: int) -> bool:
    """Проверить, что идентификатор принадлежит эмулируемому игроку."""
    return user_id <= PLAYER_ID_BASE


def emulated_user(user_id: int, name: str) -> User:
    """Собрать объект пользователя, подставляемый вместо отправителя."""
    return User(id=user_id, bot=False, first_name=name)


def utf16_length(text: str) -> int:
    """Длина текста в единицах UTF-16, как её считает Telegram."""
    return len(text.encode("utf-16-le")) // 2


def parse_command(text: str) -> ParsedCommand | None:
    """Разобрать сообщение с префиксом «эмул»."""
    if BARE_PATTERN.match(text or ""):
        return ParsedCommand("help")
    match = COMMAND_PATTERN.match(text or "")
    if match is None:
        return None

    head = match.group("head")
    rest = match.group("rest") or ""
    offset = match.start("rest") if rest else 0
    lowered = head.casefold()

    if lowered in HELP_SUBCOMMANDS:
        return ParsedCommand("help")
    if lowered in LIST_SUBCOMMANDS:
        return ParsedCommand("list")
    if lowered in CREATE_SUBCOMMANDS or lowered in DELETE_SUBCOMMANDS:
        kind = "create" if lowered in CREATE_SUBCOMMANDS else "delete"
        if not rest:
            return ParsedCommand(
                "error",
                error=f"Укажите имя персоны: `{PREFIX} {lowered} Вася`.",
            )
        name = rest.split(maxsplit=1)[0]
        if not NAME_PATTERN.match(name):
            return ParsedCommand("error", error=NAME_REQUIREMENTS)
        return ParsedCommand(kind, name=name)

    if not NAME_PATTERN.match(head):
        return ParsedCommand("error", error=NAME_REQUIREMENTS)
    if not rest:
        return ParsedCommand(
            "error",
            error=f"Укажите команду персоны: `{PREFIX} {head} каз баланс`.",
        )
    return ParsedCommand("act", name=head, rest=rest, offset=offset)


def rewrite_message(message, parsed: ParsedCommand) -> None:
    """Заменить текст сообщения командой без префикса «эмул».

    Обработчики Telethon применяют свои шаблоны к тексту сообщения уже после
    возврата из этого обработчика, поэтому подмена делает команду персоны
    неотличимой от обычной.
    """
    original = message.message or ""
    shift = utf16_length(original[: parsed.offset])
    limit = utf16_length(parsed.rest)
    entities = []
    for entity in message.entities or []:
        moved = entity.offset - shift
        if moved < 0 or moved + entity.length > limit:
            continue
        entity.offset = moved
        entities.append(entity)
    # Публичный сеттер сбрасывает кэш форматированного текста и разметку.
    message.raw_text = parsed.rest
    message.entities = entities or None


def format_players(rows) -> str:
    """Составить ответ на команду `эмул список`."""
    if not rows:
        return (
            "Персон пока нет. "
            f"Создайте первую командой `{PREFIX} создать Вася`."
        )
    lines = [
        f"• {row['name']} — {row['balance']} очков, ID `{row['user_id']}`"
        if row["has_balance"]
        else f"• {row['name']} — ещё не играла, ID `{row['user_id']}`"
        for row in rows
    ]
    return "🧪 Эмулируемые игроки:\n" + "\n".join(lines)


def set_actor(message, actor: User) -> None:
    """Пометить сообщение как отправленное от лица персоны."""
    message.emulated_actor = actor


def get_actor(message) -> User | None:
    """Прочитать персону, назначенную сообщению."""
    return getattr(message, "emulated_actor", None)


def has_prefix(text: str) -> bool:
    """Проверить, что сообщение адресовано подсистеме эмуляции."""
    return parse_command(text) is not None


def parse_player_id(token: str) -> int | None:
    """Распознать ссылку на персону по её числовому идентификатору."""
    try:
        value = int(token)
    except ValueError:
        return None
    return value if is_emulated(value) else None


async def resolve_persona(store, chat_id: int | None, token: str):
    """Найти персону по имени или идентификатору.

    В групповом чате поиск идёт только среди персон этого чата. В личном чате
    с ботом чата нет, поэтому имя ищется во всех чатах и должно быть
    однозначным; неоднозначность разрешается числовым идентификатором.
    """
    player_id = parse_player_id(token)
    if player_id is not None:
        row = await store.find_emulated_player_by_id(player_id)
        if row is None:
            return "missing", []
        if chat_id is not None and row["chat_id"] != chat_id:
            return "missing", []
        return "found", [row]

    if chat_id is not None:
        row = await store.get_emulated_player_by_name(chat_id, token)
        return ("found", [row]) if row is not None else ("missing", [])

    rows = await store.find_emulated_players_by_name(token)
    if not rows:
        return "missing", []
    if len(rows) > 1:
        return "ambiguous", rows
    return "found", rows


async def mentioned_persona(store, chat_id: int, text: str) -> User | None:
    """Найти персону, упомянутую в команде как «@Имя».

    Telegram не создаёт сущность упоминания для кириллических имён, поэтому
    текст разбирается самостоятельно.
    """
    for name in MENTION_PATTERN.findall(text or ""):
        row = await store.get_emulated_player_by_name(chat_id, name)
        if row is not None:
            return emulated_user(row["user_id"], row["name"])
    return None


async def event_sender(event):
    """Получить действующее лицо команды: персону либо реального автора."""
    actor = get_actor(event.message)
    if actor is not None:
        return actor
    return await event.get_sender()


async def message_sender(store, chat_id: int, message):
    """Определить игрока, которому принадлежит сообщение-адресат."""
    row = await store.get_emulated_player_by_message(chat_id, message.id)
    if row is not None:
        return emulated_user(row["user_id"], row["name"])
    return await message.get_sender()


async def handle_prefix(
    event, store, admin_id: int, sender, private: bool = False
) -> InterceptResult:
    """Обработать префикс «эмул» до того, как сработают команды игр."""
    parsed = parse_command(event.raw_text or "")
    if parsed is None:
        return InterceptResult()

    if not isinstance(sender, User) or sender.id != admin_id:
        await event.reply("Эмуляция игроков доступна только администратору.")
        return InterceptResult(stop=True)

    # Персоны создаются в конкретном чате, поэтому реестром управляют оттуда.
    if private and parsed.kind in {"list", "create", "delete"}:
        await event.reply(
            "Управление персонами доступно в групповом чате. "
            f"Здесь работает только `{PREFIX} <имя> <команда>`."
        )
        return InterceptResult(stop=True)

    if parsed.kind == "help":
        await event.reply(HELP_TEXT)
        return InterceptResult(stop=True)

    if parsed.kind == "error":
        await event.reply(parsed.error)
        return InterceptResult(stop=True)

    if parsed.kind == "list":
        rows = await store.list_emulated_players(event.chat_id)
        await event.reply(format_players(rows))
        return InterceptResult(stop=True)

    if parsed.kind == "create":
        status, row = await store.create_emulated_player(
            event.chat_id, parsed.name
        )
        if status == "exists":
            await event.reply(f"Персона {row['name']} уже существует.")
        else:
            await event.reply(
                f"🧪 Персона {row['name']} создана, ID `{row['user_id']}`. "
                f"Команда от её лица: `{PREFIX} {row['name']} каз баланс`."
            )
        return InterceptResult(stop=True)

    if parsed.kind == "delete":
        row = await store.delete_emulated_player(event.chat_id, parsed.name)
        if row is None:
            await event.reply(f"Персона {parsed.name} не найдена.")
        else:
            await event.reply(
                f"🧪 Персона {row['name']} удалена вместе с балансом. "
                "Записи журнала и ферма остаются в базе."
            )
        return InterceptResult(stop=True)

    chat_id = None if private else event.chat_id
    status, rows = await resolve_persona(store, chat_id, parsed.name)
    if status == "missing":
        hint = (
            f"Создайте её командой `{PREFIX} создать {parsed.name}` "
            "в групповом чате."
            if private
            else f"Создайте её командой `{PREFIX} создать {parsed.name}`."
        )
        await event.reply(f"Персона {parsed.name} не найдена. {hint}")
        return InterceptResult(stop=True)
    if status == "ambiguous":
        listed = ", ".join(f"`{row['user_id']}`" for row in rows)
        await event.reply(
            f"Персона с именем {parsed.name} есть в нескольких чатах. "
            f"Укажите её идентификатор: {listed}."
        )
        return InterceptResult(stop=True)

    row = rows[0]
    actor = emulated_user(row["user_id"], row["name"])
    rewrite_message(event.message, parsed)
    if not private:
        # Ответ на это сообщение адресует команду персоне, а не администратору.
        await store.bind_emulated_message(
            event.chat_id, event.message.id, actor.id
        )
    set_actor(event.message, actor)
    return InterceptResult(actor=actor)
