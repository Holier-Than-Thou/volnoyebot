"""SQLite storage for emulated test players."""

from __future__ import annotations

import sqlite3
import time


# Идентификаторы эмулируемых игроков лежат ниже этой границы. Telegram
# выдаёт только положительные user_id, поэтому пересечения невозможны.
PLAYER_ID_BASE = -1_000_000_000

# Привязка сообщения к персоне нужна только для ответов на него.
MESSAGE_BINDING_TTL_SECONDS = 7 * 24 * 60 * 60


def initialize_emulation_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS emulated_players (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            name_key TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (chat_id, user_id)
        )
        """
    )
    connection.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_emulated_players_name
        ON emulated_players(chat_id, name_key)
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS emulated_messages (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (chat_id, message_id)
        )
        """
    )


def name_key(name: str) -> str:
    """Ключ сравнения имён: регистр не важен и для кириллицы тоже."""
    return name.strip().casefold()


class EmulationStoreMixin:
    async def create_emulated_player(
        self, chat_id: int, name: str
    ) -> tuple[str, sqlite3.Row]:
        """Создать персону чата. Возвращает статус и её запись."""
        key = name_key(name)
        async with self.lock:
            existing = self.connection.execute(
                """
                SELECT chat_id, user_id, name FROM emulated_players
                WHERE chat_id = ? AND name_key = ?
                """,
                (chat_id, key),
            ).fetchone()
            if existing is not None:
                return "exists", existing

            # Идентификаторы уникальны во всех чатах, чтобы удалённая
            # персона не могла получить чужие остатки данных.
            lowest = self.connection.execute(
                "SELECT MIN(user_id) AS lowest FROM emulated_players"
            ).fetchone()["lowest"]
            user_id = (lowest if lowest is not None else PLAYER_ID_BASE) - 1
            self.connection.execute(
                """
                INSERT INTO emulated_players(
                    chat_id, user_id, name, name_key, created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (chat_id, user_id, name.strip(), key, time.time()),
            )
            self.connection.commit()
            created = self.connection.execute(
                """
                SELECT chat_id, user_id, name FROM emulated_players
                WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, user_id),
            ).fetchone()
            return "created", created

    async def delete_emulated_player(
        self, chat_id: int, name: str
    ) -> sqlite3.Row | None:
        """Удалить персону вместе с балансом и разовыми ресурсами."""
        key = name_key(name)
        async with self.lock:
            row = self.connection.execute(
                """
                SELECT chat_id, user_id, name FROM emulated_players
                WHERE chat_id = ? AND name_key = ?
                """,
                (chat_id, key),
            ).fetchone()
            if row is None:
                return None
            user_id = row["user_id"]
            self.connection.execute(
                "DELETE FROM emulated_players WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            self.connection.execute(
                "DELETE FROM emulated_messages WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            self.connection.execute(
                "DELETE FROM balances WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            self.connection.execute(
                "DELETE FROM player_assets WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id),
            )
            self.connection.commit()
            return row

    async def list_emulated_players(self, chat_id: int) -> list[sqlite3.Row]:
        """Персоны чата с текущим балансом в порядке создания."""
        async with self.lock:
            return self.connection.execute(
                """
                SELECT emulated_players.user_id AS user_id,
                       emulated_players.name AS name,
                       COALESCE(balances.balance, 0) AS balance,
                       balances.user_id IS NOT NULL AS has_balance
                FROM emulated_players
                LEFT JOIN balances
                    ON balances.chat_id = emulated_players.chat_id
                    AND balances.user_id = emulated_players.user_id
                WHERE emulated_players.chat_id = ?
                ORDER BY emulated_players.created_at
                """,
                (chat_id,),
            ).fetchall()

    async def get_emulated_player_by_name(
        self, chat_id: int, name: str
    ) -> sqlite3.Row | None:
        async with self.lock:
            return self.connection.execute(
                """
                SELECT chat_id, user_id, name FROM emulated_players
                WHERE chat_id = ? AND name_key = ?
                """,
                (chat_id, name_key(name)),
            ).fetchone()

    async def get_emulated_player(
        self, chat_id: int, user_id: int
    ) -> sqlite3.Row | None:
        async with self.lock:
            return self.connection.execute(
                """
                SELECT chat_id, user_id, name FROM emulated_players
                WHERE chat_id = ? AND user_id = ?
                """,
                (chat_id, user_id),
            ).fetchone()

    async def find_emulated_players_by_name(
        self, name: str
    ) -> list[sqlite3.Row]:
        """Найти одноимённых персон во всех чатах — для личного чата с ботом."""
        async with self.lock:
            return self.connection.execute(
                """
                SELECT chat_id, user_id, name FROM emulated_players
                WHERE name_key = ?
                ORDER BY created_at
                """,
                (name_key(name),),
            ).fetchall()

    async def find_emulated_player_by_id(
        self, user_id: int
    ) -> sqlite3.Row | None:
        """Найти персону по идентификатору без привязки к чату."""
        async with self.lock:
            return self.connection.execute(
                """
                SELECT chat_id, user_id, name FROM emulated_players
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()

    async def bind_emulated_message(
        self, chat_id: int, message_id: int, user_id: int
    ) -> None:
        """Запомнить, что сообщение было отправлено от лица персоны."""
        now = time.time()
        async with self.lock:
            self.connection.execute(
                """
                INSERT INTO emulated_messages(
                    chat_id, message_id, user_id, created_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id)
                DO UPDATE SET user_id = excluded.user_id,
                              created_at = excluded.created_at
                """,
                (chat_id, message_id, user_id, now),
            )
            self.connection.execute(
                "DELETE FROM emulated_messages WHERE created_at < ?",
                (now - MESSAGE_BINDING_TTL_SECONDS,),
            )
            self.connection.commit()

    async def get_emulated_player_by_message(
        self, chat_id: int, message_id: int
    ) -> sqlite3.Row | None:
        """Найти персону, от лица которой отправлено сообщение."""
        async with self.lock:
            return self.connection.execute(
                """
                SELECT emulated_players.chat_id AS chat_id,
                       emulated_players.user_id AS user_id,
                       emulated_players.name AS name
                FROM emulated_messages
                JOIN emulated_players
                    ON emulated_players.chat_id = emulated_messages.chat_id
                    AND emulated_players.user_id = emulated_messages.user_id
                WHERE emulated_messages.chat_id = ?
                    AND emulated_messages.message_id = ?
                """,
                (chat_id, message_id),
            ).fetchone()
