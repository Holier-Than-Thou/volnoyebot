"""Шуточный Telegram-бот с виртуальными очками.

Проект намеренно не содержит покупки, вывода или обмена очков.
"""

from __future__ import annotations

import asyncio
import math
import os
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.tl import types
from telethon.tl.types import Channel, Chat, MessageMediaDice, User

from games import casino, dice, farm, guess_sound, motovskikh_link, museum
from games.farm_storage import FarmStoreMixin, initialize_farm_schema
from games.guess_sound.freesound import FreesoundProvider
from games.museum_storage import MuseumStoreMixin, initialize_museum_schema
from games.motovskikh_storage import (
    MotovskikhStoreMixin,
    initialize_motovskikh_schema,
)
from games.release import (
    RELEASE_ID,
    RELEASE_SUMMARY,
    ReleaseStoreMixin,
    initialize_release_schema,
)

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = Path(os.getenv("ENV_FILE", BASE_DIR / ".env"))
load_dotenv(ENV_FILE)
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR))


def env_int(name: str, default: int | None = None) -> int:
    """Прочитать целое число из окружения и выдать понятную ошибку."""
    raw = os.getenv(name)
    if raw is None:
        if default is not None:
            return default
        raise RuntimeError(f"В .env не задано обязательное поле {name}")
    try:
        return int(raw)
    except ValueError as error:
        raise RuntimeError(f"{name} должно быть целым числом") from error


API_ID = env_int("API_ID")
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = env_int("ADMIN_ID")
SESSION_NAME = os.getenv("SESSION_NAME", "casino_bot").strip()
INITIAL_BALANCE = env_int("INITIAL_BALANCE", 1000)
MIN_BET = env_int("MIN_BET", 1)
FREESOUND_API_KEY = os.getenv("FREESOUND_API_KEY", "").strip()
MOTOVSKIKH_DEFAULT_DATA_DIR = (
    DATA_DIR if os.getenv("DATA_DIR") else BASE_DIR / "data"
)
MOTOVSKIKH_COOKIE_PATH = Path(
    os.getenv(
        "MOTOVSKIKH_COOKIE_PATH",
        MOTOVSKIKH_DEFAULT_DATA_DIR / "motovskikh.cookies.txt",
    )
)
MOTOVSKIKH_LINK_TTL_SECONDS = env_int("MOTOVSKIKH_LINK_TTL_SECONDS", 300)
MOTOVSKIKH_MAX_LINK_ATTEMPTS = env_int("MOTOVSKIKH_MAX_LINK_ATTEMPTS", 10)

if not API_HASH:
    raise RuntimeError("В .env не задан API_HASH")
if not BOT_TOKEN or BOT_TOKEN == "replace_me":
    raise RuntimeError("В .env не задан BOT_TOKEN от BotFather")
if ADMIN_ID <= 0:
    raise RuntimeError("В .env должен быть указан положительный ADMIN_ID")
if INITIAL_BALANCE < 0 or MIN_BET <= 0:
    raise RuntimeError("Проверьте INITIAL_BALANCE и MIN_BET в .env")
if MOTOVSKIKH_LINK_TTL_SECONDS <= 0 or MOTOVSKIKH_MAX_LINK_ATTEMPTS <= 0:
    raise RuntimeError("Проверьте настройки привязки Motovskikh в .env")


DEFAULT_BET_COOLDOWN_SECONDS = 20
GAME_MESSAGE_TTL_SECONDS = 5
HISTORY_LIMIT = 10
WORK_PAYOUT_AMOUNT = 1_000
WORK_PAYOUT_INTERVAL_SECONDS = 30 * 60
WORK_ACTIVITY_TIMEOUT_SECONDS = 3 * 24 * 60 * 60
NEW_PLAYER_TRANSFER_LOCK_SECONDS = 24 * 60 * 60
PET_HATCH_CHECK_INTERVAL_SECONDS = 10
MUSEUM_INCOME_CHECK_INTERVAL_SECONDS = 60
WORK_PAYOUT_MESSAGE = (
    "Вы поработали в долбильне и заработали 1000 очков. Время депать!"
)
ASSETS = {
    "малышка": ("girl_available", "👩", 2_000, "Карта девушки"),
    "мать": ("mother_available", "👵", 10_000, "Карта матери"),
    "тачка": ("car_available", "🚗", 25_000, "Машина"),
    "хата": ("home_available", "🏠", 100_000, "Квартира"),
}

HELP_TEXT = casino.help_text(MIN_BET)


