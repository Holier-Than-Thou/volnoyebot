r"""Монте-Карло симуляция экономики казино, фермы и музея статуй.

Запуск из корня проекта:
    .venv\Scripts\python.exe simulations\statue_economy.py
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from dataclasses import asdict, dataclass


PLAYERS_PER_COHORT = 100
DAYS = 30
SALARY_PER_DAY = 48_000
INITIAL_BALANCE = 1_000
PET_COUNT = 6
PET_BASE_INCOME_PER_SECOND = 0.1
SECONDS_PER_DAY = 86_400


@dataclass(frozen=True)
class StatueSize:
    name: str
    base_income_per_day: int
    gold_multiplier: float
    minimum_gold: int
    masterpiece_gold: int


STATUE_SIZES = (
    StatueSize("Большая", 9_000, 2.0, 3, 51),
    StatueSize("Гигантская", 30_000, 1.0, 5, 100),
    StatueSize("Великая", 75_000, 0.5, 9, 199),
)

QUALITY_MULTIPLIERS = {
    "Ужасное": -2,
    "Плохое": -1,
    "Нормальное": 1,
    "Хорошее": 2,
    "Отличное": 5,
    "Шедевр": 10,
}


@dataclass
class Player:
    player_id: int
    balance: int
    gold: int
    farm_income_per_day: int
    museum_income_per_day: int = 0
    statues: int = 0
    broken_statues: int = 0
    total_casino_turnover: int = 0
    total_gold_earned: int = 0
    largest_bet: int = 0
    last_day_bankroll: int = 0


def roll_pet_gene(rng: random.Random) -> int:
    """Повторить games.pets.roll_egg_value без зависимости от Telethon."""
    return max(1, min(20, round(rng.gauss(10, 4))))


def roll_farm_income_per_day(rng: random.Random) -> int:
    """Суммарный суточный доход шести уже вылупившихся питомцев."""
    income_per_second = sum(
        PET_BASE_INCOME_PER_SECOND * (1 + roll_pet_gene(rng) / 100)
        for _ in range(PET_COUNT)
    )
    return math.floor(income_per_second * SECONDS_PER_DAY)


def slot_payout_multiplier(value: int) -> tuple[int, bool]:
    """Вернуть выплату и признак комбинации «ровно две семёрки»."""
    symbols = (
        value & 3,
        (value >> 2) & 3,
        (value >> 4) & 3,
    )
    if symbols[0] == symbols[1] == symbols[2]:
        return (30, False) if symbols[0] == 3 else (
            (10, False) if symbols[0] in (0, 1) else (4, False)
        )
    two_sevens = symbols.count(3) == 2
    return (1, True) if two_sevens else (0, False)


def play_until_ruin(player: Player, rng: random.Random) -> int:
    """Ставить весь баланс до первой нулевой выплаты и вернуть число ставок."""
    bets = 0
    while player.balance:
        stake = player.balance
        player.total_casino_turnover += stake
        player.largest_bet = max(player.largest_bet, stake)
        payout, two_sevens = slot_payout_multiplier(rng.randrange(64))
        if not two_sevens:
            earned_gold = stake // 100_000
            player.gold += earned_gold
            player.total_gold_earned += earned_gold
        player.balance = stake * payout
        bets += 1
    return bets


def quality_for_score(score: int) -> str:
    if score <= 40:
        return "Ужасное"
    if score <= 70:
        return "Плохое"
    if score <= 85:
        return "Нормальное"
    if score <= 94:
        return "Хорошее"
    if score <= 99:
        return "Отличное"
    return "Шедевр"


def maybe_build_statue(
    player: Player,
    rng: random.Random,
    build_probability: float,
) -> None:
    """С вероятностью построить один доступный размер статуи."""
    if rng.random() >= build_probability:
        return
    affordable = [size for size in STATUE_SIZES if player.gold >= size.minimum_gold]
    if not affordable:
        return
    size = rng.choice(affordable)
    gold_spent = min(player.gold, size.masterpiece_gold)
    player.gold -= gold_spent
    bonus = math.floor((gold_spent - 1) * size.gold_multiplier)
    score = rng.randint(1, 100) + bonus
    quality = quality_for_score(score)
    if quality in {"Ужасное", "Плохое"}:
        player.broken_statues += 1
        return
    player.museum_income_per_day += (
        size.base_income_per_day * QUALITY_MULTIPLIERS[quality]
    )
    player.statues += 1


def simulate_cohort(
    seed: int,
    build_probability: float,
) -> tuple[list[Player], int]:
    rng = random.Random(seed)
    players = [
        Player(
            player_id=index + 1,
            balance=INITIAL_BALANCE,
            gold=0,
            farm_income_per_day=roll_farm_income_per_day(rng),
        )
        for index in range(PLAYERS_PER_COHORT)
    ]
    total_bets = 0
    for _day in range(DAYS):
        for player in players:
            museum_daily_income = player.museum_income_per_day
            player.balance += (
                SALARY_PER_DAY
                + player.farm_income_per_day
                + museum_daily_income
            )
            player.last_day_bankroll = player.balance
            total_bets += play_until_ruin(player, rng)
            maybe_build_statue(player, rng, build_probability)
    return players, total_bets


def percentile(values: list[int], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def player_metrics(player: Player) -> dict[str, int]:
    return {
        "balance": player.balance,
        "farm_per_day": player.farm_income_per_day,
        "museum_per_day": player.museum_income_per_day,
        "statues": player.statues,
        "broken_statues": player.broken_statues,
        "gold": player.gold,
        "gold_earned": player.total_gold_earned,
        "turnover": player.total_casino_turnover,
        "largest_bet": player.largest_bet,
        "last_day_bankroll": player.last_day_bankroll,
    }


def summarize(players: list[Player]) -> dict[str, dict[str, float]]:
    metrics = [player_metrics(player) for player in players]
    result: dict[str, dict[str, float]] = {}
    for key in metrics[0]:
        values = [row[key] for row in metrics]
        result[key] = {
            "mean": statistics.fmean(values),
            "median": statistics.median(values),
            "p05": percentile(values, 0.05),
            "p95": percentile(values, 0.95),
            "max": max(values),
        }
    return result


def find_outliers(players: list[Player]) -> list[dict[str, int]]:
    """Найти верхние выбросы по доходу музея, золоту или обороту."""
    rows = [player_metrics(player) for player in players]
    keys = ("museum_per_day", "gold", "turnover")
    limits: dict[str, float] = {}
    for key in keys:
        values = [row[key] for row in rows]
        q1 = percentile(values, 0.25)
        q3 = percentile(values, 0.75)
        limits[key] = q3 + 1.5 * (q3 - q1)
    outliers = []
    for player, row in zip(players, rows):
        if any(row[key] > limits[key] for key in keys):
            outliers.append({"player_id": player.player_id, **row})
    return sorted(outliers, key=lambda row: row["turnover"], reverse=True)[:10]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohorts", type=int, default=2_000)
    parser.add_argument("--seed", type=int, default=20260731)
    parser.add_argument("--build-probability", type=float, default=0.20)
    args = parser.parse_args()

    all_players: list[Player] = []
    cohort_results: list[tuple[list[Player], int]] = []
    for cohort_index in range(args.cohorts):
        result = simulate_cohort(
            args.seed + cohort_index,
            args.build_probability,
        )
        cohort_results.append(result)
        all_players.extend(result[0])

    cohort_turnovers = [
        sum(player.total_casino_turnover for player in players)
        for players, _bets in cohort_results
    ]
    median_turnover = statistics.median(cohort_turnovers)
    representative_index = min(
        range(len(cohort_results)),
        key=lambda index: abs(cohort_turnovers[index] - median_turnover),
    )
    representative_players, representative_bets = cohort_results[
        representative_index
    ]

    report = {
        "parameters": {
            "cohorts": args.cohorts,
            "players_per_cohort": PLAYERS_PER_COHORT,
            "days": DAYS,
            "seed": args.seed,
            "build_probability": args.build_probability,
            "salary_per_day": SALARY_PER_DAY,
            "initial_balance": INITIAL_BALANCE,
        },
        "all_simulated_players": summarize(all_players),
        "representative_cohort": {
            "index": representative_index,
            "total_bets": representative_bets,
            "summary": summarize(representative_players),
            "outliers": find_outliers(representative_players),
        },
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
