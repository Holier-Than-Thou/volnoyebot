"""Правила слота и регистрация команд казино."""

from __future__ import annotations

from telethon import events


SLOT_SYMBOLS = ("BAR", "🍒", "🍋", "7️⃣")
TRIPLE_PRIZES = {
    "7️⃣": (30, "Джекпот"),
    "BAR": (10, "Три BAR"),
    "🍒": (10, "Три вишни"),
    "🍋": (4, "Три лимона"),
}
SLOT_ANIMATION_SECONDS = 2.4


def fit_telegram_message(
    heading: str,
    rows: list[str],
    max_length: int = 4096,
) -> str:
    """Уместить максимум целых строк рейтинга в одно сообщение Telegram."""
    selected = [heading]
    for row in rows:
        candidate = "\n".join((*selected, row))
        if len(candidate) > max_length:
            break
        selected.append(row)

    shown = len(selected) - 1
    omitted = len(rows) - shown
    if omitted:
        footer = f"…ещё игроков: {omitted}"
        while len("\n".join((*selected, footer))) > max_length and shown:
            selected.pop()
            shown -= 1
            omitted += 1
            footer = f"…ещё игроков: {omitted}"
        if len("\n".join((*selected, footer))) <= max_length:
            selected.append(footer)
    return "\n".join(selected)


def message_topic_id(message) -> int:
    """Вернуть ID топика; основной топик и обычный чат обозначаются нулём."""
    reply_header = getattr(message, "reply_to", None)
    if reply_header is None or not getattr(reply_header, "forum_topic", False):
        return 0
    # Для ответа внутри топика Telegram обычно передаёт reply_to_top_id.
    # В обычном новом сообщении корень темы может быть только в reply_to_msg_id.
    topic_id = (
        getattr(reply_header, "reply_to_top_id", None)
        or getattr(reply_header, "reply_to_msg_id", None)
    )
    # У общего (General) топика Telegram обычно использует ID 1.
    return 0 if topic_id in (None, 1) else int(topic_id)


def is_explicit_message_reply(message) -> bool:
    """Отличить ответ пользователю от технической привязки к forum topic."""
    reply_header = getattr(message, "reply_to", None)
    reply_message_id = getattr(reply_header, "reply_to_msg_id", None)
    if reply_header is None or reply_message_id is None:
        return False
    if not getattr(reply_header, "forum_topic", False):
        return True
    top_message_id = getattr(reply_header, "reply_to_top_id", None)
    # У обычного сообщения в топике reply_to_msg_id указывает на корень темы,
    # а у настоящего ответа reply_to_top_id содержит этот корень отдельно.
    return (
        top_message_id is not None
        and reply_message_id != top_message_id
    )


def help_text(min_bet: int) -> str:
    return f"""🎰 Казино «Три топора»

каз баланс (можно ответом на сообщение игрока)
каз <сумма от {min_bet}>
каз ставка <сумма от {min_bet}>
каз ва-банк / каз вабанк
каз дать <сумма> (ответом на сообщение)
каз деп <ресурс>
каз призы
каз топ
каз топ RTP / каз топ RTP возр
каз лог / каз лог все
каз аналитика
каз ферма (можно ответом на сообщение игрока)
каз ферма собрать
каз ферма дать N (ответом игроку)
каз ферма приют N
каз ферма переименовать N Имя
каз скрестить N N

Все команды фермы также работают без «каз»:
ферма / ферма собрать / ферма дать N / ферма приют N
ферма переименовать N Имя / ферма скрестить N N
каз уведы
каз помощь

Игра между пользователями:
ответьте «кости <ставка>» на сообщение соперника;
соперник принимает игру ответом «+» или «да» в течение 60 секунд.

Одноразовые ресурсы:
👩 малышка — 2 000 очков
👵 мать — 10 000 очков
🚗 тачка — 25 000 очков
🏠 хата — 100 000 очков

Каждые 30 минут активным игрокам начисляется 1 000 очков.
Для первого начисления нужно хотя бы один раз сыграть или использовать команду.
Если не использовать команды бота более 3 суток, зарплата приостанавливается
до следующей команды.
Игроки без истории игр могут переводить очки через сутки после первой команды.
Администратор может включить или выключить это уведомление в текущем топике
командой «каз уведы».
Администратор: каз старт / каз стоп — управление казино и костями в разделе.

Каз аналитика — сводная статистика казино.
Ответом на сообщение — отдельная аналитика игрока по казино и костям.
Ровно две 🍒 дают яйцо для фермы при наличии свободного слота.
Администратор: каз зп — немедленно начислить всем по 1 000 очков.

Пример обмена: каз деп тачка"""


def prize_table() -> str:
    return "\n".join(
        (
            "🎰 Комбинации и полная выплата:",
            "7️⃣ 7️⃣ 7️⃣ — ×30 (джекпот)",
            "BAR BAR BAR — ×10",
            "🍒 🍒 🍒 — ×10",
            "🍋 🍋 🍋 — ×4",
            "Ровно две 7️⃣ — ×1 (возврат ставки)",
            "Ровно две 🍒 — яйцо для фермы",
            "Любая другая комбинация — без выплаты.",
        )
    )


def decode_slot(value: int) -> tuple[str, str, str]:
    """Преобразовать серверное значение Telegram 1..64 в три символа."""
    if not 1 <= value <= 64:
        raise ValueError(f"Некорректное значение слота Telegram: {value}")
    encoded = value - 1
    return (
        SLOT_SYMBOLS[encoded & 3],
        SLOT_SYMBOLS[(encoded >> 2) & 3],
        SLOT_SYMBOLS[(encoded >> 4) & 3],
    )


def get_prize(symbols: tuple[str, str, str]) -> tuple[int, str]:
    """Вернуть множитель полной выплаты и название результата."""
    if symbols[0] == symbols[1] == symbols[2]:
        return TRIPLE_PRIZES[symbols[0]]
    if symbols.count("7️⃣") == 2:
        return 1, "Две семёрки — ставка возвращена"
    return 0, ""


def register(client, handler) -> None:
    """Зарегистрировать корневую команду казино."""
    client.add_event_handler(
        handler,
        events.NewMessage(pattern=r"(?i)^каз(?:\s+(.+))?\s*$"),
    )
    client.add_event_handler(
        handler,
        events.NewMessage(pattern=r"(?i)^кз\s+(кд\s+\d+)\s*$"),
    )
