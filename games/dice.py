"""Игра между пользователями на нативных кубиках Telegram."""

from __future__ import annotations

import asyncio
import time

from telethon import events
from telethon.tl import types
from telethon.tl.types import Channel, Chat, MessageMediaDice, User

from .casino import is_explicit_message_reply, message_topic_id


DICE_ANIMATION_SECONDS = 3.0
CHALLENGE_TTL_SECONDS = 60


def register(client, store, display_name, schedule_delete):
    """Зарегистрировать обработчики игры в кости."""
    expiration_tasks: set[asyncio.Task] = set()

    async def expire_challenge_later(
        chat_id: int, proposal_message_id: int, delay: float
    ) -> None:
        await asyncio.sleep(max(0, delay))
        expired = await store.expire_dice_challenge(
            chat_id, proposal_message_id
        )
        if not expired:
            return
        if not await store.is_auto_delete_enabled(chat_id):
            return
        try:
            await client.delete_messages(chat_id, [proposal_message_id])
        except Exception:
            pass

    def schedule_expiration(
        chat_id: int, proposal_message_id: int, delay: float
    ) -> None:
        task = asyncio.create_task(
            expire_challenge_later(chat_id, proposal_message_id, delay)
        )
        expiration_tasks.add(task)
        task.add_done_callback(expiration_tasks.discard)

    async def restore_expiration_tasks() -> None:
        """Восстановить таймеры pending-вызовов после перезапуска."""
        now = time.time()
        for challenge in await store.get_pending_dice_challenges():
            elapsed = now - challenge["created_at"]
            schedule_expiration(
                challenge["chat_id"],
                challenge["proposal_message_id"],
                CHALLENGE_TTL_SECONDS - elapsed,
            )

    async def send_dice_pair(
        chat, reply_to: int, game_messages: list
    ) -> tuple[int, int]:
        first_message = await client.send_file(
            chat,
            types.InputMediaDice("🎲"),
            reply_to=reply_to,
        )
        game_messages.append(first_message)
        second_message = await client.send_file(
            chat,
            types.InputMediaDice("🎲"),
            reply_to=reply_to,
        )
        game_messages.append(second_message)
        messages = (first_message, second_message)
        if not all(
            isinstance(message.media, MessageMediaDice) for message in messages
        ):
            raise RuntimeError("Telegram не вернул результаты кубиков")
        await asyncio.sleep(DICE_ANIMATION_SECONDS)
        return messages[0].media.value, messages[1].media.value

    @client.on(events.NewMessage(pattern=r"(?i)^кости\s+(\d+)\s*$"))
    async def dice_challenge_command(event) -> None:
        if not event.is_group or not is_explicit_message_reply(event.message):
            return

        sender = await event.get_sender()
        chat = await event.get_chat()
        if not isinstance(sender, User) or not isinstance(chat, (Chat, Channel)):
            return
        if not await store.is_topic_enabled(
            event.chat_id, message_topic_id(event.message)
        ):
            return

        stake = int(event.pattern_match.group(1))
        if stake <= 0:
            await event.reply("Ставка должна быть больше нуля.")
            return

        replied = await event.get_reply_message()
        opponent = await replied.get_sender()
        if not isinstance(opponent, User) or opponent.bot:
            await event.reply("Вызвать на игру можно только пользователя.")
            return
        if opponent.id == sender.id:
            await event.reply("Нельзя вызвать на игру самого себя.")
            return

        challenger_name = display_name(sender)
        opponent_name = display_name(opponent)
        challenger_balance = await store.get_or_create(
            event.chat_id, sender.id, challenger_name
        )
        if challenger_balance < stake:
            await event.reply(
                f"Недостаточно очков. Текущий баланс: {challenger_balance}."
            )
            return

        proposal = await client.send_message(
            chat,
            (
                f"🎲 {challenger_name} предлагает {opponent_name} сыграть "
                f"в кости на {stake} очков.\n"
                "Чтобы принять вызов, ответьте на это сообщение «+» или «да»."
            ),
            reply_to=replied.id,
            parse_mode=None,
        )
        await store.create_dice_challenge(
            event.chat_id,
            proposal.id,
            sender.id,
            challenger_name,
            opponent.id,
            opponent_name,
            stake,
        )
        schedule_expiration(
            event.chat_id,
            proposal.id,
            CHALLENGE_TTL_SECONDS,
        )

    @client.on(events.NewMessage(pattern=r"(?i)^(?:\+|да)\s*$"))
    async def dice_challenge_accept(event) -> None:
        if not event.is_group or not is_explicit_message_reply(event.message):
            return

        sender = await event.get_sender()
        chat = await event.get_chat()
        if not isinstance(sender, User) or not isinstance(chat, (Chat, Channel)):
            return
        if not await store.is_topic_enabled(
            event.chat_id, message_topic_id(event.message)
        ):
            return

        proposal = await event.get_reply_message()
        status, challenge = await store.accept_dice_challenge(
            event.chat_id,
            proposal.id,
            sender.id,
            expires_before=time.time() - CHALLENGE_TTL_SECONDS,
        )
        if status in {"not_found", "wrong_user"}:
            return
        if status == "expired":
            if await store.is_auto_delete_enabled(event.chat_id):
                try:
                    await client.delete_messages(chat, [proposal.id])
                except Exception:
                    pass
            expired_message = await event.reply(
                "Время принятия вызова истекло."
            )
            schedule_delete(chat, expired_message)
            return
        if status == "challenger_funds":
            await event.reply(
                f"У игрока {challenge['challenger_name']} уже недостаточно очков."
            )
            return
        if status == "opponent_funds":
            await event.reply(
                f"У игрока {challenge['opponent_name']} недостаточно очков."
            )
            return

        game_messages = [proposal]
        try:
            while True:
                challenger_roll, opponent_roll = await send_dice_pair(
                    chat, event.message.id, game_messages
                )
                if challenger_roll != opponent_roll:
                    break
                tie_message = await event.reply(
                    f"🎲 Ничья: {challenger_roll}–{opponent_roll}. Перебрасываем."
                )
                game_messages.append(tie_message)
        except Exception:
            await store.refund_dice_challenge(event.chat_id, proposal.id)
            error_message = await event.reply(
                "Не удалось бросить кубики. Обе ставки возвращены."
            )
            game_messages.append(error_message)
            schedule_delete(chat, *game_messages)
            return

        if challenger_roll > opponent_roll:
            winner_id = challenge["challenger_id"]
            winner_name = challenge["challenger_name"]
        else:
            winner_id = challenge["opponent_id"]
            winner_name = challenge["opponent_name"]

        winner_balance = await store.finish_dice_challenge(
            event.chat_id, proposal.id, winner_id
        )
        await store.record_dice_game(
            event.chat_id,
            challenge,
            challenger_roll,
            opponent_roll,
        )
        bank = challenge["stake"] * 2
        result_message = await event.reply(
            f"🏆 {winner_name} победил: "
            f"{challenger_roll}–{opponent_roll}.\n"
            f"Банк: {bank} очков. Баланс победителя: {winner_balance}."
        )
        game_messages.append(result_message)
        schedule_delete(chat, *game_messages)

    return restore_expiration_tasks
