"""Проверки хранения опубликованных сводок обновлений."""

from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path

from games.release import ReleaseStoreMixin, initialize_release_schema


class TestReleaseStore(ReleaseStoreMixin):
    def __init__(self, path: Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.lock = asyncio.Lock()
        self.connection.execute(
            """
            CREATE TABLE chat_settings (
                chat_id INTEGER PRIMARY KEY,
                activated INTEGER NOT NULL
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE casino_topics (
                chat_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                enabled INTEGER NOT NULL,
                PRIMARY KEY (chat_id, topic_id)
            )
            """
        )
        initialize_release_schema(self.connection)
        self.connection.commit()


class ReleaseStoreTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = TestReleaseStore(
            Path(self.directory.name) / "release.sqlite3"
        )

    async def asyncTearDown(self) -> None:
        self.store.connection.close()
        self.directory.cleanup()

    async def test_only_enabled_unannounced_topics_are_returned(self) -> None:
        self.store.connection.executemany(
            "INSERT INTO chat_settings(chat_id, activated) VALUES (?, ?)",
            ((1, 1), (2, 0)),
        )
        self.store.connection.executemany(
            """
            INSERT INTO casino_topics(chat_id, topic_id, enabled)
            VALUES (?, ?, ?)
            """,
            ((1, 0, 1), (1, 10, 0), (2, 0, 1)),
        )
        self.store.connection.commit()

        self.assertEqual(
            await self.store.pending_release_targets("version-1"),
            [(1, 0)],
        )
        await self.store.mark_release_announced(
            1, 0, "version-1", 123, pinned=False
        )
        self.assertEqual(
            await self.store.pending_release_targets("version-1"),
            [],
        )
        self.assertEqual(
            await self.store.pending_release_targets("version-2"),
            [(1, 0)],
        )


if __name__ == "__main__":
    unittest.main()
