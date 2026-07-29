"""Telegram-команды фермы питомцев."""

from __future__ import annotations

import math
import time

from telethon import events
from telethon.tl.types import Channel, Chat, User

from . import pets
from .casino import is_explicit_message_reply, message_topic_id


FARM_COMMANDS = {"ферма", "скрестить"}
MAX_PET_NAME_LENGTH = 32
PET_TRANSFER_TTL_SECONDS = 60


def register(client, handler, store) -> None:
    """Зарегистрировать общий префикс всех команд фермы."""
    client.add_event_handler(
        handler,
        events.NewMessage(pattern=r"(?iu)^ферма(?:\s+(.+))?\s*$"),
    )

    @client.on(events.NewMessage(pattern=r"(?iu)^(?:\+|да)\s*$"))
    async def accept_pet_transfer(event) -> None:
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
        status, transfer = await store.accept_pet_transfer(
            event.chat_id,
            proposal.id,
            sender.id,
            expires_before=time.time() - PET_TRANSFER_TTL_SECONDS,
        )
        if status in {"not_found", "wrong_user"}:
            return
        if status == "expired":
            await event.reply("Время принятия питомца истекло.", parse_mode=None)
            return
        if status == "full":
            await event.reply(
                "На вашей ферме нет свободного слота.",
                parse_mode=None,
            )
            return
        if status == "missing":
            await event.reply(
                "Питомец больше не доступен для передачи.",
                parse_mode=None,
            )
            return
        pet = transfer["pet"]
        kind = "яйцо" if pet.is_egg else f"питомца {pet.name}"
        await event.reply(
            f"🤝 Вы приняли {kind} от {transfer['sender_name']}.\n"
            f"Новый слот: {transfer['recipient_slot'] + 1}.",
            parse_mode=None,
        )


def earns_pet_egg(symbols: tuple[str, str, str]) -> bool:
    """Проверить награду за ровно две вишни."""
    return symbols.count("🍒") == 2


def _format_pet(pet: pets.Pet, now: float) -> str:
    slot = pet.slot_index + 1
    if pet.is_egg:
        remaining = max(0, math.ceil(pet.egg_hatch_at - now))
        if remaining:
            hours, remainder = divmod(remaining, 3600)
            minutes, seconds = divmod(remainder, 60)
            return (
                f"{slot}️⃣ 🥚 Неизвестное яйцо — до вылупления "
                f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            )
        return f"{slot}️⃣ 🥚 Неизвестное яйцо вылупляется автоматически"
    generation = "гибрид" if pet.generation else "чистопородный"
    return (
        f"{slot}️⃣ {pet.name} ({generation})\n"
        f"   💨 {pet.stench} · 🤢 {pet.ugliness} · 🍯 {pet.stickiness}\n"
        f"   Доход: {pet.income_per_second():.3f} очка/сек."
    )


