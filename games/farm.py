"""Telegram-команды фермы питомцев."""

from __future__ import annotations

import math

from . import pets


FARM_COMMANDS = {"ферма", "скрестить"}
MAX_PET_NAME_LENGTH = 32


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
                f"{slot}️⃣ 🥚 Яйцо — до вылупления "
                f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            )
        return f"{slot}️⃣ 🥚 Яйцо созрело — каз ферма вылупить {slot}"
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
                "",
            ]
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
                    "каз ферма собрать",
                    "каз ферма вылупить N",
                    "каз ферма переименовать N Имя",
                    "каз скрестить N N",
                )
            )
            await event.reply("\n".join(lines), parse_mode=None)
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

        if subcommand == "вылупить" and len(args) == 2 and args[1].isdigit():
            slot = int(args[1]) - 1
            if slot not in range(pets.MAX_SLOTS):
                await event.reply("Укажите слот от 1 до 4.", parse_mode=None)
                return True
            status = await store.hatch_pet_egg(chat_id, user_id, slot)
            messages = {
                "ok": f"🐣 Яйцо в слоте {slot + 1} вылупилось!",
                "early": "🥚 Яйцо ещё не созрело.",
                "not_egg": "В этом слоте нет яйца.",
            }
            await event.reply(messages[status], parse_mode=None)
            return True

        if subcommand == "переименовать" and len(args) >= 3:
            if not args[1].isdigit():
                await event.reply(
                    "Формат: каз ферма переименовать 1 Имя",
                    parse_mode=None,
                )
                return True
            slot = int(args[1]) - 1
            new_name = " ".join(args[2:]).strip()
            if slot not in range(pets.MAX_SLOTS):
                await event.reply("Укажите слот от 1 до 4.", parse_mode=None)
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
            "Команды: каз ферма, каз ферма собрать, "
            "каз ферма вылупить N, каз ферма переименовать N Имя",
            parse_mode=None,
        )
        return True

    if len(args) != 2 or not all(argument.isdigit() for argument in args):
        await event.reply("Формат: каз скрестить 1 2", parse_mode=None)
        return True
    first_slot, second_slot = (int(argument) - 1 for argument in args)
    if (
        first_slot == second_slot
        or first_slot not in range(pets.MAX_SLOTS)
        or second_slot not in range(pets.MAX_SLOTS)
    ):
        await event.reply(
            "Укажите два разных слота от 1 до 4.",
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
        generation = "гибрид" if child.generation else "чистопородный"
        await event.reply(
            "🔬 Родители заменены новым яйцом.\n"
            f"Слот: {child.slot_index + 1}. Поколение: {generation}.\n"
            f"Гены: 💨 {child.stench} · 🤢 {child.ugliness} · "
            f"🍯 {child.stickiness}.",
            parse_mode=None,
        )
    return True


async def award_slot_egg(event, store, chat_id: int, user_id: int) -> None:
    """Выдать награду за комбинацию ровно с двумя вишнями."""
    status, spec, egg = await store.award_pet_egg(chat_id, user_id)
    if status == "full":
        await event.reply(
            "🍒🍒 Выпало яйцо, но на ферме нет свободного слота.",
            parse_mode=None,
        )
        return
    await event.reply(
        "🍒🍒 Вы получили яйцо!\n"
        f"Специализация: {pets.SPEC_EMOJI[spec]} {pets.SPEC_NAMES[spec]}.\n"
        f"Слот: {egg.slot_index + 1}. До вылупления: 1 час.",
        parse_mode=None,
    )