class BalanceStore(
    FarmStoreMixin,
    MotovskikhStoreMixin,
    MuseumStoreMixin,
    ReleaseStoreMixin,
):
    """Небольшое SQLite-хранилище балансов по чату и пользователю."""

    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.lock = asyncio.Lock()
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS balances (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                balance INTEGER NOT NULL CHECK (balance >= 0),
                last_bet_at REAL NOT NULL DEFAULT 0,
                last_command_at REAL NOT NULL DEFAULT 0,
                first_command_at REAL,
                max_bet INTEGER CHECK (max_bet IS NULL OR max_bet > 0),
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
        # Миграция базы, созданной до появления ограничения между ставками.
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(balances)")
        }
        if "last_bet_at" not in columns:
            self.connection.execute(
                "ALTER TABLE balances "
                "ADD COLUMN last_bet_at REAL NOT NULL DEFAULT 0"
            )
        if "last_command_at" not in columns:
            self.connection.execute(
                "ALTER TABLE balances "
                "ADD COLUMN last_command_at REAL NOT NULL DEFAULT 0"
            )
            # Исторически команды не записывались. Начинаем отсчёт неактивности
            # для существующих пользователей с момента установки обновления.
            self.connection.execute(
                "UPDATE balances SET last_command_at = ?",
                (time.time(),),
            )
            # Игроки без единой завершённой игры должны сначала явно
            # воспользоваться командой после обновления.
            self.connection.execute(
                """
                UPDATE balances
                SET last_command_at = 0
                WHERE NOT EXISTS (
                    SELECT 1
                    FROM game_history AS history
                    WHERE history.chat_id = balances.chat_id
                        AND (
                            history.player_id = balances.user_id
                            OR (
                                history.game_type = 'dice'
                                AND history.opponent_id = balances.user_id
                            )
                        )
                )
                """
            )
        if "first_command_at" not in columns:
            self.connection.execute(
                "ALTER TABLE balances ADD COLUMN first_command_at REAL"
            )
        if "max_bet" not in columns:
            self.connection.execute(
                "ALTER TABLE balances "
                "ADD COLUMN max_bet INTEGER "
                "CHECK (max_bet IS NULL OR max_bet > 0)"
            )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_settings (
                chat_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
                auto_delete INTEGER NOT NULL DEFAULT 1
                    CHECK (auto_delete IN (0, 1)),
                bet_cooldown_seconds INTEGER NOT NULL DEFAULT 20
                    CHECK (bet_cooldown_seconds >= 0),
                activated INTEGER NOT NULL DEFAULT 0
                    CHECK (activated IN (0, 1))
            )
            """
        )
        chat_setting_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(chat_settings)")
        }
        if "auto_delete" not in chat_setting_columns:
            self.connection.execute(
                "ALTER TABLE chat_settings "
                "ADD COLUMN auto_delete INTEGER NOT NULL DEFAULT 1"
            )
        if "bet_cooldown_seconds" not in chat_setting_columns:
            self.connection.execute(
                "ALTER TABLE chat_settings "
                "ADD COLUMN bet_cooldown_seconds INTEGER NOT NULL DEFAULT 20"
            )
        if "activated" not in chat_setting_columns:
            self.connection.execute(
                "ALTER TABLE chat_settings "
                "ADD COLUMN activated INTEGER NOT NULL DEFAULT 0"
            )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS player_assets (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                girl_available INTEGER NOT NULL DEFAULT 1
                    CHECK (girl_available IN (0, 1)),
                mother_available INTEGER NOT NULL DEFAULT 1
                    CHECK (mother_available IN (0, 1)),
                car_available INTEGER NOT NULL DEFAULT 1
                    CHECK (car_available IN (0, 1)),
                home_available INTEGER NOT NULL DEFAULT 1
                    CHECK (home_available IN (0, 1)),
                PRIMARY KEY (chat_id, user_id)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS dice_challenges (
                chat_id INTEGER NOT NULL,
                proposal_message_id INTEGER NOT NULL,
                challenger_id INTEGER NOT NULL,
                challenger_name TEXT NOT NULL,
                opponent_id INTEGER NOT NULL,
                opponent_name TEXT NOT NULL,
                stake INTEGER NOT NULL CHECK (stake > 0),
                status TEXT NOT NULL DEFAULT 'pending',
                created_at REAL NOT NULL,
                PRIMARY KEY (chat_id, proposal_message_id)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS game_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                game_type TEXT NOT NULL CHECK (game_type IN ('casino', 'dice')),
                player_id INTEGER NOT NULL,
                player_name TEXT NOT NULL,
                opponent_id INTEGER,
                opponent_name TEXT,
                stake INTEGER NOT NULL,
                player_payout INTEGER NOT NULL,
                opponent_payout INTEGER,
                player_result INTEGER,
                opponent_result INTEGER,
                created_at REAL NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_game_history_chat_time
            ON game_history(chat_id, created_at DESC)
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS balance_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                operation_type TEXT NOT NULL
                    CHECK (operation_type IN ('transfer', 'grant', 'set')),
                actor_id INTEGER NOT NULL,
                actor_name TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                target_name TEXT NOT NULL,
                amount INTEGER NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_balance_history_chat_time
            ON balance_history(chat_id, created_at DESC)
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS work_payouts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                recipients_count INTEGER NOT NULL,
                created_at REAL NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS work_payout_recipients (
                payout_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                display_name TEXT NOT NULL,
                PRIMARY KEY (payout_id, user_id)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduler_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_topics (
                chat_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                PRIMARY KEY (chat_id, topic_id)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS casino_topics (
                chat_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                PRIMARY KEY (chat_id, topic_id)
            )
            """
        )
        # Перенести прежнюю общую настройку в основной раздел один раз.
        self.connection.execute(
            """
            INSERT OR IGNORE INTO casino_topics(chat_id, topic_id, enabled)
            SELECT chat_id, 0, enabled
            FROM chat_settings
            """
        )
        initialize_farm_schema(self.connection)
        initialize_motovskikh_schema(self.connection)
        initialize_museum_schema(self.connection)
        initialize_release_schema(self.connection)
        self.connection.commit()

    async def is_topic_enabled(self, chat_id: int, topic_id: int) -> bool:
        """Проверить работу казино в конкретном разделе активного чата."""
        async with self.lock:
            chat_row = self.connection.execute(
                """
                SELECT activated FROM chat_settings
                WHERE chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
            if chat_row is None or not chat_row["activated"]:
                return False
            topic_row = self.connection.execute(
                """
                SELECT enabled FROM casino_topics
                WHERE chat_id = ? AND topic_id = ?
                """,
                (chat_id, topic_id),
            ).fetchone()
            if topic_row is not None:
                return bool(topic_row["enabled"])
            return topic_id == 0

    async def is_chat_activated(self, chat_id: int) -> bool:
        """Проверить, активировал ли администратор бота в чате."""
        async with self.lock:
            row = self.connection.execute(
                "SELECT activated FROM chat_settings WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            return bool(row is not None and row["activated"])

    async def activate_chat(self, chat_id: int) -> None:
        """Активировать чат и разрешить обработку команд."""
        async with self.lock:
            self.connection.execute(
                """
                INSERT INTO chat_settings(chat_id, enabled, activated)
                VALUES (?, 1, 1)
                ON CONFLICT(chat_id) DO UPDATE SET
                    enabled = 1,
                    activated = 1
                """,
                (chat_id,),
            )
            self.connection.execute(
                """
                INSERT OR IGNORE INTO casino_topics(chat_id, topic_id, enabled)
                VALUES (?, 0, 1)
                """,
                (chat_id,),
            )
            self.connection.commit()

    async def set_topic_enabled(
        self, chat_id: int, topic_id: int, enabled: bool
    ) -> None:
        """Сохранить состояние казино для конкретного раздела чата."""
        async with self.lock:
            self.connection.execute(
                """
                INSERT INTO casino_topics(chat_id, topic_id, enabled)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id, topic_id)
                DO UPDATE SET enabled = excluded.enabled
                """,
                (chat_id, topic_id, int(enabled)),
            )
            self.connection.commit()

    async def is_auto_delete_enabled(self, chat_id: int) -> bool:
        """Проверить настройку автоматического удаления в чате."""
        async with self.lock:
            row = self.connection.execute(
                "SELECT auto_delete FROM chat_settings WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            return row is None or bool(row["auto_delete"])

    async def toggle_auto_delete(self, chat_id: int) -> bool:
        """Переключить автоудаление и вернуть новое состояние."""
        async with self.lock:
            row = self.connection.execute(
                "SELECT auto_delete FROM chat_settings WHERE chat_id = ?",
                (chat_id,),
            ).fetchone()
            new_state = not (row is None or bool(row["auto_delete"]))
            self.connection.execute(
                """
                INSERT INTO chat_settings(chat_id, auto_delete) VALUES (?, ?)
                ON CONFLICT(chat_id)
                DO UPDATE SET auto_delete = excluded.auto_delete
                """,
                (chat_id, int(new_state)),
            )
            self.connection.commit()
            return new_state

    async def toggle_topic_notifications(
        self, chat_id: int, topic_id: int
    ) -> bool:
        """Переключить периодические уведомления в одном топике."""
        async with self.lock:
            row = self.connection.execute(
                """
                SELECT enabled FROM notification_topics
                WHERE chat_id = ? AND topic_id = ?
                """,
                (chat_id, topic_id),
            ).fetchone()
            # Основной топик (0) включён по умолчанию, остальные выключены.
            current_state = (
                bool(row["enabled"]) if row is not None else topic_id == 0
            )
            new_state = not current_state
            self.connection.execute(
                """
                INSERT INTO notification_topics(chat_id, topic_id, enabled)
                VALUES (?, ?, ?)
                ON CONFLICT(chat_id, topic_id)
                DO UPDATE SET enabled = excluded.enabled
                """,
                (chat_id, topic_id, int(new_state)),
            )
            self.connection.commit()
            return new_state

    async def notification_topic_ids(self, chat_id: int) -> list[int]:
        """Вернуть топики, в которые нужно отправить периодическое сообщение."""
        async with self.lock:
            rows = self.connection.execute(
                """
                SELECT topic_id, enabled FROM notification_topics
                WHERE chat_id = ?
                """,
                (chat_id,),
            ).fetchall()
            states = {int(row["topic_id"]): bool(row["enabled"]) for row in rows}
            topic_ids = []
            if states.get(0, True):
                topic_ids.append(0)
            topic_ids.extend(
                sorted(
                    topic_id
                    for topic_id, enabled in states.items()
                    if topic_id != 0 and enabled
                )
            )
            return topic_ids

    async def get_bet_cooldown(self, chat_id: int) -> int:
        """Получить кулдаун ставок казино для конкретного чата."""
        async with self.lock:
            row = self.connection.execute(
                """
                SELECT bet_cooldown_seconds FROM chat_settings
                WHERE chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
            return (
                DEFAULT_BET_COOLDOWN_SECONDS
                if row is None
                else int(row["bet_cooldown_seconds"])
            )

    async def set_bet_cooldown(self, chat_id: int, seconds: int) -> None:
        """Установить неотрицательный кулдаун ставок для чата."""
        if seconds < 0:
            raise ValueError("Кулдаун не может быть отрицательным")
        async with self.lock:
            self.connection.execute(
                """
                INSERT INTO chat_settings(chat_id, bet_cooldown_seconds)
                VALUES (?, ?)
                ON CONFLICT(chat_id)
                DO UPDATE SET bet_cooldown_seconds = excluded.bet_cooldown_seconds
                """,
                (chat_id, seconds),
            )
            self.connection.commit()

    async def get_or_create(
        self, chat_id: int, user_id: int, display_name: str
    ) -> int:
        async with self.lock:
            self.connection.execute(
                """
                INSERT INTO balances(chat_id, user_id, display_name, balance)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id)
                DO UPDATE SET display_name = excluded.display_name
                """,
                (chat_id, user_id, display_name, INITIAL_BALANCE),
            )
            self.connection.commit()
            row = self.connection.execute(
                "SELECT balance FROM balances WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            ).fetchone()
            return int(row["balance"])

    async def get_max_bet(self, chat_id: int, user_id: int) -> int | None:
        """Вернуть персональный предел ставки казино, если он установлен."""
        async with self.lock:
            row = self.connection.execute(
                "SELECT max_bet FROM balances WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            ).fetchone()
            if row is None or row["max_bet"] is None:
                return None
            return int(row["max_bet"])

    async def set_max_bet(
        self,
        chat_id: int,
        user_id: int,
        display_name: str,
        max_bet: int | None,
    ) -> None:
        """Установить или снять персональный предел ставки казино."""
        if max_bet is not None and max_bet <= 0:
            raise ValueError("Максимальная ставка должна быть положительной")
        async with self.lock:
            self.connection.execute(
                """
                INSERT INTO balances(
                    chat_id, user_id, display_name, balance, max_bet
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    max_bet = excluded.max_bet
                """,
                (chat_id, user_id, display_name, INITIAL_BALANCE, max_bet),
            )
            self.connection.commit()

    async def mark_command_activity(
        self,
        chat_id: int,
        user_id: int,
        display_name: str,
    ) -> None:
        """Запомнить последнюю команду пользователя в этом чате."""
        async with self.lock:
            now = time.time()
            has_played = self.connection.execute(
                """
                SELECT 1
                FROM game_history
                WHERE chat_id = ?
                    AND (
                        player_id = ?
                        OR (
                            game_type = 'dice'
                            AND opponent_id = ?
                        )
                    )
                LIMIT 1
                """,
                (chat_id, user_id, user_id),
            ).fetchone()
            first_command_at = None if has_played is not None else now
            self.connection.execute(
                """
                INSERT INTO balances(
                    chat_id, user_id, display_name, balance,
                    last_command_at, first_command_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    last_command_at = excluded.last_command_at,
                    first_command_at = COALESCE(
                        balances.first_command_at,
                        excluded.first_command_at
                    )
                """,
                (
                    chat_id,
                    user_id,
                    display_name,
                    INITIAL_BALANCE,
                    now,
                    first_command_at,
                ),
            )
            self.connection.commit()

    async def get_assets(self, chat_id: int, user_id: int) -> dict[str, bool]:
        """Вернуть ещё не обменянные ресурсы пользователя."""
        async with self.lock:
            self.connection.execute(
                """
                INSERT INTO player_assets(chat_id, user_id) VALUES (?, ?)
                ON CONFLICT(chat_id, user_id) DO NOTHING
                """,
                (chat_id, user_id),
            )
            self.connection.commit()
            row = self.connection.execute(
                "SELECT * FROM player_assets WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            ).fetchone()
            return {
                asset_name: bool(row[column])
                for asset_name, (column, _emoji, _reward, _title) in ASSETS.items()
            }

    async def redeem_asset(
        self,
        chat_id: int,
        user_id: int,
        display_name: str,
        asset_name: str,
    ) -> tuple[bool, int]:
        """Однократно обменять ресурс на очки и вернуть новый баланс."""
        column, _emoji, reward, _title = ASSETS[asset_name]
        async with self.lock:
            self.connection.execute(
                """
                INSERT INTO balances(chat_id, user_id, display_name, balance)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id)
                DO UPDATE SET display_name = excluded.display_name
                """,
                (chat_id, user_id, display_name, INITIAL_BALANCE),
            )
            self.connection.execute(
                """
                INSERT INTO player_assets(chat_id, user_id) VALUES (?, ?)
                ON CONFLICT(chat_id, user_id) DO NOTHING
                """,
                (chat_id, user_id),
            )
            # Имя колонки берётся только из константы ASSETS, не из сообщения.
            cursor = self.connection.execute(
                f"""
                UPDATE player_assets SET {column} = 0
                WHERE chat_id = ? AND user_id = ? AND {column} = 1
                """,
                (chat_id, user_id),
            )
            if cursor.rowcount:
                self.connection.execute(
                    """
                    UPDATE balances SET balance = balance + ?
                    WHERE chat_id = ? AND user_id = ?
                    """,
                    (reward, chat_id, user_id),
                )
            self.connection.commit()
            balance = self.connection.execute(
                "SELECT balance FROM balances WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            ).fetchone()["balance"]
            return bool(cursor.rowcount), int(balance)

    async def reserve_bet(
        self,
        chat_id: int,
        user_id: int,
        display_name: str,
        bet: int | None,
        ignore_cooldown: bool = False,
    ) -> tuple[str, int, float, int]:
        """Проверить кулдаун и атомарно списать ставку.

        None вместо суммы означает ставку всего текущего баланса.
        Возвращает статус, баланс, время ожидания и фактическую ставку.
        """
        async with self.lock:
            self.connection.execute(
                """
                INSERT INTO balances(chat_id, user_id, display_name, balance)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id)
                DO UPDATE SET display_name = excluded.display_name
                """,
                (chat_id, user_id, display_name, INITIAL_BALANCE),
            )
            row = self.connection.execute(
                """
                SELECT balance, last_bet_at, max_bet FROM balances
                WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, user_id),
            ).fetchone()
            now = time.time()
            cooldown_row = self.connection.execute(
                """
                SELECT bet_cooldown_seconds FROM chat_settings
                WHERE chat_id = ?
                """,
                (chat_id,),
            ).fetchone()
            cooldown_seconds = (
                DEFAULT_BET_COOLDOWN_SECONDS
                if cooldown_row is None
                else int(cooldown_row["bet_cooldown_seconds"])
            )
            limit_status, actual_bet = casino.resolve_bet_amount(
                int(row["balance"]),
                bet,
                None if row["max_bet"] is None else int(row["max_bet"]),
            )
            if limit_status == "limit":
                self.connection.commit()
                return "limit", int(row["balance"]), 0, actual_bet

            remaining = cooldown_seconds - (now - row["last_bet_at"])
            if not ignore_cooldown and remaining > 0:
                self.connection.commit()
                return "cooldown", int(row["balance"]), remaining, 0
            if actual_bet <= 0 or row["balance"] < actual_bet:
                self.connection.commit()
                return "insufficient", int(row["balance"]), 0, 0

            self.connection.execute(
                """
                UPDATE balances
                SET balance = balance - ?, display_name = ?, last_bet_at = ?
                WHERE chat_id = ? AND user_id = ?
                """,
                (actual_bet, display_name, now, chat_id, user_id),
            )
            self.connection.commit()
            row = self.connection.execute(
                "SELECT balance FROM balances WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            ).fetchone()
            return "ok", int(row["balance"]), 0, actual_bet

    async def add_points(
        self, chat_id: int, user_id: int, amount: int
    ) -> int:
        """Начислить выплату или вернуть ставку после технической ошибки."""
        async with self.lock:
            self.connection.execute(
                """
                UPDATE balances SET balance = balance + ?
                WHERE chat_id = ? AND user_id = ?
                """,
                (amount, chat_id, user_id),
            )
            self.connection.commit()
            row = self.connection.execute(
                "SELECT balance FROM balances WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            ).fetchone()
            return int(row["balance"])

    async def transfer(
        self,
        chat_id: int,
        sender_id: int,
        sender_name: str,
        recipient_id: int,
        recipient_name: str,
        amount: int,
    ) -> tuple[str, int, int, float]:
        """Атомарно перевести очки между двумя пользователями одного чата."""
        async with self.lock:
            self.connection.execute(
                """
                INSERT INTO balances(chat_id, user_id, display_name, balance)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id)
                DO UPDATE SET display_name = excluded.display_name
                """,
                (chat_id, sender_id, sender_name, INITIAL_BALANCE),
            )
            self.connection.execute(
                """
                INSERT INTO balances(chat_id, user_id, display_name, balance)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id)
                DO UPDATE SET display_name = excluded.display_name
                """,
                (chat_id, recipient_id, recipient_name, INITIAL_BALANCE),
            )
            sender_row = self.connection.execute(
                """
                SELECT balance, first_command_at
                FROM balances
                WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, sender_id),
            ).fetchone()
            first_command_at = sender_row["first_command_at"]
            remaining = (
                NEW_PLAYER_TRANSFER_LOCK_SECONDS
                - (time.time() - float(first_command_at))
                if first_command_at is not None
                else 0
            )
            if remaining > 0:
                self.connection.commit()
                recipient_balance = self.connection.execute(
                    """
                    SELECT balance FROM balances
                    WHERE chat_id = ? AND user_id = ?
                    """,
                    (chat_id, recipient_id),
                ).fetchone()["balance"]
                return (
                    "locked",
                    int(sender_row["balance"]),
                    int(recipient_balance),
                    remaining,
                )
            cursor = self.connection.execute(
                """
                UPDATE balances SET balance = balance - ?
                WHERE chat_id = ? AND user_id = ? AND balance >= ?
                """,
                (amount, chat_id, sender_id, amount),
            )
            if cursor.rowcount:
                self.connection.execute(
                    """
                    UPDATE balances SET balance = balance + ?
                    WHERE chat_id = ? AND user_id = ?
                    """,
                    (amount, chat_id, recipient_id),
                )
                self.connection.execute(
                    """
                    INSERT INTO balance_history(
                        chat_id, operation_type,
                        actor_id, actor_name,
                        target_id, target_name,
                        amount, created_at
                    )
                    VALUES (?, 'transfer', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chat_id,
                        sender_id,
                        sender_name,
                        recipient_id,
                        recipient_name,
                        amount,
                        time.time(),
                    ),
                )
            self.connection.commit()
            sender_balance = self.connection.execute(
                "SELECT balance FROM balances WHERE chat_id = ? AND user_id = ?",
                (chat_id, sender_id),
            ).fetchone()["balance"]
            recipient_balance = self.connection.execute(
                "SELECT balance FROM balances WHERE chat_id = ? AND user_id = ?",
                (chat_id, recipient_id),
            ).fetchone()["balance"]
            status = "ok" if cursor.rowcount else "insufficient"
            return status, int(sender_balance), int(recipient_balance), 0

    async def create_dice_challenge(
        self,
        chat_id: int,
        proposal_message_id: int,
        challenger_id: int,
        challenger_name: str,
        opponent_id: int,
        opponent_name: str,
        stake: int,
    ) -> None:
        """Сохранить предложение игры, на которое должен ответить соперник."""
        async with self.lock:
            self.connection.execute(
                """
                INSERT INTO dice_challenges(
                    chat_id, proposal_message_id,
                    challenger_id, challenger_name,
                    opponent_id, opponent_name,
                    stake, status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    chat_id,
                    proposal_message_id,
                    challenger_id,
                    challenger_name,
                    opponent_id,
                    opponent_name,
                    stake,
                    time.time(),
                ),
            )
            self.connection.commit()

    async def accept_dice_challenge(
        self,
        chat_id: int,
        proposal_message_id: int,
        accepting_user_id: int,
        expires_before: float,
    ) -> tuple[str, dict | None]:
        """Принять вызов и атомарно списать ставку у обоих игроков."""
        async with self.lock:
            row = self.connection.execute(
                """
                SELECT * FROM dice_challenges
                WHERE chat_id = ? AND proposal_message_id = ?
                  AND status = 'pending'
                """,
                (chat_id, proposal_message_id),
            ).fetchone()
            if row is None:
                return "not_found", None
            challenge = dict(row)
            if challenge["created_at"] <= expires_before:
                self.connection.execute(
                    """
                    UPDATE dice_challenges SET status = 'expired'
                    WHERE chat_id = ? AND proposal_message_id = ?
                      AND status = 'pending'
                    """,
                    (chat_id, proposal_message_id),
                )
                self.connection.commit()
                return "expired", challenge
            if challenge["opponent_id"] != accepting_user_id:
                return "wrong_user", None

            for user_id, user_name in (
                (challenge["challenger_id"], challenge["challenger_name"]),
                (challenge["opponent_id"], challenge["opponent_name"]),
            ):
                self.connection.execute(
                    """
                    INSERT INTO balances(chat_id, user_id, display_name, balance)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(chat_id, user_id)
                    DO UPDATE SET display_name = excluded.display_name
                    """,
                    (chat_id, user_id, user_name, INITIAL_BALANCE),
                )

            balances = {
                row["user_id"]: row["balance"]
                for row in self.connection.execute(
                    """
                    SELECT user_id, balance FROM balances
                    WHERE chat_id = ? AND user_id IN (?, ?)
                    """,
                    (
                        chat_id,
                        challenge["challenger_id"],
                        challenge["opponent_id"],
                    ),
                )
            }
            if balances[challenge["challenger_id"]] < challenge["stake"]:
                self.connection.commit()
                return "challenger_funds", challenge
            if balances[challenge["opponent_id"]] < challenge["stake"]:
                self.connection.commit()
                return "opponent_funds", challenge

            self.connection.execute(
                """
                UPDATE balances SET balance = balance - ?
                WHERE chat_id = ? AND user_id IN (?, ?)
                """,
                (
                    challenge["stake"],
                    chat_id,
                    challenge["challenger_id"],
                    challenge["opponent_id"],
                ),
            )
            self.connection.execute(
                """
                UPDATE dice_challenges SET status = 'playing'
                WHERE chat_id = ? AND proposal_message_id = ?
                """,
                (chat_id, proposal_message_id),
            )
            self.connection.commit()
            return "ok", challenge

    async def expire_dice_challenge(
        self, chat_id: int, proposal_message_id: int
    ) -> bool:
        """Пометить непринятый вызов истёкшим."""
        async with self.lock:
            cursor = self.connection.execute(
                """
                UPDATE dice_challenges SET status = 'expired'
                WHERE chat_id = ? AND proposal_message_id = ?
                  AND status = 'pending'
                """,
                (chat_id, proposal_message_id),
            )
            self.connection.commit()
            return bool(cursor.rowcount)

    async def get_pending_dice_challenges(self) -> list[sqlite3.Row]:
        """Вернуть вызовы, таймеры которых нужно восстановить после запуска."""
        async with self.lock:
            return self.connection.execute(
                """
                SELECT chat_id, proposal_message_id, created_at
                FROM dice_challenges WHERE status = 'pending'
                """
            ).fetchall()

    async def finish_dice_challenge(
        self,
        chat_id: int,
        proposal_message_id: int,
        winner_id: int,
    ) -> int:
        """Передать победителю банк и закрыть игру."""
        async with self.lock:
            challenge = self.connection.execute(
                """
                SELECT stake FROM dice_challenges
                WHERE chat_id = ? AND proposal_message_id = ?
                  AND status = 'playing'
                """,
                (chat_id, proposal_message_id),
            ).fetchone()
            if challenge is None:
                raise RuntimeError("Игра в кости уже завершена")
            self.connection.execute(
                """
                UPDATE balances SET balance = balance + ?
                WHERE chat_id = ? AND user_id = ?
                """,
                (challenge["stake"] * 2, chat_id, winner_id),
            )
            self.connection.execute(
                """
                UPDATE dice_challenges SET status = 'completed'
                WHERE chat_id = ? AND proposal_message_id = ?
                """,
                (chat_id, proposal_message_id),
            )
            self.connection.commit()
            return int(
                self.connection.execute(
                    """
                    SELECT balance FROM balances
                    WHERE chat_id = ? AND user_id = ?
                    """,
                    (chat_id, winner_id),
                ).fetchone()["balance"]
            )

    async def refund_dice_challenge(
        self, chat_id: int, proposal_message_id: int
    ) -> None:
        """Вернуть обе ставки, если Telegram не смог отправить кубики."""
        async with self.lock:
            challenge = self.connection.execute(
                """
                SELECT challenger_id, opponent_id, stake
                FROM dice_challenges
                WHERE chat_id = ? AND proposal_message_id = ?
                  AND status = 'playing'
                """,
                (chat_id, proposal_message_id),
            ).fetchone()
            if challenge is None:
                return
            self.connection.execute(
                """
                UPDATE balances SET balance = balance + ?
                WHERE chat_id = ? AND user_id IN (?, ?)
                """,
                (
                    challenge["stake"],
                    chat_id,
                    challenge["challenger_id"],
                    challenge["opponent_id"],
                ),
            )
            self.connection.execute(
                """
                UPDATE dice_challenges SET status = 'failed'
                WHERE chat_id = ? AND proposal_message_id = ?
                """,
                (chat_id, proposal_message_id),
            )
            self.connection.commit()

    async def record_casino_game(
        self,
        chat_id: int,
        player_id: int,
        player_name: str,
        stake: int,
        payout: int,
        slot_value: int,
    ) -> None:
        """Записать завершённую ставку казино."""
        async with self.lock:
            self.connection.execute(
                """
                INSERT INTO game_history(
                    chat_id, game_type, player_id, player_name,
                    stake, player_payout, player_result, created_at
                )
                VALUES (?, 'casino', ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    player_id,
                    player_name,
                    stake,
                    payout,
                    slot_value,
                    time.time(),
                ),
            )
            self.connection.commit()

    async def record_dice_game(
        self,
        chat_id: int,
        challenge: dict,
        challenger_roll: int,
        opponent_roll: int,
    ) -> None:
        """Записать партию в кости одной строкой без дублирования."""
        challenger_won = challenger_roll > opponent_roll
        bank = challenge["stake"] * 2
        async with self.lock:
            self.connection.execute(
                """
                INSERT INTO game_history(
                    chat_id, game_type,
                    player_id, player_name,
                    opponent_id, opponent_name,
                    stake, player_payout, opponent_payout,
                    player_result, opponent_result, created_at
                )
                VALUES (?, 'dice', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    challenge["challenger_id"],
                    challenge["challenger_name"],
                    challenge["opponent_id"],
                    challenge["opponent_name"],
                    challenge["stake"],
                    bank if challenger_won else 0,
                    0 if challenger_won else bank,
                    challenger_roll,
                    opponent_roll,
                    time.time(),
                ),
            )
            self.connection.commit()

    async def get_next_work_payout_at(self) -> float:
        """Вернуть время следующего фонового начисления."""
        async with self.lock:
            row = self.connection.execute(
                """
                SELECT value FROM scheduler_state
                WHERE key = 'next_work_payout_at'
                """
            ).fetchone()
            return float(row["value"]) if row else 0.0

    async def apply_work_payout(
        self,
        target_chat_id: int | None = None,
        advance_schedule: bool = True,
    ) -> list[dict]:
        """Начислить оплату всем чатам либо одному указанному чату."""
        async with self.lock:
            now = time.time()
            active_since = now - WORK_ACTIVITY_TIMEOUT_SECONDS
            chat_filter = (
                "" if target_chat_id is None else "AND b.chat_id = ?"
            )
            parameters = (
                (active_since,)
                if target_chat_id is None
                else (active_since, target_chat_id)
            )
            recipients = self.connection.execute(
                f"""
                SELECT b.chat_id, b.user_id, b.display_name
                FROM balances AS b
                JOIN chat_settings AS s ON s.chat_id = b.chat_id
                WHERE s.activated = 1
                    AND b.last_command_at >= ?
                    {chat_filter}
                ORDER BY b.chat_id, b.user_id
                """,
                parameters,
            ).fetchall()
            if not recipients:
                return []

            recipients_by_chat: dict[int, list[sqlite3.Row]] = {}
            for row in recipients:
                recipients_by_chat.setdefault(row["chat_id"], []).append(row)

            zero_balance_by_chat: dict[int, list[sqlite3.Row]] = {}
            for chat_id in recipients_by_chat:
                zero_balance_by_chat[chat_id] = self.connection.execute(
                    """
                    SELECT
                        b.user_id,
                        b.display_name,
                        COALESCE(MAX(g.created_at), 0) AS last_game_at
                    FROM balances AS b
                    LEFT JOIN game_history AS g
                        ON g.chat_id = b.chat_id
                        AND (
                            g.player_id = b.user_id
                            OR g.opponent_id = b.user_id
                        )
                    WHERE b.chat_id = ?
                        AND b.balance = 0
                        AND b.last_command_at >= ?
                    GROUP BY b.user_id, b.display_name
                    ORDER BY last_game_at DESC, b.user_id DESC
                    LIMIT 10
                    """,
                    (chat_id, active_since),
                ).fetchall()

            self.connection.executemany(
                """
                UPDATE balances SET balance = balance + ?
                WHERE chat_id = ? AND user_id = ?
                """,
                [
                    (WORK_PAYOUT_AMOUNT, row["chat_id"], row["user_id"])
                    for row in recipients
                ],
            )

            payouts = []
            for chat_id, chat_recipients in recipients_by_chat.items():
                cursor = self.connection.execute(
                    """
                    INSERT INTO work_payouts(
                        chat_id, amount, recipients_count, created_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        chat_id,
                        WORK_PAYOUT_AMOUNT,
                        len(chat_recipients),
                        now,
                    ),
                )
                payout_id = cursor.lastrowid
                self.connection.executemany(
                    """
                    INSERT INTO work_payout_recipients(
                        payout_id, user_id, display_name
                    )
                    VALUES (?, ?, ?)
                    """,
                    [
                        (payout_id, row["user_id"], row["display_name"])
                        for row in chat_recipients
                    ],
                )
                payouts.append(
                    {
                        "chat_id": chat_id,
                        "recipients_count": len(chat_recipients),
                        "zero_balance_users": [
                            {
                                "user_id": int(row["user_id"]),
                                "display_name": row["display_name"],
                            }
                            for row in zero_balance_by_chat[chat_id]
                        ],
                    }
                )

            if advance_schedule:
                self.connection.execute(
                    """
                    INSERT INTO scheduler_state(key, value)
                    VALUES ('next_work_payout_at', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (str(now + WORK_PAYOUT_INTERVAL_SECONDS),),
                )
            self.connection.commit()
            return payouts

    async def get_activity_history(
        self, chat_id: int, user_id: int | None
    ) -> list[dict]:
        """Получить единый журнал игр и операций с балансом."""
        async with self.lock:
            if user_id is None:
                game_rows = self.connection.execute(
                    """
                    SELECT * FROM game_history
                    WHERE chat_id = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (chat_id, HISTORY_LIMIT),
                ).fetchall()
                balance_rows = self.connection.execute(
                    """
                    SELECT * FROM balance_history
                    WHERE chat_id = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (chat_id, HISTORY_LIMIT),
                ).fetchall()
                work_rows = self.connection.execute(
                    """
                    SELECT * FROM work_payouts
                    WHERE chat_id = ?
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (chat_id, HISTORY_LIMIT),
                ).fetchall()
            else:
                game_rows = self.connection.execute(
                    """
                    SELECT * FROM game_history
                    WHERE chat_id = ? AND (player_id = ? OR opponent_id = ?)
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (chat_id, user_id, user_id, HISTORY_LIMIT),
                ).fetchall()
                balance_rows = self.connection.execute(
                    """
                    SELECT * FROM balance_history
                    WHERE chat_id = ? AND (actor_id = ? OR target_id = ?)
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (chat_id, user_id, user_id, HISTORY_LIMIT),
                ).fetchall()
                work_rows = self.connection.execute(
                    """
                    SELECT p.*
                    FROM work_payouts AS p
                    JOIN work_payout_recipients AS r ON r.payout_id = p.id
                    WHERE p.chat_id = ? AND r.user_id = ?
                    ORDER BY p.created_at DESC LIMIT ?
                    """,
                    (chat_id, user_id, HISTORY_LIMIT),
                ).fetchall()

            activities = [
                {"activity_kind": "game", **dict(row)} for row in game_rows
            ]
            activities.extend(
                {"activity_kind": "balance", **dict(row)}
                for row in balance_rows
            )
            activities.extend(
                {"activity_kind": "work", **dict(row)}
                for row in work_rows
            )
            activities.sort(key=lambda row: row["created_at"], reverse=True)
            return activities[:HISTORY_LIMIT]

    async def get_casino_analytics(
        self, chat_id: int, user_id: int | None = None
    ) -> dict | None:
        """Посчитать показатели слота для чата или отдельного игрока."""
        async with self.lock:
            user_filter = "" if user_id is None else "AND player_id = ?"
            parameters = (chat_id,) if user_id is None else (chat_id, user_id)
            row = self.connection.execute(
                f"""
                SELECT
                    COUNT(*) AS games,
                    COALESCE(SUM(stake), 0) AS stakes,
                    COALESCE(SUM(player_payout), 0) AS payouts,
                    COALESCE(SUM(stake - player_payout), 0)
                        AS casino_profit,
                    COALESCE(SUM(
                        CASE WHEN player_payout > 0 THEN 1 ELSE 0 END
                    ), 0) AS winning_games,
                    COALESCE(SUM(
                        CASE WHEN player_payout = 0 THEN 1 ELSE 0 END
                    ), 0) AS losing_games,
                    COALESCE(SUM(
                        CASE WHEN player_payout = 0 THEN stake ELSE 0 END
                    ), 0) AS lost_stakes,
                    MIN(created_at) AS first_game,
                    MAX(created_at) AS last_game
                FROM game_history
                WHERE chat_id = ? AND game_type = 'casino'
                    {user_filter}
                """,
                parameters,
            ).fetchone()
            return dict(row) if row["games"] else None

    async def get_dice_analytics(
        self, chat_id: int, user_id: int
    ) -> dict | None:
        """Посчитать показатели игрока в завершённых партиях в кости."""
        async with self.lock:
            row = self.connection.execute(
                """
                SELECT
                    COUNT(*) AS games,
                    COALESCE(SUM(stake), 0) AS stakes,
                    COALESCE(SUM(
                        CASE
                            WHEN player_id = ? THEN player_payout
                            ELSE opponent_payout
                        END
                    ), 0) AS payouts,
                    COALESCE(SUM(
                        CASE
                            WHEN (
                                player_id = ? AND player_payout > 0
                            ) OR (
                                opponent_id = ? AND opponent_payout > 0
                            )
                            THEN 1 ELSE 0
                        END
                    ), 0) AS wins,
                    MIN(created_at) AS first_game,
                    MAX(created_at) AS last_game
                FROM game_history
                WHERE chat_id = ?
                    AND game_type = 'dice'
                    AND (player_id = ? OR opponent_id = ?)
                """,
                (
                    user_id,
                    user_id,
                    user_id,
                    chat_id,
                    user_id,
                    user_id,
                ),
            ).fetchone()
            return dict(row) if row["games"] else None

    async def seed_users(
        self, chat_id: int, users: list[tuple[int, str]], reset: bool
    ) -> int:
        async with self.lock:
            if reset:
                self.connection.execute(
                    "DELETE FROM balances WHERE chat_id = ?", (chat_id,)
                )
            self.connection.executemany(
                """
                INSERT INTO balances(chat_id, user_id, display_name, balance)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id)
                DO UPDATE SET display_name = excluded.display_name
                """,
                [
                    (chat_id, user_id, display_name, INITIAL_BALANCE)
                    for user_id, display_name in users
                ],
            )
            self.connection.commit()
            return len(users)

    async def known_user_count(self, chat_id: int) -> int:
        """Посчитать пользователей, уже известных боту в этом чате."""
        async with self.lock:
            return int(
                self.connection.execute(
                    "SELECT COUNT(*) FROM balances WHERE chat_id = ?",
                    (chat_id,),
                ).fetchone()[0]
            )

    async def reset_known_balances(self, chat_id: int) -> int:
        """Сбросить баланс всех известных боту пользователей чата."""
        async with self.lock:
            cursor = self.connection.execute(
                """
                UPDATE balances
                SET balance = ?, last_bet_at = 0
                WHERE chat_id = ?
                """,
                (INITIAL_BALANCE, chat_id),
            )
            self.connection.commit()
            return int(cursor.rowcount)

    async def change(
        self,
        chat_id: int,
        user_id: int,
        display_name: str,
        amount: int,
        set_value: bool,
        actor_id: int,
        actor_name: str,
    ) -> int:
        async with self.lock:
            current = self.connection.execute(
                "SELECT balance FROM balances WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            ).fetchone()
            old_balance = int(current["balance"]) if current else INITIAL_BALANCE
            new_balance = amount if set_value else old_balance + amount
            if new_balance < 0:
                raise ValueError("Баланс не может быть отрицательным")
            self.connection.execute(
                """
                INSERT INTO balances(chat_id, user_id, display_name, balance)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    balance = excluded.balance
                """,
                (chat_id, user_id, display_name, new_balance),
            )
            self.connection.execute(
                """
                INSERT INTO balance_history(
                    chat_id, operation_type,
                    actor_id, actor_name,
                    target_id, target_name,
                    amount, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    "set" if set_value else "grant",
                    actor_id,
                    actor_name,
                    user_id,
                    display_name,
                    amount,
                    time.time(),
                ),
            )
            self.connection.commit()
            return new_balance

    async def top(self, chat_id: int) -> list[sqlite3.Row]:
        async with self.lock:
            return self.connection.execute(
                """
                SELECT display_name, balance
                FROM balances WHERE chat_id = ?
                ORDER BY balance DESC, display_name ASC
                """,
                (chat_id,),
            ).fetchall()

    async def top_casino_rtp(
        self, chat_id: int, descending: bool = True
    ) -> list[sqlite3.Row]:
        """Вернуть игроков чата, отсортированных по фактическому RTP."""
        direction = "DESC" if descending else "ASC"
        async with self.lock:
            return self.connection.execute(
                f"""
                SELECT
                    history.player_id,
                    COALESCE(
                        balances.display_name,
                        MAX(history.player_name)
                    ) AS display_name,
                    COUNT(*) AS games,
                    SUM(history.stake) AS stakes,
                    SUM(history.player_payout) AS payouts,
                    100.0 * SUM(history.player_payout)
                        / SUM(history.stake) AS rtp
                FROM game_history AS history
                LEFT JOIN balances
                    ON balances.chat_id = history.chat_id
                    AND balances.user_id = history.player_id
                WHERE history.chat_id = ?
                    AND history.game_type = 'casino'
                GROUP BY history.player_id, balances.display_name
                ORDER BY rtp {direction}, games DESC, display_name ASC
                """,
                (chat_id,),
            ).fetchall()


def display_name(user: User) -> str:
    """Получить удобное имя без обязательного username."""
    full_name = " ".join(part for part in (user.first_name, user.last_name) if part)
    return full_name or (f"@{user.username}" if user.username else str(user.id))


def format_points(value: int, signed: bool = False) -> str:
    """Отформатировать очки с пробелами и необязательным знаком."""
    text = f"{abs(value):,}".replace(",", " ")
    if not signed:
        return text
    if value > 0:
        return f"+{text}"
    if value < 0:
        return f"−{text}"
    return "0"


def analytics_period(analytics: dict) -> str:
    """Отформатировать период накопленной игровой статистики."""
    first_game = datetime.fromtimestamp(
        analytics["first_game"]
    ).strftime("%d.%m.%Y %H:%M")
    last_game = datetime.fromtimestamp(
        analytics["last_game"]
    ).strftime("%d.%m.%Y %H:%M")
    return f"{first_game} — {last_game}"


def format_chat_casino_analytics(analytics: dict) -> str:
    """Подготовить общую администраторскую аналитику слота."""
    stakes = int(analytics["stakes"])
    payouts = int(analytics["payouts"])
    profit = int(analytics["casino_profit"])
    rtp = payouts / stakes * 100 if stakes else 0.0
    return (
        "📊 Аналитика казино\n"
        f"Период: {analytics_period(analytics)}\n"
        f"Сыграно вращений: {analytics['games']}\n"
        f"Общая сумма ставок: {format_points(stakes)}\n"
        "Полностью проиграно на неудачных вращениях: "
        f"{format_points(int(analytics['lost_stakes']))}\n"
        f"Выплачено выигрышей: {format_points(payouts)}\n"
        f"Чистый результат казино: {format_points(profit, signed=True)}\n"
        f"Выигрышных вращений: {analytics['winning_games']}\n"
        f"Проигрышных вращений: {analytics['losing_games']}\n"
        f"Фактический RTP: {rtp:.2f}%"
    )


def format_player_analytics(
    player_name: str,
    casino_analytics: dict | None,
    dice_analytics: dict | None,
) -> str:
    """Подготовить раздельную статистику игрока по слоту и костям."""
    lines = [f"📊 Аналитика игрока: {player_name}", "", "🎰 Казино"]
    if casino_analytics is None:
        lines.append("Игр пока нет.")
    else:
        stakes = int(casino_analytics["stakes"])
        payouts = int(casino_analytics["payouts"])
        net = payouts - stakes
        rtp = payouts / stakes * 100 if stakes else 0.0
        lines.extend(
            (
                f"Период: {analytics_period(casino_analytics)}",
                f"Вращений: {casino_analytics['games']}",
                f"Ставки: {format_points(stakes)}",
                f"Выплаты: {format_points(payouts)}",
                f"Результат игрока: {format_points(net, signed=True)}",
                f"Выигрышей: {casino_analytics['winning_games']}",
                f"Проигрышей: {casino_analytics['losing_games']}",
                f"RTP игрока: {rtp:.2f}%",
            )
        )

    lines.extend(("", "🎲 Кости"))
    if dice_analytics is None:
        lines.append("Игр пока нет.")
    else:
        games = int(dice_analytics["games"])
        stakes = int(dice_analytics["stakes"])
        payouts = int(dice_analytics["payouts"])
        wins = int(dice_analytics["wins"])
        lines.extend(
            (
                f"Период: {analytics_period(dice_analytics)}",
                f"Партий: {games}",
                f"Ставки: {format_points(stakes)}",
                f"Выплаты: {format_points(payouts)}",
                f"Результат игрока: {format_points(payouts - stakes, signed=True)}",
                f"Побед: {wins}",
                f"Поражений: {games - wins}",
                f"Процент побед: {wins / games * 100:.2f}%",
            )
        )
    return "\n".join(lines)


def format_history(rows: list[dict], viewer_id: int | None) -> str:
    """Подготовить общий журнал игр и операций с балансом."""
    lines = [f"📜 Последние события ({len(rows)}):"]
    for row in rows:
        played_at = datetime.fromtimestamp(row["created_at"]).strftime("%d.%m %H:%M")
        if row["activity_kind"] == "work":
            if viewer_id is None:
                description = (
                    f"начислено {row['recipients_count']} пользователям "
                    f"по {format_points(int(row['amount']))}"
                )
            else:
                description = (
                    f"работа: {format_points(int(row['amount']), signed=True)}"
                )
            lines.append(f"🛠 {played_at} · {description}")
            continue

        if row["activity_kind"] == "balance":
            amount = int(row["amount"])
            if row["operation_type"] == "transfer":
                if viewer_id is not None and row["target_id"] == viewer_id:
                    description = (
                        f"получено от {row['actor_name']}: "
                        f"{format_points(amount, signed=True)}"
                    )
                elif viewer_id is not None:
                    description = (
                        f"передано {row['target_name']}: "
                        f"{format_points(-amount, signed=True)}"
                    )
                else:
                    description = (
                        f"{row['actor_name']} → {row['target_name']}: "
                        f"{format_points(amount)}"
                    )
                lines.append(f"🤝 {played_at} · {description}")
            elif row["operation_type"] == "grant":
                lines.append(
                    f"🎁 {played_at} · {row['actor_name']} выдал "
                    f"{row['target_name']} {format_points(amount)}"
                )
            else:
                lines.append(
                    f"⚙️ {played_at} · {row['actor_name']} установил баланс "
                    f"{row['target_name']}: {format_points(amount)}"
                )
            continue

        stake = int(row["stake"])
        if row["game_type"] == "casino":
            payout = int(row["player_payout"])
            net = payout - stake
            lines.append(
                f"🎰 {played_at} · {row['player_name']}: "
                f"ставка {format_points(stake)}, "
                f"выигрыш {format_points(payout)}, "
                f"итог {format_points(net, signed=True)}"
            )
            continue

        if viewer_id is not None and row["opponent_id"] == viewer_id:
            player_name = row["opponent_name"]
            opponent_name = row["player_name"]
            player_roll = row["opponent_result"]
            opponent_roll = row["player_result"]
            payout = int(row["opponent_payout"])
        else:
            player_name = row["player_name"]
            opponent_name = row["opponent_name"]
            player_roll = row["player_result"]
            opponent_roll = row["opponent_result"]
            payout = int(row["player_payout"])

        if viewer_id is None:
            winner_name = (
                row["player_name"]
                if row["player_payout"]
                else row["opponent_name"]
            )
            lines.append(
                f"🎲 {played_at} · {row['player_name']} {row['player_result']}–"
                f"{row['opponent_result']} {row['opponent_name']}: "
                f"ставка {format_points(stake)}, победитель {winner_name}, "
                f"выигрыш {format_points(stake * 2)}"
            )
        else:
            net = payout - stake
            lines.append(
                f"🎲 {played_at} · {player_name} против {opponent_name} "
                f"{player_roll}–{opponent_roll}: "
                f"ставка {format_points(stake)}, "
                f"выигрыш {format_points(payout)}, "
                f"итог {format_points(net, signed=True)}"
            )
    return "\n".join(lines)


client = TelegramClient(str(DATA_DIR / SESSION_NAME), API_ID, API_HASH)
store = BalanceStore(
    Path(os.getenv("CASINO_DATABASE_PATH", DATA_DIR / "casino.sqlite3"))
)
admin_id = ADMIN_ID
bot_username = ""
cleanup_tasks: set[asyncio.Task] = set()


def track_background_task(task: asyncio.Task) -> None:
    """Keep a background task alive until it finishes."""
    cleanup_tasks.add(task)
    task.add_done_callback(cleanup_tasks.discard)


def telegram_mention(user_id: int, display_name_value: str) -> str:
    """Сформировать Markdown-упоминание пользователя без username."""
    safe_name = re.sub(r"([\\\[\]])", r"\\\1", display_name_value)
    return f"[{safe_name}](tg://user?id={user_id})"


def work_payout_text(zero_balance_users: list[dict]) -> str:
    """Дополнить уведомление упоминаниями недавних игроков без очков."""
    if not zero_balance_users:
        return WORK_PAYOUT_MESSAGE
    mentions = "\n".join(
        f"{telegram_mention(user['user_id'], user['display_name'])} "
        "— вас особенно касается!"
        for user in zero_balance_users
    )
    return f"{WORK_PAYOUT_MESSAGE}\n\n{mentions}"


async def work_payout_loop() -> None:
    """Раз в полчаса начислять очки всем известным игрокам активных чатов."""
    while True:
        next_payout_at = await store.get_next_work_payout_at()
        delay = next_payout_at - time.time()
        if delay > 0:
            await asyncio.sleep(delay)

        payouts = await store.apply_work_payout()
        if not payouts:
            # До первой активации чата начислять некому. Повторяем проверку,
            # не сдвигая расписание, чтобы первое начисление было немедленным.
            await asyncio.sleep(5)
            continue

        await send_work_payout_notifications(payouts)


async def pet_hatching_loop() -> None:
    """В фоновом режиме вылуплять созревшие яйца питомцев."""
    while True:
        await store.hatch_due_pet_eggs()
        await store.expire_pet_transfers(
            time.time() - farm.PET_TRANSFER_TTL_SECONDS
        )
        await asyncio.sleep(PET_HATCH_CHECK_INTERVAL_SECONDS)


async def museum_income_loop() -> None:
    """Раз в минуту начислять все завершённые сутки дохода музеев."""
    while True:
        await store.accrue_all_museum_income()
        await asyncio.sleep(MUSEUM_INCOME_CHECK_INTERVAL_SECONDS)


async def announce_release() -> None:
    """Опубликовать и попытаться закрепить текущую сводку изменений."""
    targets = await store.pending_release_targets(RELEASE_ID)
    for chat_id, topic_id in targets:
        try:
            message = await client.send_message(
                chat_id,
                RELEASE_SUMMARY,
                reply_to=topic_id or None,
                parse_mode=None,
            )
        except Exception as error:
            print(
                "Не удалось отправить сводку обновления "
                f"в чат {chat_id}, топик {topic_id}: {error}"
            )
            continue

        pinned = False
        try:
            await client.pin_message(chat_id, message, notify=False)
            pinned = True
        except Exception as error:
            print(
                "Не удалось закрепить сводку обновления "
                f"в чате {chat_id}, топике {topic_id}: {error}"
            )
        await store.mark_release_announced(
            chat_id,
            topic_id,
            RELEASE_ID,
            message.id,
            pinned,
        )


async def send_work_payout_notifications(payouts: list[dict]) -> int:
    """Уведомить о зарплате только чаты с игроками без очков."""
    sent_count = 0
    for payout in payouts:
        if not payout["zero_balance_users"]:
            continue
        topic_ids = await store.notification_topic_ids(payout["chat_id"])
        for topic_id in topic_ids:
            try:
                await client.send_message(
                    payout["chat_id"],
                    work_payout_text(payout["zero_balance_users"]),
                    reply_to=topic_id or None,
                )
                sent_count += 1
            except Exception:
                # Ошибка отправки в один топик не останавливает остальные.
                pass
    return sent_count


async def delete_messages_later(
    chat, chat_id: int, message_ids: tuple[int, ...]
) -> None:
    """Удалить исходящие игровые сообщения через заданное время."""
    if not await store.is_auto_delete_enabled(chat_id):
        return
    await asyncio.sleep(GAME_MESSAGE_TTL_SECONDS)
    if not await store.is_auto_delete_enabled(chat_id):
        return
    try:
        await client.delete_messages(chat, list(message_ids))
    except Exception:
        # Недостаток прав или уже удалённое сообщение не должны останавливать бота.
        pass


def schedule_delete(chat, *messages) -> None:
    """Запланировать удаление и удерживать ссылку на фоновую задачу."""
    message_ids = tuple(
        message.id for message in messages if message is not None
    )
    if not message_ids:
        return
    chat_id = messages[0].chat_id
    task = asyncio.create_task(
        delete_messages_later(chat, chat_id, message_ids)
    )
    cleanup_tasks.add(task)
    task.add_done_callback(cleanup_tasks.discard)


def is_tagged_start(text: str) -> bool:
    """Распознать сообщение с упоминанием бота и словом «старт»."""
    if not bot_username:
        return False
    mention = re.search(
        rf"(?i)(?<!\w)@{re.escape(bot_username)}(?!\w)",
        text,
    )
    return bool(mention and re.search(r"(?iu)\bстарт\b", text))


def is_bot_command(text: str) -> bool:
    """Распознать сообщения, адресованные одному из обработчиков бота."""
    return bool(
        re.match(
            r"(?iu)^(?:каз(?:\s|$)|кз\s+кд(?:\s|$)|"
            r"ферма(?:\s|$)|музей(?:\s|$)|кости(?:\s|$)|зг(?:\s|$))",
            text.strip(),
        )
    )


@client.on(events.NewMessage)
async def activation_gate(event) -> None:
    """Не пропускать события чата до явной активации администратором."""
    if not event.is_group:
        if re.match(
            r"(?i)^/motovskikh_auth(?:@\w+)?\s*$",
            event.raw_text or "",
        ):
            return
        raise events.StopPropagation
    if casino.is_forwarded_message(event.message):
        raise events.StopPropagation

    sender = await event.get_sender()
    if (
        isinstance(sender, User)
        and sender.id == admin_id
        and is_tagged_start(event.raw_text or "")
    ):
        await store.activate_chat(event.chat_id)
        await store.mark_command_activity(
            event.chat_id,
            sender.id,
            display_name(sender),
        )
        await event.reply("▶️ Бот активирован и принимает команды в этом чате.")
        raise events.StopPropagation

    if not await store.is_chat_activated(event.chat_id):
        raise events.StopPropagation

    if (
        isinstance(sender, User)
        and not sender.bot
        and is_bot_command(event.raw_text or "")
    ):
        await store.mark_command_activity(
            event.chat_id,
            sender.id,
            display_name(sender),
        )


async def target_from_command(event, args: list[str]) -> tuple[User, int] | None:
    """Разобрать цель админ-команды: reply либо пара <user_id> <очки>."""
    if event.is_reply and len(args) == 1:
        replied = await event.get_reply_message()
        sender = await replied.get_sender()
        if isinstance(sender, User):
            return sender, int(args[0])
    if len(args) == 2:
        entity = await client.get_entity(int(args[0]))
        if isinstance(entity, User):
            return entity, int(args[1])
    return None


async def casino_command(event) -> None:
    """Единый обработчик игровых и административных команд."""
    if not event.is_group:
        await event.reply("Эта игра работает только в групповых чатах.")
        return

    sender = await event.get_sender()
    chat = await event.get_chat()
    if not isinstance(sender, User) or not isinstance(chat, (Chat, Channel)):
        return

    command_line = (event.pattern_match.group(1) or "помощь").strip()
    parts = command_line.split()
    command = parts[0].lower()
    args = parts[1:]
    chat_id = event.chat_id
    topic_id = casino.message_topic_id(event.message)
    name = display_name(sender)

    topic_enabled = await store.is_topic_enabled(chat_id, topic_id)

    # Управление доступно администратору даже в остановленном чате.
    if command in {"стоп", "старт"}:
        if sender.id != admin_id:
            if topic_enabled:
                await event.reply("Эта команда доступна только администратору.")
            return
        enabled = command == "старт"
        await store.set_topic_enabled(chat_id, topic_id, enabled)
        if enabled:
            await event.reply(
                "▶️ Казино и игра в кости запущены в этом разделе."
            )
        else:
            await event.reply(
                "⏸ Казино и игра в кости остановлены в этом разделе."
            )
        return

    if command == "автоудаление":
        if sender.id != admin_id:
            if topic_enabled:
                await event.reply("Эта команда доступна только администратору.")
            return
        auto_delete_enabled = await store.toggle_auto_delete(chat_id)
        state_text = "включено" if auto_delete_enabled else "выключено"
        await event.reply(
            f"🧹 Автоматическое удаление сообщений {state_text} для этого чата."
        )
        return

    if command == "кд":
        if sender.id != admin_id:
            if topic_enabled:
                await event.reply("Эта команда доступна только администратору.")
            return
        if len(args) != 1 or not args[0].isdigit():
            await event.reply("Формат: `кз кд 20` или `каз кд 20`.")
            return
        cooldown_seconds = int(args[0])
        await store.set_bet_cooldown(chat_id, cooldown_seconds)
        if cooldown_seconds:
            await event.reply(
                f"⏱ Кулдаун казино установлен: {cooldown_seconds} сек."
            )
        else:
            await event.reply("⏱ Кулдаун казино отключён для этого чата.")
        return

    # В остановленном чате бот молча игнорирует все остальные команды.
    if not topic_enabled:
        return

    if command == "уведы":
        if sender.id != admin_id:
            await event.reply("Эта команда доступна только администратору.")
            return
        if args:
            await event.reply("Формат: `каз уведы`.")
            return
        topic_id = casino.message_topic_id(event.message)
        notifications_enabled = await store.toggle_topic_notifications(
            chat_id, topic_id
        )
        state_text = "включены" if notifications_enabled else "выключены"
        await event.reply(
            f"🔔 Периодические уведомления {state_text} в этом топике."
        )
        return

    if command == "зп":
        if sender.id != admin_id:
            await event.reply("Эта команда доступна только администратору.")
            return
        if args:
            await event.reply("Формат: `каз зп`.")
            return
        payouts = await store.apply_work_payout(
            target_chat_id=chat_id,
            advance_schedule=False,
        )
        if not payouts:
            await event.reply("В этом чате пока нет известных игроков.")
            return
        notification_topics = await store.notification_topic_ids(chat_id)
        sent_count = await send_work_payout_notifications(payouts)
        has_zero_balance_users = any(
            payout["zero_balance_users"] for payout in payouts
        )
        if not has_zero_balance_users:
            await event.reply(
                "✅ Очки начислены. Игроков с нулевым балансом нет, "
                "поэтому уведомление не отправлено."
            )
        elif not notification_topics:
            await event.reply(
                "✅ Очки начислены, но уведомления выключены во всех топиках."
            )
        elif not sent_count:
            await event.reply(
                "✅ Очки начислены, но отправить уведомление не удалось."
            )
        return

    if command in {"помощь", "help"}:
        await event.reply(HELP_TEXT)
        return

    if command == "призы":
        await event.reply(casino.prize_table())
        return

    if command == "баланс":
        target_user = sender
        target_name = name
        if casino.is_explicit_message_reply(event.message):
            replied = await event.get_reply_message()
            replied_user = await replied.get_sender()
            if not isinstance(replied_user, User) or replied_user.bot:
                await event.reply(
                    "Ответьте командой на сообщение обычного пользователя."
                )
                return
            target_user = replied_user
            target_name = display_name(replied_user)
        balance = await store.get_or_create(
            chat_id, target_user.id, target_name
        )
        gold = await store.get_museum_gold(chat_id, target_user.id)
        assets = await store.get_assets(chat_id, target_user.id)
        asset_icons = " ".join(
            ASSETS[asset_name][1]
            for asset_name, available in assets.items()
            if available
        )
        await event.reply(
            f"💰 {target_name}: {balance} очков\n"
            f"🥇 Золото: {gold}\n"
            f"Ресурсы: {asset_icons or 'нет'}"
        )
        return

    if command == "макс":
        if not args:
            max_bet = await store.get_max_bet(chat_id, sender.id)
            if max_bet is None:
                await event.reply("🎚 Максимальная ставка не ограничена.")
            else:
                await event.reply(
                    "🎚 Максимальная ставка: "
                    f"{format_points(max_bet)} очков."
                )
            return
        if len(args) != 1:
            await event.reply(
                "Формат: `каз макс 1000`; снять ограничение — "
                "`каз макс нет` или `каз макс 0`."
            )
            return

        raw_limit = args[0].casefold()
        if raw_limit in {"нет", "0"}:
            await store.set_max_bet(chat_id, sender.id, name, None)
            await event.reply("🎚 Ограничение максимальной ставки снято.")
            return
        try:
            max_bet = int(raw_limit)
        except ValueError:
            await event.reply("Максимальная ставка должна быть целым числом.")
            return
        if max_bet < MIN_BET:
            await event.reply(
                f"Максимальная ставка должна быть не меньше {MIN_BET}."
            )
            return
        if max_bet > 9_223_372_036_854_775_807:
            await event.reply("Указана слишком большая максимальная ставка.")
            return
        await store.set_max_bet(chat_id, sender.id, name, max_bet)
        await event.reply(
            "🎚 Максимальная ставка установлена: "
            f"{format_points(max_bet)} очков.\n"
            "Ва-банк выше этой суммы будет отклонён."
        )
        return

    if command == "деп":
        if len(args) != 1 or args[0].lower() not in ASSETS:
            await event.reply(
                "Формат: `каз деп малышка`, `каз деп мать`, "
                "`каз деп тачка` или `каз деп хата`."
            )
            return
        asset_name = args[0].lower()
        redeemed, balance = await store.redeem_asset(
            chat_id, sender.id, name, asset_name
        )
        _column, emoji, reward, title = ASSETS[asset_name]
        if not redeemed:
            await event.reply(f"{emoji} Ресурс «{title}» уже был обменян.")
            return
        reward_text = f"{reward:,}".replace(",", " ")
        await event.reply(
            f"{emoji} Ресурс «{title}» обменян на {reward_text} очков.\n"
            f"Баланс: {balance}."
        )
        return

    if command == "дать":
        if not event.is_reply or len(args) != 1:
            await event.reply(
                "Ответьте на сообщение получателя командой `каз дать 100`."
            )
            return
        try:
            amount = int(args[0])
        except ValueError:
            await event.reply("Сумма перевода должна быть целым числом.")
            return
        if amount <= 0:
            await event.reply("Сумма перевода должна быть больше нуля.")
            return

        replied = await event.get_reply_message()
        recipient = await replied.get_sender()
        if not isinstance(recipient, User) or recipient.bot:
            await event.reply("Переводить очки можно только пользователям.")
            return
        if recipient.id == sender.id:
            await event.reply("Нельзя переводить очки самому себе.")
            return

        recipient_name = display_name(recipient)
        status, sender_balance, recipient_balance, remaining = (
            await store.transfer(
                chat_id,
                sender.id,
                name,
                recipient.id,
                recipient_name,
                amount,
            )
        )
        if status == "locked":
            hours = max(1, math.ceil(remaining / 3600))
            await event.reply(
                "Переводы для новых игроков открываются через сутки после "
                f"первой команды. Осталось примерно {hours} ч."
            )
            return
        if status == "insufficient":
            await event.reply(
                f"Недостаточно очков. Текущий баланс: {sender_balance}."
            )
            return
        transfer_message = await event.reply(
            f"🤝 {name} передал {recipient_name} {amount} очков.\n"
            f"Баланс отправителя: {sender_balance}.\n"
            f"Баланс получателя: {recipient_balance}."
        )
        schedule_delete(chat, transfer_message)
        return

    if command == "лог":
        if not args:
            history_user_id = sender.id
        elif len(args) == 1 and args[0].lower() == "все":
            history_user_id = None
        else:
            await event.reply("Формат: `каз лог` или `каз лог все`.")
            return
        rows = await store.get_activity_history(chat_id, history_user_id)
        if not rows:
            await event.reply("История игр пока пуста.")
            return
        await event.reply(format_history(rows, history_user_id))
        return

    if command == "топ":
        if args and args[0].casefold() == "rtp":
            if len(args) > 2 or (
                len(args) == 2 and args[1].casefold() != "возр"
            ):
                await event.reply(
                    "Формат: `каз топ RTP` или `каз топ RTP возр`."
                )
                return
            descending = len(args) == 1
            rows = await store.top_casino_rtp(chat_id, descending)
            if not rows:
                await event.reply("Статистика казино в этом чате пока пуста.")
                return
            direction = "убыванию" if descending else "возрастанию"
            leaderboard_rows = [
                f"{index}. {row['display_name']} — {row['rtp']:.2f}% "
                f"({row['games']} игр, ставки: "
                f"{format_points(int(row['stakes']))})"
                for index, row in enumerate(rows, start=1)
            ]
            await event.reply(
                casino.fit_telegram_message(
                    f"🏆 RTP игроков по {direction}:",
                    leaderboard_rows,
                ),
                parse_mode=None,
            )
            return
        if args:
            await event.reply(
                "Формат: `каз топ`, `каз топ RTP` "
                "или `каз топ RTP возр`."
            )
            return
        rows = await store.top(chat_id)
        if not rows:
            await event.reply("Таблица пока пуста. Используйте `каз раздать`.")
            return
        leaderboard_rows = [
            f"{index}. {row['display_name']} — {row['balance']}"
            for index, row in enumerate(rows, start=1)
        ]
        await event.reply(
            casino.fit_telegram_message(
                "🏆 Балансы чата:", leaderboard_rows
            ),
            parse_mode=None,
        )
        return

    if command == "аналитика":
        if args:
            await event.reply(
                "Формат: `каз аналитика` или эта же команда ответом "
                "на сообщение пользователя."
            )
            return
        if casino.is_explicit_message_reply(event.message):
            replied = await event.get_reply_message()
            target_user = await replied.get_sender()
            if not isinstance(target_user, User) or target_user.bot:
                await event.reply(
                    "Ответьте командой на сообщение обычного пользователя."
                )
                return
            casino_analytics = await store.get_casino_analytics(
                chat_id, target_user.id
            )
            dice_analytics = await store.get_dice_analytics(
                chat_id, target_user.id
            )
            await event.reply(
                format_player_analytics(
                    display_name(target_user),
                    casino_analytics,
                    dice_analytics,
                )
            )
            return

        analytics = await store.get_casino_analytics(chat_id)
        if analytics is None:
            await event.reply("Статистика казино в этом чате пока пуста.")
            return
        await event.reply(format_chat_casino_analytics(analytics))
        return

    if command == "музей":
        await handle_museum_command(event, sender, name, args)
        return

    if await farm.handle_command(
        event,
        command,
        args,
        store,
        sender.id,
        name,
        INITIAL_BALANCE,
    ):
        return

    # Короткая форма «каз 100» равнозначна «каз ставка 100».
    if command.isdigit():
        args = [command]
        command = "ставка"

    all_in = command in {"ва-банк", "вабанк"}
    if all_in:
        command = "ставка"

    if command == "ставка":
        if all_in:
            if args:
                await event.reply("Формат: `каз ва-банк` или `каз вабанк`")
                return
            requested_bet = None
        elif len(args) != 1:
            await event.reply(
                f"Формат: `каз {MIN_BET}` или `каз ставка {MIN_BET}`"
            )
            return
        else:
            try:
                requested_bet = int(args[0])
            except ValueError:
                await event.reply("Ставка должна быть целым числом.")
                return
            if requested_bet < MIN_BET:
                await event.reply(f"Минимальная ставка: {MIN_BET}.")
                return

        # Ставка резервируется до анимации, чтобы параллельными командами
        # нельзя было потратить один и тот же баланс несколько раз.
        (
            bet_status,
            balance_after_bet,
            cooldown_remaining,
            bet,
        ) = await store.reserve_bet(
            chat_id,
            sender.id,
            name,
            requested_bet,
            ignore_cooldown=sender.id == admin_id,
        )
        if bet_status == "cooldown":
            await event.reply(
                "Следующую ставку можно сделать через "
                f"{math.ceil(cooldown_remaining)} сек."
            )
            return
        if bet_status == "insufficient":
            await event.reply(
                f"Недостаточно очков. Текущий баланс: {balance_after_bet}."
            )
            return
        if bet_status == "limit":
            if all_in:
                await event.reply(
                    "Сумма ва-банка выше установленного лимита: "
                    f"{format_points(bet)}."
                )
            else:
                await event.reply(
                    "Ставка превышает ваш максимум: "
                    f"{format_points(bet)} очков.\n"
                    "Изменить ограничение: `каз макс Х`; "
                    "снять: `каз макс нет`."
                )
            return

        try:
            # InputMediaDice заставляет Telegram самостоятельно сгенерировать
            # и показать нативную анимацию 🎰. Результат приходит в media.value.
            slot_message = await client.send_file(
                chat,
                types.InputMediaDice("🎰"),
                reply_to=event.message,
            )
            if not isinstance(slot_message.media, MessageMediaDice):
                raise RuntimeError("Telegram не вернул результат слота")
            slot_value = slot_message.media.value
            result = casino.decode_slot(slot_value)
            multiplier, prize_title = casino.get_prize(result)
            payout = bet * multiplier
            gold_reward = (
                museum.all_in_gold_reward(bet, result)
                if all_in
                else 0
            )
            gold_balance = (
                await store.award_museum_gold(
                    chat_id,
                    sender.id,
                    gold_reward,
                )
                if gold_reward
                else None
            )
        except Exception:
            # При ошибке отправки пользователь не должен терять ставку.
            balance = await store.add_points(chat_id, sender.id, bet)
            await event.reply(
                "Не удалось запустить слот. Ставка возвращена.\n"
                f"Баланс: {balance}"
            )
            return

        # Значение известно сразу, но ответ ждёт окончания анимации клиента.
        await asyncio.sleep(casino.SLOT_ANIMATION_SECONDS)
        gold_text = (
            f"\n🥇 Получено золота: {gold_reward}. "
            f"Всего: {gold_balance}."
            if gold_reward
            else ""
        )
        if payout:
            balance = await store.add_points(chat_id, sender.id, payout)
            await store.record_casino_game(
                chat_id, sender.id, name, bet, payout, slot_value
            )
            net = payout - bet
            await event.reply(
                f"{prize_title}! Выплата: {payout} "
                f"(чистый результат: +{net}).\n"
                f"Баланс: {balance}"
                f"{gold_text}"
            )
        else:
            await store.record_casino_game(
                chat_id, sender.id, name, bet, 0, slot_value
            )
            result_message = await event.reply(
                f"Комбинация не сыграла. Списано: {bet}.\n"
                f"Баланс: {balance_after_bet}"
                f"{gold_text}"
            )
            schedule_delete(chat, slot_message, result_message)
        if farm.earns_pet_egg(result):
            await farm.award_slot_egg(event, store, chat_id, sender.id)
        return

    # Всё ниже доступно только аккаунту из ADMIN_ID.
    if sender.id != admin_id:
        await event.reply("Эта команда доступна только администратору.")
        return

    if command in {"раздать", "сброс"}:
        if command == "сброс":
            count = await store.reset_known_balances(chat_id)
            await event.reply(
                f"✅ Балансы сброшены. Пользователей обработано: {count}. "
                f"Начальный баланс: {INITIAL_BALANCE}."
            )
        else:
            count = await store.known_user_count(chat_id)
            await event.reply(
                f"✅ Начальный баланс автоматически выдаётся при первом "
                f"обращении пользователя. Уже известных пользователей: {count}."
            )
        return

    gold_grant = (
        command == "выдать"
        and bool(args)
        and args[-1].casefold() in {"з", "золото", "золота"}
    )
    if gold_grant:
        try:
            target = await target_from_command(event, args[:-1])
        except (ValueError, TypeError):
            target = None
        if target is None:
            await event.reply(
                "Ответьте на сообщение командой `каз выдать 2 з` "
                "или укажите `каз выдать USER_ID 2 з`."
            )
            return
        user, amount = target
        if amount <= 0:
            await event.reply("Укажите положительное количество золота.")
            return
        gold_balance = await store.award_museum_gold(
            chat_id,
            user.id,
            amount,
        )
        grant_message = await event.reply(
            f"✅ {display_name(user)}: выдано {amount} 🥇. "
            f"Всего: {gold_balance} 🥇."
        )
        schedule_delete(chat, grant_message)
        return

    if command in {"выдать", "установить"}:
        try:
            target = await target_from_command(event, args)
        except (ValueError, TypeError):
            target = None
        if target is None:
            await event.reply(
                f"Ответьте на сообщение командой `каз {command} 500` "
                f"или укажите `каз {command} USER_ID 500`."
            )
            return
        user, amount = target
        if amount < 0:
            await event.reply("Укажите неотрицательное количество очков.")
            return
        try:
            balance = await store.change(
                chat_id,
                user.id,
                display_name(user),
                amount,
                set_value=command == "установить",
                actor_id=sender.id,
                actor_name=name,
            )
        except ValueError as error:
            await event.reply(str(error))
            return
        grant_message = await event.reply(
            f"✅ {display_name(user)}: новый баланс {balance}."
        )
        schedule_delete(chat, grant_message)
        return

    await event.reply("Неизвестная команда. Используйте `каз помощь`.")


async def handle_museum_command(
    event,
    sender: User,
    sender_name: str,
    args: list[str],
) -> None:
    """Показать музей либо создать статую за золото."""
    chat_id = event.chat_id
    if len(args) == 1 and args[0].casefold() in {"помощь", "help"}:
        await event.reply(museum.help_text(), parse_mode=None)
        return

    if args and args[0].casefold() == "создать":
        parsed = museum.parse_create_arguments(args[1:])
        if parsed is None:
            await event.reply(
                "Формат: `музей создать 10 золота Б`, "
                "`музей создать 10з Б` или `музей создать Б 10 з`.\n"
                "Размеры: Б — большая, Г — гигантская, В — великая."
            )
            return
        gold, size_code = parsed
        await store.get_or_create(chat_id, sender.id, sender_name)
        status, roll, gold_left = await store.create_museum_statue(
            chat_id,
            sender.id,
            gold,
            size_code,
        )
        if status == "insufficient":
            await event.reply(
                f"Недостаточно золота. Доступно: {gold_left} 🥇."
            )
            return
        if status == "broken" and roll is not None:
            await event.reply(
                "💥 Заготовка сломалась — статуя не создана.\n"
                f"Бросок: {roll.base_roll} + {roll.bonus} = {roll.score}\n"
                f"Потрачено: {gold} 🥇 · Осталось: {gold_left} 🥇"
            )
            return
        if status != "ok" or roll is None:
            await event.reply("Для создания нужно вложить хотя бы 1 золото.")
            return
        await event.reply(
            f"🏛 Статуя создана!\n"
            f"{roll.color} {roll.size.marker} {roll.size.name} · "
            f"{roll.quality}\n"
            f"Бросок: {roll.base_roll} + {roll.bonus} = {roll.score}\n"
            f"Доход: {museum.format_points(roll.income_per_day, signed=True)} "
            f"очков/сутки\n"
            f"Потрачено: {gold} 🥇 · Осталось: {gold_left} 🥇"
        )
        return

    if args:
        await event.reply(
            "Формат: `музей`, `музей помощь` или "
            "`музей создать 10 золота Б`."
        )
        return

    target = sender
    target_name = sender_name
    if casino.is_explicit_message_reply(event.message):
        replied = await event.get_reply_message()
        replied_user = await replied.get_sender()
        if not isinstance(replied_user, User) or replied_user.bot:
            await event.reply(
                "Ответьте командой на сообщение обычного пользователя."
            )
            return
        target = replied_user
        target_name = display_name(replied_user)
    await store.get_or_create(chat_id, target.id, target_name)
    snapshot = await store.get_museum(chat_id, target.id)
    await event.reply(
        museum.format_museum(target_name, snapshot),
        parse_mode=None,
    )


async def direct_museum_command(event) -> None:
    """Обработать пользовательский префикс «музей»."""
    if not event.is_group:
        return
    sender = await event.get_sender()
    chat = await event.get_chat()
    if not isinstance(sender, User) or not isinstance(chat, (Chat, Channel)):
        return
    topic_id = casino.message_topic_id(event.message)
    if not await store.is_topic_enabled(event.chat_id, topic_id):
        return
    command_line = (event.pattern_match.group(1) or "").strip()
    args = command_line.split() if command_line else []
    await handle_museum_command(
        event,
        sender,
        display_name(sender),
        args,
    )


async def direct_farm_command(event) -> None:
    """Обработать общий префикс «ферма» без слова «каз»."""
    if not event.is_group:
        return
    sender = await event.get_sender()
    chat = await event.get_chat()
    if not isinstance(sender, User) or not isinstance(chat, (Chat, Channel)):
        return
    topic_id = casino.message_topic_id(event.message)
    if not await store.is_topic_enabled(event.chat_id, topic_id):
        return
    command_line = (event.pattern_match.group(1) or "").strip()
    args = command_line.split() if command_line else []
    await farm.handle_command(
        event,
        "ферма",
        args,
        store,
        sender.id,
        display_name(sender),
        INITIAL_BALANCE,
    )


restore_dice_expirations = dice.register(
    client, store, display_name, schedule_delete
)
casino.register(client, casino_command)
farm.register(client, direct_farm_command, store, display_name)
museum.register(client, direct_museum_command)
motovskikh_link.register(
    client,
    store,
    MOTOVSKIKH_COOKIE_PATH,
    track_background_task,
    MOTOVSKIKH_LINK_TTL_SECONDS,
    MOTOVSKIKH_MAX_LINK_ATTEMPTS,
)
guess_sound.register(
    client,
    FreesoundProvider(FREESOUND_API_KEY),
    Path(
        os.getenv(
            "GUESS_SOUND_DATABASE_PATH",
            DATA_DIR / "guess_sound.sqlite3",
        )
    ),
    display_name,
    lambda: admin_id,
)


async def main() -> None:
    global bot_username
    await client.start(bot_token=BOT_TOKEN)
    me = await client.get_me()
    if not me.bot:
        raise RuntimeError(
            "SESSION_NAME указывает на пользовательскую сессию. "
            "Укажите новое имя сессии, например casino_bot."
        )
    if not me.username:
        raise RuntimeError("У Telegram-бота отсутствует username")
    bot_username = me.username
    await announce_release()
    await restore_dice_expirations()
    payout_task = asyncio.create_task(work_payout_loop())
    cleanup_tasks.add(payout_task)
    payout_task.add_done_callback(cleanup_tasks.discard)
    hatching_task = asyncio.create_task(pet_hatching_loop())
    cleanup_tasks.add(hatching_task)
    hatching_task.add_done_callback(cleanup_tasks.discard)
    museum_task = asyncio.create_task(museum_income_loop())
    cleanup_tasks.add(museum_task)
    museum_task.add_done_callback(cleanup_tasks.discard)
    print(
        f"Бот @{bot_username} запущен. "
        f"Администратор: {admin_id}."
    )
    print("Для остановки нажмите Ctrl+C.")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
