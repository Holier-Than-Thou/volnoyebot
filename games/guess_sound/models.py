"""Модели, не зависящие от Telegram и конкретного банка звуков."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Sound:
    provider: str
    external_id: str
    description: str
    source_url: str
    preview_url: str
    duration: float


@dataclass
class GuessOption:
    text: str
    author_ids: set[int] = field(default_factory=set)
    author_names: dict[int, str] = field(default_factory=dict)


@dataclass
class Round:
    chat_id: int
    sound: Sound
    audio_message_id: int
    topic_id: int | None = None
    phase: str = "guessing"
    guesses_by_user: dict[int, str] = field(default_factory=dict)
    options: dict[str, GuessOption] = field(default_factory=dict)
    poll_id: int | None = None
    poll_option_indexes: dict[bytes, int] = field(default_factory=dict)
    poll_votes_by_user: dict[int, set[int]] = field(default_factory=dict)
    limit_announced: bool = False
    task: object | None = None
