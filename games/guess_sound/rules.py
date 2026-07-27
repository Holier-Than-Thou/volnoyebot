"""Чистые правила нормализации ответов и подсчёта победителей."""

from __future__ import annotations

import re
import unicodedata

from .models import GuessOption


MAX_UNIQUE_OPTIONS = 11
MAX_OPTION_LENGTH = 100


def clean_guess(text: str) -> str:
    """Подготовить отображаемый текст ответа."""
    return re.sub(r"\s+", " ", text).strip()


def normalize_guess(text: str) -> str:
    """Нормализовать регистр, пробелы и пунктуацию для поиска дублей."""
    without_punctuation = "".join(
        " " if unicodedata.category(char).startswith("P") else char
        for char in clean_guess(text)
    )
    return re.sub(r"\s+", " ", without_punctuation).strip().casefold()


def winning_users(
    options: list[GuessOption],
    voters_by_option: dict[int, set[int]],
) -> set[int]:
    """Вернуть авторов вариантов с хотя бы одним внешним голосом."""
    winners: set[int] = set()
    for index, option in enumerate(options):
        eligible_voters = voters_by_option.get(index, set()) - option.author_ids
        if eligible_voters:
            winners.update(option.author_ids)
    return winners


def update_poll_vote(
    votes_by_user: dict[int, set[int]],
    user_id: int,
    selected_options: set[int],
) -> None:
    """Сохранить полный актуальный выбор пользователя или удалить его голос."""
    if selected_options:
        votes_by_user[user_id] = set(selected_options)
    else:
        votes_by_user.pop(user_id, None)


def voters_grouped_by_option(
    votes_by_user: dict[int, set[int]],
) -> dict[int, set[int]]:
    """Преобразовать актуальные голоса пользователей в разрез вариантов."""
    voters_by_option: dict[int, set[int]] = {}
    for user_id, selected_options in votes_by_user.items():
        for option_index in selected_options:
            voters_by_option.setdefault(option_index, set()).add(user_id)
    return voters_by_option


def selected_poll_option_indexes(
    positions: list[int] | None,
    options: list[bytes],
    option_indexes: dict[bytes, int],
) -> set[int]:
    """Получить выбранные индексы из события с совместимым fallback."""
    if positions:
        return {int(position) for position in positions}
    return {
        option_indexes[option]
        for option in options
        if option in option_indexes
    }
