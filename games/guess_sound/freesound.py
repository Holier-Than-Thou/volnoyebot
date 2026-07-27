"""Адаптер Freesound API v2."""

from __future__ import annotations

import asyncio
import html
import json
import random
import re
import urllib.error
import urllib.parse
import urllib.request

from .models import Sound


API_URL = "https://freesound.org/apiv2/search/"
PAGE_SIZE = 50
MAX_RANDOM_PAGE = 150
BST_SUBCATEGORIES = {
    "fx-o": "Objects / House appliances",
    "fx-v": "Vehicles",
    "fx-m": "Other mechanisms, engines, machines",
    "fx-h": "Human sounds and actions",
    "fx-a": "Animals",
    "fx-n": "Natural elements and explosions",
}


class FreesoundError(RuntimeError):
    """Ошибка получения звука или превью."""


class FreesoundProvider:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def _request_json(self, params: dict[str, str | int]) -> dict:
        if not self.api_key:
            raise FreesoundError("в .env не задан FREESOUND_API_KEY")
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{API_URL}?{query}",
            headers={
                "Authorization": f"Token {self.api_key}",
                "User-Agent": "telegram-guess-sound-userbot/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8", errors="replace"))
                detail = payload.get("detail") or str(error)
            except (ValueError, OSError):
                detail = str(error)
            raise FreesoundError(
                f"Freesound API вернул HTTP {error.code}: {detail}"
            ) from error
        except Exception as error:
            raise FreesoundError("Freesound API недоступен") from error

    def _search_page(self, page: int, category: str | None) -> dict:
        return self._request_json(
            {
                "query": "",
                "filter": _search_filter(category),
                "fields": "id,description,url,duration,previews",
                "page_size": PAGE_SIZE,
                "page": page,
            }
        )

    async def get_sound(
        self,
        excluded_ids: set[str],
        category: str | None = None,
    ) -> Sound:
        first_page = await asyncio.to_thread(self._search_page, 1, category)
        count = int(first_page.get("count", 0))
        if count <= 0:
            raise FreesoundError("Freesound не вернул подходящих звуков")

        max_page = min(MAX_RANDOM_PAGE, (count + PAGE_SIZE - 1) // PAGE_SIZE)
        pages = [1]
        if max_page > 1:
            pages.extend(random.sample(range(2, max_page + 1), min(5, max_page - 1)))

        for page in pages:
            payload = (
                first_page
                if page == 1
                else await asyncio.to_thread(self._search_page, page, category)
            )
            candidates = list(payload.get("results") or [])
            random.shuffle(candidates)
            for item in candidates:
                external_id = str(item["id"])
                previews = item.get("previews") or {}
                preview_url = previews.get("preview-hq-mp3")
                description = _plain_description(item.get("description", ""))
                if (
                    external_id not in excluded_ids
                    and preview_url
                    and description
                ):
                    return Sound(
                        provider="freesound",
                        external_id=external_id,
                        description=description,
                        source_url=item["url"],
                        preview_url=preview_url,
                        duration=float(item["duration"]),
                    )
        raise FreesoundError(
            "Не удалось найти новый звук; возможно, доступные записи уже сыграны"
        )

    async def download_preview(self, sound: Sound) -> bytes:
        def download() -> bytes:
            request = urllib.request.Request(
                sound.preview_url,
                headers={"User-Agent": "telegram-guess-sound-userbot/1.0"},
            )
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    return response.read()
            except Exception as error:
                raise FreesoundError("Не удалось скачать превью звука") from error

        return await asyncio.to_thread(download)


def _plain_description(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _search_filter(category: str | None) -> str:
    filters = [
        "duration:[3 TO 20]",
        'license:"Creative Commons 0"',
        "is_explicit:false",
    ]
    if category:
        subcategory = BST_SUBCATEGORIES.get(category)
        if subcategory is None:
            raise FreesoundError(f"неизвестная BST-категория: {category}")
        filters.extend(
            (
                'category:"Sound effects"',
                f'subcategory:"{subcategory}"',
            )
        )
    return " ".join(filters)