async def handle_command(
    event,
    command: str,
    args: list[str],
    store,
    user_id: int,
    display_name: str,
    initial_balance: int,
) -> bool:
    """Обработать команду фермы и сообщить, была ли она распознана."""
    if command not in FARM_COMMANDS:
        return False
    chat_id = event.chat_id

    # «каз скрестить» — исторический вариант. Все команды с общим префиксом
    # проходят сюда как «ферма <подкоманда>».
    if command == "ферма" and args and args[0].casefold() == "скрестить":
        command = "скрестить"
        args = args[1:]

    if command == "ферма":
        subcommand = args[0].lower() if args else "показать"
        if subcommand in {"показать", "show"} and len(args) == 1:
            args = []
        if not args:
            snapshot = await store.get_farm(chat_id, user_id)
            pet_by_slot = {
                pet.slot_index: pet for pet in snapshot["pets"]
            }
            lines = [
                "🧬 Генетическая Мерзость",
                "Специализация: "
                f"{pets.SPEC_EMOJI[snapshot['spec']]} "
                f"{pets.SPEC_NAMES[snapshot['spec']]}",
                f"Накоплено: {snapshot['accumulated']:.1f} очков",
            ]
            total_income = sum(
                pet.income_per_second() for pet in snapshot["pets"]
            )
            hourly_income = total_income * 3600
            daily_income = total_income * 86400
            lines.extend(
                (
                    f"Общий доход: {total_income:.3f} очка/сек.",
                    "За час: ≈"
                    f"{hourly_income:,.0f}".replace(",", " "),
                    "За сутки: ≈"
                    f"{daily_income:,.0f}".replace(",", " "),
                    "",
                )
            )
            for slot in range(pets.MAX_SLOTS):
                pet = pet_by_slot.get(slot)
                lines.append(
                    _format_pet(pet, snapshot["now"])
                    if pet is not None
                    else f"{slot + 1}️⃣ Пусто"
                )
            lines.extend(
                (
                    "",
                    "ферма собрать",
                    "ферма дать N (ответом игроку)",
                    "ферма приют N",
                    "ферма переименовать N Имя",
                    "ферма скрестить N N",
                    "Также можно использовать префикс «каз ферма».",
                )
            )
            await event.reply("\n".join(lines), parse_mode=None)
            return True

        if subcommand == "приют" and len(args) == 2:
            if not args[1].isdigit():
                await event.reply("Формат: ферма приют 1", parse_mode=None)
                return True
            slot = int(args[1]) - 1
            if slot not in range(pets.MAX_SLOTS):
                await event.reply("Укажите слот от 1 до 6.", parse_mode=None)
                return True
            status = await store.shelter_pet(chat_id, user_id, slot)
            messages = {
                "ok": f"🏠 Питомец из слота {slot + 1} отдан в приют.",
                "missing": "Этот слот уже пуст.",
                "reserved": (
                    "Питомец зарезервирован для передачи. "
                    "Дождитесь истечения предложения."
                ),
            }
            await event.reply(
                messages[status],
                parse_mode=None,
            )
            return True

        if subcommand == "дать" and len(args) == 2:
            if not args[1].isdigit() or not is_explicit_message_reply(
                event.message
            ):
                await event.reply(
                    "Ответьте на сообщение получателя: ферма дать 1",
                    parse_mode=None,
                )
                return True
            slot = int(args[1]) - 1
            if slot not in range(pets.MAX_SLOTS):
                await event.reply("Укажите слот от 1 до 6.", parse_mode=None)
                return True
            replied = await event.get_reply_message()
            recipient = await replied.get_sender()
            if not isinstance(recipient, User) or recipient.bot:
                await event.reply(
                    "Передать питомца можно только пользователю.",
                    parse_mode=None,
                )
                return True
            if recipient.id == user_id:
                await event.reply(
                    "Нельзя передать питомца самому себе.",
                    parse_mode=None,
                )
                return True
            recipient_name = " ".join(
                part
                for part in (recipient.first_name, recipient.last_name)
                if part
            ) or (
                f"@{recipient.username}"
                if recipient.username
                else str(recipient.id)
            )
            proposal = await event.reply(
                f"🎁 {display_name} предлагает {recipient_name} "
                f"питомца или яйцо из слота {slot + 1}.\n"
                "Чтобы принять, ответьте на это сообщение «+» или «да» "
                f"в течение {PET_TRANSFER_TTL_SECONDS} секунд.",
                parse_mode=None,
            )
            status = await store.create_pet_transfer(
                chat_id,
                proposal.id,
                user_id,
                display_name,
                recipient.id,
                recipient_name,
                slot,
            )
            if status != "ok":
                try:
                    await proposal.delete()
                except Exception:
                    pass
                message = (
                    "Этот слот пуст."
                    if status == "missing"
                    else "Питомец уже зарезервирован для передачи."
                )
                await event.reply(message, parse_mode=None)
            return True

        if subcommand == "собрать" and len(args) == 1:
            amount, balance = await store.claim_pet_income(
                chat_id,
                user_id,
                display_name,
                initial_balance,
            )
            if amount:
                await event.reply(
                    f"🌾 Собрано: {amount} очков.\nБаланс: {balance}.",
                    parse_mode=None,
                )
            else:
                await event.reply(
                    "🌾 Пока накопилось меньше одного очка.",
                    parse_mode=None,
                )
            return True

        if subcommand == "переименовать" and len(args) >= 3:
            if not args[1].isdigit():
                await event.reply(
                    "Формат: ферма переименовать 1 Имя",
                    parse_mode=None,
                )
                return True
            slot = int(args[1]) - 1
            new_name = " ".join(args[2:]).strip()
            if slot not in range(pets.MAX_SLOTS):
                await event.reply("Укажите слот от 1 до 6.", parse_mode=None)
                return True
            if not 1 <= len(new_name) <= MAX_PET_NAME_LENGTH:
                await event.reply(
                    f"Имя должно содержать от 1 до {MAX_PET_NAME_LENGTH} символов.",
                    parse_mode=None,
                )
                return True
            renamed = await store.rename_pet(
                chat_id, user_id, slot, new_name
            )
            await event.reply(
                (
                    f"✅ Питомец в слоте {slot + 1} теперь зовётся {new_name}."
                    if renamed
                    else "В этом слоте нет взрослого питомца."
                ),
                parse_mode=None,
            )
            return True

        await event.reply(
            "Команды: ферма, ферма собрать, ферма дать N, ферма приют N, "
            "ферма переименовать N Имя, ферма скрестить N N. "
            "После «каз» доступны те же команды.",
            parse_mode=None,
        )
        return True

    if len(args) != 2 or not all(argument.isdigit() for argument in args):
        await event.reply(
            "Формат: ферма скрестить 1 2 или каз скрестить 1 2",
            parse_mode=None,
        )
        return True
    first_slot, second_slot = (int(argument) - 1 for argument in args)
    if (
        first_slot == second_slot
        or first_slot not in range(pets.MAX_SLOTS)
        or second_slot not in range(pets.MAX_SLOTS)
    ):
        await event.reply(
            "Укажите два разных слота от 1 до 6.",
            parse_mode=None,
        )
        return True
    status, child = await store.breed_pets(
        chat_id, user_id, first_slot, second_slot
    )
    if status == "missing":
        await event.reply("Оба слота должны быть заняты.", parse_mode=None)
    elif status != "ok":
        await event.reply(f"❌ {status}.", parse_mode=None)
    else:
        remaining_minutes = math.ceil(
            (child.egg_hatch_at - child.created_at) / 60
        )
        await event.reply(
            "🔬 Родители заменены новым яйцом.\n"
            f"Слот: {child.slot_index + 1}. "
            f"До вылупления: {remaining_minutes} мин.\n"
            "Гены и происхождение пока неизвестны.",
            parse_mode=None,
        )
    return True


async def award_slot_egg(event, store, chat_id: int, user_id: int) -> None:
    """Выдать награду за комбинацию ровно с двумя вишнями."""
    status, _spec, egg = await store.award_pet_egg(chat_id, user_id)
    if status == "full":
        await event.reply(
            "🍒🍒 Выпало яйцо, но на ферме нет свободного слота.",
            parse_mode=None,
        )
        return
    remaining_minutes = math.ceil((egg.egg_hatch_at - egg.created_at) / 60)
    await event.reply(
        "🍒🍒 Вы получили неизвестное яйцо!\n"
        f"Слот: {egg.slot_index + 1}. "
        f"До вылупления: {remaining_minutes} мин.",
        parse_mode=None,
    )
