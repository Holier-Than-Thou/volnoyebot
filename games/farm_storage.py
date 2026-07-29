"""SQLite-операции фермы, совместимые с общим хранилищем бота."""

from __future__ import annotations

import sqlite3
import time

from . import pets


def initialize_farm_schema(connection: sqlite3.Connection) -> None:
    """Создать таблицы фермы без изменения существующих данных."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS player_specs (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            spec TEXT NOT NULL
                CHECK (spec IN ('stench', 'ugliness', 'stickiness')),
            PRIMARY KEY (chat_id, user_id)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS pets (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            slot_index INTEGER NOT NULL CHECK (slot_index BETWEEN 0 AND 3),
            name TEXT NOT NULL DEFAULT 'Мутант',
            stench INTEGER NOT NULL CHECK (stench BETWEEN 1 AND 100),
            ugliness INTEGER NOT NULL CHECK (ugliness BETWEEN 1 AND 100),
            stickiness INTEGER NOT NULL CHECK (stickiness BETWEEN 1 AND 100),
            generation INTEGER NOT NULL CHECK (generation IN (0, 1)),
            is_egg INTEGER NOT NULL CHECK (is_egg IN (0, 1)),
            egg_hatch_at REAL NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (chat_id, user_id, slot_index)
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS pet_income (
            chat_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            accumulated REAL NOT NULL DEFAULT 0 CHECK (accumulated >= 0),
            updated_at REAL NOT NULL,
            PRIMARY KEY (chat_id, user_id)
        )
        """
    )


