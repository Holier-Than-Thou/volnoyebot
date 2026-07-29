"""Чистые правила фермы и селекции питомцев."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from pathlib import Path
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
MAX_SLOTS = 6
MIN_EGG_HATCH_MINUTES = 5
MAX_EGG_HATCH_MINUTES = 60
BASE_INCOME_PER_SECOND = 0.1
INCOME_SYNERGY = {1: 1.0, 2: 1.35, 3: 1.8}
PURE_MUTATION_MEAN = 1.5
PURE_MUTATION_DEVIATION = 3
HYBRID_MUTATION_MEAN = 0.5
HYBRID_MUTATION_DEVIATION = 2
MAX_BREEDING_GAIN = 8
PET_NAMES_PATH = Path(__file__).resolve().parent.parent / "data" / "pet_names.txt"


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
        return self.adult_income_per_second()

    def adult_income_per_second(self) -> float:
        """Вернуть потенциальный доход питомца после вылупления."""
        active_values = [
            value
            for value in (self.stench, self.ugliness, self.stickiness)
            if value > 0
        ]
        if not active_values:
            return 0.0
        income = BASE_INCOME_PER_SECOND
        for value in active_values:
            income *= 1 + value / 100
        return income * INCOME_SYNERGY[len(active_values)]

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


def roll_hatch_seconds() -> int:
    """Выбрать равноверное время созревания от 5 до 60 минут."""
    return random.randint(
        MIN_EGG_HATCH_MINUTES,
        MAX_EGG_HATCH_MINUTES,
    ) * 60


def random_pet_name(path: Path = PET_NAMES_PATH) -> str:
    """Выбрать имя из UTF-8-файла, игнорируя пустые строки и комментарии."""
    try:
        names = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
    except OSError:
        names = []
    return random.choice(names) if names else "Мутант"


def create_pure_egg(spec: str, slot_index: int, now: float | None = None) -> Pet:
    """Создать яйцо со специализацией игрока."""
    if spec not in SPECS:
        raise ValueError("Неизвестная специализация")
    created_at = time.time() if now is None else now
    stats = {key: 0 for key in SPECS}
    stats[spec] = roll_egg_value()
    return Pet(
        slot_index=slot_index,
        name="",
        stench=stats["stench"],
        ugliness=stats["ugliness"],
        stickiness=stats["stickiness"],
        generation=0,
        is_egg=True,
        egg_hatch_at=created_at + roll_hatch_seconds(),
        created_at=created_at,
    )


def breed_value(first: int, second: int) -> int:
    """Унаследовать активный признак с небольшим положительным прогрессом."""
    if first == 0 and second == 0:
        return 0
    if first == 0 or second == 0:
        base = max(first, second)
        mutation = random.gauss(
            HYBRID_MUTATION_MEAN,
            HYBRID_MUTATION_DEVIATION,
        )
    else:
        base = (first + second) / 2
        mutation = random.gauss(
            PURE_MUTATION_MEAN,
            PURE_MUTATION_DEVIATION,
        )
    mutated = round(base + mutation)
    upper_bound = min(100, max(first, second) + MAX_BREEDING_GAIN)
    return max(1, min(mutated, upper_bound))


def breed(first: Pet, second: Pet, slot_index: int, now: float | None = None) -> Pet:
    """Создать яйцо ребёнка; гибриды и яйца не допускаются."""
    if first.is_egg or second.is_egg:
        raise ValueError("Нельзя скрещивать яйца")
    if first.generation != 0 or second.generation != 0:
        raise ValueError("Гибриды стерильны и не могут участвовать в селекции")
    created_at = time.time() if now is None else now
    child_stats = {
        "stench": breed_value(first.stench, second.stench),
        "ugliness": breed_value(first.ugliness, second.ugliness),
        "stickiness": breed_value(first.stickiness, second.stickiness),
    }
    generation = int(sum(value > 0 for value in child_stats.values()) > 1)
    return Pet(
        slot_index=slot_index,
        name="",
        stench=child_stats["stench"],
        ugliness=child_stats["ugliness"],
        stickiness=child_stats["stickiness"],
        generation=generation,
        is_egg=True,
        egg_hatch_at=created_at + roll_hatch_seconds(),
        created_at=created_at,
    )
