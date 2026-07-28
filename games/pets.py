"""Чистые правила фермы и селекции питомцев."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Mapping


SPECS = ("stench", "ugliness", "stickiness")
SPEC_EMOJI = {
    "stench": "💨",
    "ugliness": "🤢",
    "stickiness": "🍯",
}
SPEC_NAMES = {
    "stench": "Вонь",
    "ugliness": "Уродство",
    "stickiness": "Липкость",
}
MAX_SLOTS = 4
EGG_HATCH_SECONDS = 60 * 60
BASE_INCOME_PER_SECOND = 0.1


@dataclass(frozen=True)
class Pet:
    slot_index: int
    name: str
    stench: int
    ugliness: int
    stickiness: int
    generation: int
    is_egg: bool
    egg_hatch_at: float
    created_at: float

    def income_per_second(self) -> float:
        """Вернуть пассивный доход взрослого питомца."""
        if self.is_egg:
            return 0.0
        return (
            BASE_INCOME_PER_SECOND
            * (1 + self.stench / 100)
            * (1 + self.ugliness / 100)
            * (1 + self.stickiness / 100)
        )

    def main_spec(self) -> str:
        """Вернуть характеристику с максимальным значением."""
        values = (
            (self.stench, "stench"),
            (self.ugliness, "ugliness"),
            (self.stickiness, "stickiness"),
        )
        return max(values)[1]


def pet_from_mapping(row: Mapping) -> Pet:
    """Преобразовать SQLite-строку или словарь в модель питомца."""
    return Pet(
        slot_index=int(row["slot_index"]),
        name=str(row["name"]),
        stench=int(row["stench"]),
        ugliness=int(row["ugliness"]),
        stickiness=int(row["stickiness"]),
        generation=int(row["generation"]),
        is_egg=bool(row["is_egg"]),
        egg_hatch_at=float(row["egg_hatch_at"]),
        created_at=float(row["created_at"]),
    )


def roll_spec() -> str:
    """Случайно выбрать постоянную специализацию игрока."""
    return random.choice(SPECS)


def roll_egg_value() -> int:
    """Получить стартовую характеристику чистопородного яйца."""
    return max(1, min(20, round(random.gauss(10, 4))))


def create_pure_egg(spec: str, slot_index: int, now: float | None = None) -> Pet:
    """Создать яйцо со специализацией игрока."""
    if spec not in SPECS:
        raise ValueError("Неизвестная специализация")
    created_at = time.time() if now is None else now
    stats = {key: 1 for key in SPECS}
    stats[spec] = roll_egg_value()
    return Pet(
        slot_index=slot_index,
        name="Мутант",
        stench=stats["stench"],
        ugliness=stats["ugliness"],
        stickiness=stats["stickiness"],
        generation=0,
        is_egg=True,
        egg_hatch_at=created_at + EGG_HATCH_SECONDS,
        created_at=created_at,
    )


def breed_value(first: int, second: int) -> int:
    """Унаследовать характеристику с мутацией и ограничением роста."""
    average = (first + second) / 2
    mutated = round(average + random.gauss(0, 5))
    return max(1, min(100, min(mutated, max(first, second) + 33)))


def breed(first: Pet, second: Pet, slot_index: int, now: float | None = None) -> Pet:
    """Создать яйцо ребёнка; гибриды и яйца не допускаются."""
    if first.is_egg or second.is_egg:
        raise ValueError("Нельзя скрещивать яйца")
    if first.generation != 0 or second.generation != 0:
        raise ValueError("Гибриды стерильны и не могут участвовать в селекции")
    created_at = time.time() if now is None else now
    generation = 0 if first.main_spec() == second.main_spec() else 1
    return Pet(
        slot_index=slot_index,
        name="Мутант",
        stench=breed_value(first.stench, second.stench),
        ugliness=breed_value(first.ugliness, second.ugliness),
        stickiness=breed_value(first.stickiness, second.stickiness),
        generation=generation,
        is_egg=True,
        egg_hatch_at=created_at + EGG_HATCH_SECONDS,
        created_at=created_at,
    )