class FarmStoreMixin:
    """Методы фермы для класса с ``connection`` и ``asyncio.Lock``."""

    def _get_or_create_spec_unlocked(self, chat_id: int, user_id: int) -> str:
        row = self.connection.execute(
            """
            SELECT spec FROM player_specs
            WHERE chat_id = ? AND user_id = ?
            """,
            (chat_id, user_id),
        ).fetchone()
        if row is not None:
            return str(row["spec"])
        spec = pets.roll_spec()
        self.connection.execute(
            """
            INSERT INTO player_specs(chat_id, user_id, spec)
            VALUES (?, ?, ?)
            """,
            (chat_id, user_id, spec),
        )
        return spec

    def _accrue_income_unlocked(
        self, chat_id: int, user_id: int, now: float
    ) -> float:
        row = self.connection.execute(
            """
            SELECT accumulated, updated_at FROM pet_income
            WHERE chat_id = ? AND user_id = ?
            """,
            (chat_id, user_id),
        ).fetchone()
        if row is None:
            self.connection.execute(
                """
                INSERT INTO pet_income(chat_id, user_id, accumulated, updated_at)
                VALUES (?, ?, 0, ?)
                """,
                (chat_id, user_id, now),
            )
            self.connection.execute(
                """
                UPDATE pets SET is_egg = 0, egg_hatch_at = 0
                WHERE chat_id = ? AND user_id = ?
                    AND is_egg = 1 AND egg_hatch_at <= ?
                """,
                (chat_id, user_id, now),
            )
            return 0.0
        updated_at = float(row["updated_at"])
        accumulated = float(row["accumulated"])
        pet_rows = self.connection.execute(
            """
            SELECT * FROM pets
            WHERE chat_id = ? AND user_id = ?
            """,
            (chat_id, user_id),
        ).fetchall()
        for pet_row in pet_rows:
            pet = pets.pet_from_mapping(pet_row)
            income_started_at = updated_at
            if pet.is_egg:
                if pet.egg_hatch_at > now:
                    continue
                income_started_at = max(updated_at, pet.egg_hatch_at)
            elapsed = max(0.0, now - income_started_at)
            accumulated += pet.adult_income_per_second() * elapsed
        self.connection.execute(
            """
            UPDATE pets SET is_egg = 0, egg_hatch_at = 0
            WHERE chat_id = ? AND user_id = ?
                AND is_egg = 1 AND egg_hatch_at <= ?
            """,
            (chat_id, user_id, now),
        )
        self.connection.execute(
            """
            UPDATE pet_income SET accumulated = ?, updated_at = ?
            WHERE chat_id = ? AND user_id = ?
            """,
            (accumulated, now, chat_id, user_id),
        )
        return accumulated

    async def get_farm(self, chat_id: int, user_id: int) -> dict:
        """Вернуть актуальное состояние фермы."""
        async with self.lock:
            now = time.time()
            spec = self._get_or_create_spec_unlocked(chat_id, user_id)
            accumulated = self._accrue_income_unlocked(chat_id, user_id, now)
            rows = self.connection.execute(
                """
                SELECT * FROM pets
                WHERE chat_id = ? AND user_id = ?
                ORDER BY slot_index
                """,
                (chat_id, user_id),
            ).fetchall()
            self.connection.commit()
            return {
                "spec": spec,
                "accumulated": accumulated,
                "pets": [pets.pet_from_mapping(row) for row in rows],
                "now": now,
            }

    async def hatch_due_pet_eggs(self) -> int:
        """Автоматически вылупить все созревшие яйца."""
        async with self.lock:
            now = time.time()
            owners = self.connection.execute(
                """
                SELECT DISTINCT chat_id, user_id
                FROM pets
                WHERE is_egg = 1 AND egg_hatch_at <= ?
                """,
                (now,),
            ).fetchall()
            egg_count = int(
                self.connection.execute(
                    """
                    SELECT COUNT(*) FROM pets
                    WHERE is_egg = 1 AND egg_hatch_at <= ?
                    """,
                    (now,),
                ).fetchone()[0]
            )
            for owner in owners:
                self._accrue_income_unlocked(
                    int(owner["chat_id"]),
                    int(owner["user_id"]),
                    now,
                )
            self.connection.commit()
            return egg_count

    async def award_pet_egg(
        self, chat_id: int, user_id: int
    ) -> tuple[str, str, pets.Pet | None]:
        """Добавить яйцо в первый свободный слот после двух вишен."""
        async with self.lock:
            occupied = {
                int(row["slot_index"])
                for row in self.connection.execute(
                    """
                    SELECT slot_index FROM pets
                    WHERE chat_id = ? AND user_id = ?
                    """,
                    (chat_id, user_id),
                ).fetchall()
            }
            free_slots = [
                slot for slot in range(pets.MAX_SLOTS) if slot not in occupied
            ]
            spec = self._get_or_create_spec_unlocked(chat_id, user_id)
            if not free_slots:
                self.connection.commit()
                return "full", spec, None
            now = time.time()
            self._accrue_income_unlocked(chat_id, user_id, now)
            egg = pets.create_pure_egg(spec, free_slots[0], now)
            self._insert_pet_unlocked(chat_id, user_id, egg)
            self.connection.commit()
            return "ok", spec, egg

    def _insert_pet_unlocked(
        self, chat_id: int, user_id: int, pet: pets.Pet
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO pets(
                chat_id, user_id, slot_index, name,
                stench, ugliness, stickiness, generation,
                is_egg, egg_hatch_at, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chat_id,
                user_id,
                pet.slot_index,
                pet.name,
                pet.stench,
                pet.ugliness,
                pet.stickiness,
                pet.generation,
                int(pet.is_egg),
                pet.egg_hatch_at,
                pet.created_at,
            ),
        )

    async def rename_pet(
        self,
        chat_id: int,
        user_id: int,
        slot_index: int,
        new_name: str,
    ) -> bool:
        """Переименовать взрослого питомца."""
        async with self.lock:
            self._accrue_income_unlocked(chat_id, user_id, time.time())
            cursor = self.connection.execute(
                """
                UPDATE pets SET name = ?
                WHERE chat_id = ? AND user_id = ? AND slot_index = ?
                    AND is_egg = 0
                """,
                (new_name, chat_id, user_id, slot_index),
            )
            self.connection.commit()
            return cursor.rowcount == 1

    async def breed_pets(
        self, chat_id: int, user_id: int, first_slot: int, second_slot: int
    ) -> tuple[str, pets.Pet | None]:
        """Атомарно заменить двух родителей яйцом ребёнка."""
        async with self.lock:
            now = time.time()
            self._accrue_income_unlocked(chat_id, user_id, now)
            rows = self.connection.execute(
                """
                SELECT * FROM pets
                WHERE chat_id = ? AND user_id = ?
                    AND slot_index IN (?, ?)
                """,
                (chat_id, user_id, first_slot, second_slot),
            ).fetchall()
            by_slot = {
                int(row["slot_index"]): pets.pet_from_mapping(row) for row in rows
            }
            if first_slot not in by_slot or second_slot not in by_slot:
                return "missing", None
            try:
                child = pets.breed(
                    by_slot[first_slot],
                    by_slot[second_slot],
                    min(first_slot, second_slot),
                    now=now,
                )
            except ValueError as error:
                return str(error), None
            self.connection.execute(
                """
                DELETE FROM pets
                WHERE chat_id = ? AND user_id = ?
                    AND slot_index IN (?, ?)
                """,
                (chat_id, user_id, first_slot, second_slot),
            )
            self._insert_pet_unlocked(chat_id, user_id, child)
            self.connection.commit()
            return "ok", child

    async def claim_pet_income(
        self,
        chat_id: int,
        user_id: int,
        display_name: str,
        initial_balance: int,
    ) -> tuple[int, int]:
        """Начислить целую часть дохода, сохранив дробный остаток."""
        async with self.lock:
            now = time.time()
            accumulated = self._accrue_income_unlocked(chat_id, user_id, now)
            amount = int(accumulated)
            self.connection.execute(
                """
                INSERT INTO balances(chat_id, user_id, display_name, balance)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id)
                DO UPDATE SET display_name = excluded.display_name
                """,
                (chat_id, user_id, display_name, initial_balance),
            )
            if amount:
                self.connection.execute(
                    """
                    UPDATE balances SET balance = balance + ?
                    WHERE chat_id = ? AND user_id = ?
                    """,
                    (amount, chat_id, user_id),
                )
            self.connection.execute(
                """
                UPDATE pet_income SET accumulated = ?
                WHERE chat_id = ? AND user_id = ?
                """,
                (accumulated - amount, chat_id, user_id),
            )
            balance = int(
                self.connection.execute(
                    """
                    SELECT balance FROM balances
                    WHERE chat_id = ? AND user_id = ?
                    """,
                    (chat_id, user_id),
                ).fetchone()["balance"]
            )
            self.connection.commit()
            return amount, balance
