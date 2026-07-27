"""Telegram-сценарий игры «Угадай звук»."""

from __future__ import annotations

import asyncio
import io
import random
import re
import secrets
from pathlib import Path

import mutagen
from telethon import events
from telethon.tl import types
from telethon.tl.types import Channel, Chat, User

from .models import GuessOption, Round
from .rules import (
    MAX_OPTION_LENGTH,
    MAX_UNIQUE_OPTIONS,
    clean_guess,
    normalize_guess,
    update_poll_vote,
    voters_grouped_by_option,
    winning_users,
)
from .storage import GuessSoundStore


GUESS_SECONDS = 60
VOTE_SECONDS = 60
NONE_OPTION = "Никто не угадал"
BST_CATEGORIES = {
    "fx-o": "Предметы и бытовая техника",
    "fx-v": "Транспорт",
    "fx-m": "Механизмы, двигатели и машины",
    "fx-h": "Человеческие звуки и действия",
    "fx-a": "Животные",
    "fx-n": "Природные явления и взрывы",
}

HELP_TEXT = """🔊 Игра «Угадай звук»

зг старт — начать раунд со случайной тематикой
зг старт <BST-ID> — выбрать тематику (только администратор)
зг инфо — ваша статистика
зг инфо (ответом) — статистика пользователя
зг топ — лучшие игроки чата
зг стоп — отменить раунд (только администратор)

После появления аудио ответьте на него своим предположением. Принимается
первый ответ пользователя. На ответы и голосование даётся по одной минуте."""


