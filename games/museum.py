"""Чистые правила музея и создания статуй."""

from __future__ import annotations

import random
from dataclasses import dataclass

from telethon import events


@dataclass(frozen=True)
class StatueSize:
    code: str
    name: str
    marker: str
    base_income: int
    gold_multiplier_numerator: int
    gold_multiplier_denominator: int


SIZES = {
    "Б": StatueSize("Б", "Большая", "📍", 9_000, 2, 1),
    "Г": StatueSize("Г", "Гигантская", "📌", 30_000, 1, 1),
    "В": StatueSize("В", "Великая", "🗿", 75_000, 1, 2),
}

QUALITY_RULES = (
    (85, "Нормальное", "⬜", 1),
    (94, "Хорошее", "🟩", 2),
    (99, "Отличное", "🟦", 5),
    (None, "Шедевр", "🟨", 10),
)


def all_in_gold_reward(
    stake: int,
    symbols: tuple[str, str, str],
) -> int:
    """Начислить золото за ва-банк, кроме результата ровно с двумя 7."""
    if symbols.count("7️⃣") == 2:
        return 0
    return max(0, stake) // 100_000


@dataclass(frozen=True)
class StatueRoll:
    size: StatueSize
    quality: str | None
    color: str | None
    quality_multiplier: int
    gold_spent: int
    base_roll: int
    bonus: int
    score: int
    income_per_day: int

    @property
    def is_broken(self) -> bool:
        """Заготовка сломалась, если бросок не достиг нормального качества."""
        return self.quality is None


def normalize_size_code(value: str) -> str | None:
    """Принять букву размера независимо от регистра и точки."""
    code = value.strip().rstrip(".").upper()
    return code if code in SIZES else None


def quality_for_score(score: int) -> tuple[str, str, int] | None:
    """Определить качество либо вернуть None для сломанной заготовки."""
    if score < 71:
        return None
    for upper_bound, name, color, multiplier in QUALITY_RULES:
        if upper_bound is None or score <= upper_bound:
            return name, color, multiplier
    raise AssertionError("Сетка качества не покрывает результат")


def create_statue_roll(
    size_code: str,
    gold: int,
    rng: random.Random | None = None,
) -> StatueRoll:
    """Создать результат статуи без дробных промежуточных значений."""
    if gold < 1:
        raise ValueError("Нужно вложить хотя бы 1 золото")
    normalized = normalize_size_code(size_code)
    if normalized is None:
        raise ValueError("Размер должен быть Б, Г или В")
    size = SIZES[normalized]
    generator = rng or random
    base_roll = generator.randint(1, 100)
    # Для Великой статуи K=0,5 считается целочисленно: каждые две
    # дополнительные единицы золота дают один полный балл.
    bonus = (
        (gold - 1) * size.gold_multiplier_numerator
        // size.gold_multiplier_denominator
    )
    score = base_roll + bonus
    quality_result = quality_for_score(score)
    if quality_result is None:
        quality = None
        color = None
        quality_multiplier = 0
    else:
        quality, color, quality_multiplier = quality_result
    return StatueRoll(
        size=size,
        quality=quality,
        color=color,
        quality_multiplier=quality_multiplier,
        gold_spent=gold,
        base_roll=base_roll,
        bonus=bonus,
        score=score,
        income_per_day=size.base_income * quality_multiplier,
    )


def parse_create_arguments(args: list[str]) -> tuple[int, str] | None:
    """Разобрать полную и короткие формы количества золота."""
    filtered = [
        value
        for value in args
        if value.casefold() not in {"з", "золото", "золота"}
    ]
    if len(filtered) != 2:
        return None
    amount = next(
        (
            int(value[:-1])
            for value in filtered
            if value.casefold().endswith("з") and value[:-1].isdigit()
        ),
        None,
    )
    if amount is None:
        amount = next(
            (int(value) for value in filtered if value.isdigit()),
            None,
        )
    size = next(
        (
            normalize_size_code(value)
            for value in filtered
            if normalize_size_code(value) is not None
        ),
        None,
    )
    if amount is None or size is None or amount < 1:
        return None
    return amount, size


