"""Публикация краткой сводки изменений после обновления бота."""

from __future__ import annotations

import sqlite3
import time


# При каждом пользовательском обновлении меняются и идентификатор, и текст.
# Запись в SQLite не позволяет повторять одну сводку при обычных перезапусках.
RELEASE_ID = "2026-07-31-new-player-transfer-lock"
RELEASE_SUMMARY = """🆕 Обновление бота

• Новые игроки и пользователи без истории игр могут переводить очки другим
  игрокам только через сутки после своей первой команды."""


def initialize_release_schema(connection: sqlite3.Connection) -> None:
    """Создать журнал уже опубликованных версий."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS release_announcements (
            chat_id INTEGER NOT NULL,
            topic_id INTEGER NOT NULL,
            release_id TEXT NOT NULL,
            message_id INTEGER NOT NULL,
            pinned INTEGER NOT NULL CHECK (pinned IN (0, 1)),
            announced_at REAL NOT NULL,
            PRIMARY KEY (chat_id, topic_id, release_id)
        )
        """
    )


class ReleaseStoreMixin:
    """SQLite-методы для рассылки сводки только один раз."""

    async def pending_release_targets(
        self, release_id: str
    ) -> list[tuple[int, int]]:
        """Вернуть активные разделы, ещё не получавшие эту версию."""
        async with self.lock:
            rows = self.connection.execute(
                """
                SELECT topics.chat_id, topics.topic_id
                FROM casino_topics AS topics
                JOIN chat_settings AS chats
                    ON chats.chat_id = topics.chat_id
                LEFT JOIN release_announcements AS announcements
                    ON announcements.chat_id = topics.chat_id
                    AND announcements.topic_id = topics.topic_id
                    AND announcements.release_id = ?
                WHERE chats.activated = 1
                    AND topics.enabled = 1
                    AND announcements.chat_id IS NULL
                ORDER BY topics.chat_id, topics.topic_id
                """,
                (release_id,),
            ).fetchall()
            return [
                (int(row["chat_id"]), int(row["topic_id"])) for row in rows
            ]

    async def mark_release_announced(
        self,
        chat_id: int,
        topic_id: int,
        release_id: str,
        message_id: int,
        pinned: bool,
    ) -> None:
        """Зафиксировать успешную отправку сводки."""
        async with self.lock:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO release_announcements(
                    chat_id, topic_id, release_id,
                    message_id, pinned, announced_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_id,
                    topic_id,
                    release_id,
                    message_id,
                    int(pinned),
                    time.time(),
                ),
            )
            self.connection.commit()
