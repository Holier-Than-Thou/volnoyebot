"""Контракт источника звуков."""

from __future__ import annotations

from typing import Protocol

from .models import Sound


class SoundProvider(Protocol):
    async def get_sound(
        self,
        excluded_ids: set[str],
        category: str | None = None,
    ) -> Sound:
        """Выбрать ещё не использованный звук."""

    async def download_preview(self, sound: Sound) -> bytes:
        """Получить обезличиваемое MP3-превью."""