def format_points(value: int, signed: bool = False) -> str:
    text = f"{abs(value):,}".replace(",", " ")
    if not signed:
        return text
    if value > 0:
        return f"+{text}"
    if value < 0:
        return f"−{text}"
    return "0"


def format_museum(owner_name: str, snapshot: dict) -> str:
    """Подготовить компактную экспозицию и легенду маркеров."""
    lines = [
        f"🏛 Музей: {owner_name}",
        f"🥇 Золото: {format_points(snapshot['gold'])}",
        f"Статуй: {len(snapshot['statues'])}",
        "Доход: "
        f"{format_points(snapshot['daily_income'], signed=True)} очков/сутки",
    ]
    if snapshot["raw_daily_income"] > snapshot["daily_income"]:
        lines.append(
            "Сумма доходов статуй: "
            f"{format_points(snapshot['raw_daily_income'])} очков/сутки; "
            "начисление ограничено 300 000."
        )
    if not snapshot["statues"]:
        lines.append("\nЭкспозиция пока пуста.")
    else:
        lines.append("\nЭкспозиция:")
        for index, statue in enumerate(snapshot["statues"], 1):
            size = SIZES[statue["size_code"]]
            lines.append(
                f"{index}. {statue['color']} {size.marker} {size.name} · "
                f"{statue['quality']} · "
                f"{format_points(statue['income_per_day'], signed=True)}/сутки"
            )
    lines.extend(
        (
            "",
            "Размер: 📍 Большая · 📌 Гигантская · 🗿 Великая",
            "Качество: ⬜ нормальное · 🟩 хорошее · "
            "🟦 отличное · 🟨 шедевр",
        )
    )
    return "\n".join(lines)


def help_text() -> str:
    """Вернуть полную пользовательскую справку по музею."""
    return """🏛 Музей — справка

Как получить золото
  Золото выдаётся только за ставку ва-банк: 1 🥇 за каждые полные
  100 000 очков ставки, независимо от выигрыша или проигрыша.

  Например, ва-банк на 350 000 даёт 3 🥇.

  Исключение: если выпало ровно две 7️⃣, золото не начисляется.
  Три 7️⃣ золото дают. Запас золота виден в «каз баланс» и «музей».

Создание статуи
  Вы выбираете размер и количество золота. Всё указанное золото списывается.
  Бросок = случайное число 1–100 + бонус вложенного золота.

  📍 Б — Большая: базовый доход 9 000/сутки, бонус (золото − 1) × 2.
  📌 Г — Гигантская: базовый доход 30 000/сутки, бонус (золото − 1).
  🗿 В — Великая: базовый доход 75 000/сутки, бонус (золото − 1) // 2.

Качество и множитель дохода
  1–70 — заготовка ломается, статуя не появляется.
  ⬜️ 71–85 — Нормальное: ×1
  🟩 86–94 — Хорошее: ×2
  🟦 95–99 — Отличное: ×5
  🟨 100 и выше — Шедевр: ×10

Пример
  Большая статуя за 10 🥇 получила базовый бросок 70.
  Бонус: (10 − 1) × 2 = 18. Итог: 70 + 18 = 88.
  Это 🟩 Хорошее качество: 9 000 × 2 = 18 000 очков/сутки.

Доход музея
  Доходы всех статуй складываются и начисляются за каждые завершённые сутки.
  За одни сутки музей может начислить не более 300 000 очков. Статуи и их
  собственные характеристики при достижении лимита не изменяются.
  Статуи нельзя удалить вручную.

Команды
  музей — показать свой музей
  музей (ответом на сообщение) — показать музей игрока
  музей помощь — открыть эту справку
  музей создать Х золота Б/Г/В
  музей создать Хз Б/Г/В
  музей создать Б/Г/В Х з"""


def register(client, handler) -> None:
    """Зарегистрировать пользовательский префикс «музей»."""
    client.add_event_handler(
        handler,
        events.NewMessage(pattern=r"(?iu)^музей(?:\s+(.+))?\s*$"),
    )