def register(
    client,
    provider,
    database_path: Path,
    display_name,
    get_admin_id,
) -> None:
    """Подключить игру к Telegram-клиенту."""
    store = GuessSoundStore(database_path)
    rounds: dict[int, Round] = {}
    polls_by_id: dict[int, Round] = {}
    start_lock = asyncio.Lock()

    async def start_round(
        event,
        sender: User,
        chat: Chat | Channel,
        category: str | None = None,
    ) -> None:
        chat_id = event.chat_id
        async with start_lock:
            current = rounds.get(chat_id)
            if current is not None:
                stage = "приём ответов" if current.phase == "guessing" else "голосование"
                await event.reply(f"В этом чате уже идёт раунд: {stage}.")
                return

            try:
                used_ids = await store.used_ids(chat_id, "freesound")
                selected_category = category or secrets.choice(
                    tuple(BST_CATEGORIES)
                )
                sound = await provider.get_sound(
                    used_ids,
                    category=selected_category,
                )
                preview = await provider.download_preview(sound)
                preview = _remove_metadata(preview)
            except Exception as error:
                await event.reply(f"Не удалось подготовить звук: {error}")
                return

            await event.reply(
                "🔊 Угадайте звук!\n"
                "Ответьте на аудиосообщение одним предположением. "
                "Принимается первый ответ пользователя. Время: 1 минута."
            )
            stream = io.BytesIO(preview)
            stream.name = "audio.mp3"
            try:
                audio_message = await client.send_file(
                    chat,
                    stream,
                    force_document=False,
                    mime_type="audio/mpeg",
                    attributes=[
                        types.DocumentAttributeAudio(
                            duration=max(1, round(sound.duration)),
                            voice=False,
                        ),
                        types.DocumentAttributeFilename("audio.mp3"),
                    ],
                    reply_to=event.message.id,
                )
            except Exception:
                await event.reply("Не удалось отправить аудио. Раунд не начат.")
                return

            await store.mark_used(chat_id, sound.provider, sound.external_id)
            round_state = Round(
                chat_id=chat_id,
                sound=sound,
                audio_message_id=audio_message.id,
                topic_id=_topic_id(audio_message) or _topic_id(event.message),
            )
            rounds[chat_id] = round_state
            round_state.task = asyncio.create_task(
                finish_guessing(round_state, chat)
            )

    async def finish_guessing(
        round_state: Round, chat: Chat | Channel
    ) -> None:
        try:
            await asyncio.sleep(GUESS_SECONDS)
            if rounds.get(round_state.chat_id) is not round_state:
                return
            round_state.phase = "voting"

            options = list(round_state.options.values())
            if not options:
                await client.send_message(
                    chat,
                    "Время вышло. Ответов нет, раунд отменён.\n"
                    f"Источник: {round_state.sound.source_url}",
                    reply_to=round_state.topic_id,
                )
                rounds.pop(round_state.chat_id, None)
                return

            description = round_state.sound.description
            if len(description) > 3500:
                description = f"{description[:3497]}..."
            await client.send_message(
                chat,
                f"⏱ Приём ответов завершён.\n\nОписание звука:\n{description}",
                parse_mode=None,
                reply_to=round_state.topic_id,
            )

            poll_answers = [
                types.PollAnswer(
                    text=types.TextWithEntities(option.text, []),
                    option=index.to_bytes(1, "big"),
                )
                for index, option in enumerate(options)
            ]
            none_index = len(poll_answers)
            poll_answers.append(
                types.PollAnswer(
                    text=types.TextWithEntities(NONE_OPTION, []),
                    option=none_index.to_bytes(1, "big"),
                )
            )
            poll = types.Poll(
                id=random.getrandbits(63),
                question=types.TextWithEntities(
                    "Какой вариант ближе всего к описанию?", []
                ),
                answers=poll_answers,
                hash=0,
                public_voters=True,
                multiple_choice=True,
                close_period=VOTE_SECONDS,
            )
            round_state.poll_id = poll.id
            round_state.poll_option_indexes = {
                answer.option: index
                for index, answer in enumerate(poll_answers)
            }
            polls_by_id[poll.id] = round_state
            try:
                await client.send_file(
                    chat,
                    types.InputMediaPoll(poll=poll),
                    reply_to=round_state.topic_id,
                )
            except Exception:
                polls_by_id.pop(poll.id, None)
                raise
            await asyncio.sleep(VOTE_SECONDS + 2)

            voters_by_option = voters_grouped_by_option(
                round_state.poll_votes_by_user
            )
            winners = winning_users(options, voters_by_option)
            players = {
                user_id: option.author_names[user_id]
                for option in options
                for user_id in option.author_ids
            }
            await store.record_round(round_state.chat_id, players, winners)

            if winners:
                winner_names = sorted(players[user_id] for user_id in winners)
                result = "🏆 Победители: " + ", ".join(winner_names)
            else:
                result = "В этом раунде победителей нет."
            await client.send_message(
                chat,
                f"{result}\nИсточник: {round_state.sound.source_url}",
                parse_mode=None,
                reply_to=round_state.topic_id,
            )
            polls_by_id.pop(round_state.poll_id, None)
            rounds.pop(round_state.chat_id, None)
        except asyncio.CancelledError:
            if round_state.poll_id is not None:
                polls_by_id.pop(round_state.poll_id, None)
            raise
        except Exception as error:
            if round_state.poll_id is not None:
                polls_by_id.pop(round_state.poll_id, None)
            rounds.pop(round_state.chat_id, None)
            await client.send_message(
                chat,
                "Не удалось завершить раунд и обработать голоса. "
                "Результат не записан в статистику.\n"
                f"Ошибка: {type(error).__name__}: {error}\n"
                f"Источник: {round_state.sound.source_url}",
                parse_mode=None,
                reply_to=round_state.topic_id,
            )

    @client.on(events.Raw)
    async def collect_poll_vote(update) -> None:
        """Накопить актуальный множественный выбор из событий Telegram."""
        if not isinstance(update, types.UpdateMessagePollVote):
            return
        if not isinstance(update.peer, types.PeerUser):
            return
        round_state = polls_by_id.get(update.poll_id)
        if round_state is None:
            return
        selected_options = {
            round_state.poll_option_indexes[option]
            for option in update.options
            if option in round_state.poll_option_indexes
        }
        update_poll_vote(
            round_state.poll_votes_by_user,
            update.peer.user_id,
            selected_options,
        )

    @client.on(events.NewMessage(pattern=r"(?i)^зг(?:\s+(.+))?\s*$"))
    async def guess_sound_command(event) -> None:
        if not event.is_group:
            await event.reply("Игра работает только в групповых чатах.")
            return
        sender = await event.get_sender()
        chat = await event.get_chat()
        if not isinstance(sender, User) or not isinstance(chat, (Chat, Channel)):
            return

        command = (event.pattern_match.group(1) or "").strip().lower()
        if not command:
            await event.reply(HELP_TEXT)
            return
        command_parts = command.split()
        if command_parts and command_parts[0] == "старт":
            if len(command_parts) > 2:
                await event.reply("Формат: `зг старт` или `зг старт fx-o`.")
                return
            category = command_parts[1] if len(command_parts) == 2 else None
            if category is not None:
                if sender.id != get_admin_id():
                    await event.reply(
                        "Выбирать BST-категорию может только администратор."
                    )
                    return
                if category not in BST_CATEGORIES:
                    allowed = ", ".join(BST_CATEGORIES)
                    await event.reply(
                        f"Неизвестная BST-категория. Доступны: {allowed}."
                    )
                    return
            await start_round(event, sender, chat, category)
            return
        if command == "стоп":
            if sender.id != get_admin_id():
                await event.reply("Эта команда доступна только администратору.")
                return
            round_state = rounds.pop(event.chat_id, None)
            if round_state is None:
                await event.reply("В этом чате нет активного раунда.")
                return
            if isinstance(round_state.task, asyncio.Task):
                round_state.task.cancel()
            if round_state.poll_id is not None:
                polls_by_id.pop(round_state.poll_id, None)
            await event.reply(
                "Раунд отменён. Его ответы не учитываются в статистике."
            )
            return
        if command == "инфо":
            target = sender
            if event.is_reply:
                replied = await event.get_reply_message()
                replied_sender = await replied.get_sender()
                if isinstance(replied_sender, User):
                    target = replied_sender
            row = await store.info(event.chat_id, target.id)
            games = int(row["games"]) if row else 0
            wins = int(row["wins"]) if row else 0
            win_rate = wins / games * 100 if games else 0
            await event.reply(
                f"🔊 {display_name(target)}\n"
                f"Игр: {games}\nПобед: {wins}\n"
                f"Процент побед: {win_rate:.1f}%"
            )
            return
        if command == "топ":
            rows = await store.top(event.chat_id)
            if not rows:
                await event.reply("Статистика игры пока пуста.")
                return
            lines = ["🏆 Топ игры «Угадай звук»:"]
            for index, row in enumerate(rows, 1):
                lines.append(
                    f"{index}. {row['display_name']} — {row['wins']} побед, "
                    f"{float(row['win_rate']) * 100:.1f}%"
                )
            await event.reply("\n".join(lines))
            return
        await event.reply("Неизвестная команда. Используйте `зг` для справки.")

    @client.on(events.NewMessage)
    async def collect_guess(event) -> None:
        if not event.is_group or not event.is_reply or not event.raw_text:
            return
        if re.match(r"(?i)^зг(?:\s|$)", event.raw_text.strip()):
            return
        round_state = rounds.get(event.chat_id)
        if round_state is None or round_state.phase != "guessing":
            return
        replied = await event.get_reply_message()
        if replied is None or replied.id != round_state.audio_message_id:
            return
        sender = await event.get_sender()
        if not isinstance(sender, User) or sender.bot:
            return
        if sender.id in round_state.guesses_by_user:
            return

        text = clean_guess(event.raw_text)
        if not text or len(text) > MAX_OPTION_LENGTH:
            await event.reply(
                f"Ответ должен содержать от 1 до {MAX_OPTION_LENGTH} символов."
            )
            return
        normalized = normalize_guess(text)
        if not normalized:
            await event.reply("Ответ должен содержать не только знаки препинания.")
            return

        option = round_state.options.get(normalized)
        added_new_option = option is None
        if option is None:
            if len(round_state.options) >= MAX_UNIQUE_OPTIONS:
                await event.reply(
                    "Все 11 вариантов голосования уже заняты. "
                    "Новые уникальные ответы не принимаются."
                )
                return
            option = GuessOption(text=text)
            round_state.options[normalized] = option

        name = display_name(sender)
        option.author_ids.add(sender.id)
        option.author_names[sender.id] = name
        round_state.guesses_by_user[sender.id] = normalized
        if (
            added_new_option
            and len(round_state.options) == MAX_UNIQUE_OPTIONS
            and not round_state.limit_announced
        ):
            round_state.limit_announced = True
            await event.reply(
                "Достигнут лимит: в голосовании будет 11 уникальных вариантов. "
                "Можно присоединиться только к уже предложенному ответу."
            )


def _remove_metadata(data: bytes) -> bytes:
    """Удалить встроенные метаданные MP3 перед отправкой."""
    stream = io.BytesIO(data)
    audio = mutagen.File(stream)
    if audio is None:
        raise ValueError("Freesound вернул некорректный MP3-файл")
    if audio.tags is not None:
        audio.delete(stream)
    stream.seek(0)
    return stream.read()


def _topic_id(message) -> int | None:
    """Вернуть корневое сообщение forum topic для отложенных ответов."""
    reply_header = getattr(message, "reply_to", None)
    if reply_header is None or not getattr(reply_header, "forum_topic", False):
        return None
    return (
        getattr(reply_header, "reply_to_top_id", None)
        or getattr(reply_header, "reply_to_msg_id", None)
    )
