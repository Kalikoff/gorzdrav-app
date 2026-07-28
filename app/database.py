import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime

import aiosqlite
import pytz

from app.config import settings

logger = logging.getLogger(__name__)
MSK = pytz.timezone("Europe/Moscow")

SUBSCRIPTION_FIELDS = (
    "id, user_id, district_id, district_name, lpu_id, lpu_name, "
    "speciality_id, speciality_name, doctor_id, doctor_name, "
    "time_from, time_to, is_active, last_check_data, last_notified_at, created_at"
)

# Колонки, которых не было в первой версии схемы. Добавляются на старте,
# чтобы уже накопленные подписки не пришлось пересоздавать.
_ADDED_COLUMNS = {
    "district_id": "TEXT",
    "district_name": "TEXT",
    "lpu_name": "TEXT",
    "time_from": "TEXT NOT NULL DEFAULT '00:00'",
    "time_to": "TEXT NOT NULL DEFAULT '23:59'",
    "is_active": "INTEGER NOT NULL DEFAULT 1",
    "last_notified_at": "TEXT",
}


def _now() -> str:
    return datetime.now(MSK).isoformat()


@asynccontextmanager
async def _db():
    async with aiosqlite.connect(settings.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        yield db


async def init_db() -> None:
    directory = os.path.dirname(settings.DATABASE_PATH)
    if directory:
        os.makedirs(directory, exist_ok=True)

    async with _db() as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                first_name TEXT,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id          INTEGER NOT NULL,
                district_id      TEXT,
                district_name    TEXT,
                lpu_id           TEXT NOT NULL,
                lpu_name         TEXT,
                speciality_id    TEXT NOT NULL,
                speciality_name  TEXT NOT NULL,
                doctor_id        TEXT,
                doctor_name      TEXT,
                time_from        TEXT NOT NULL DEFAULT '00:00',
                time_to          TEXT NOT NULL DEFAULT '23:59',
                is_active        INTEGER NOT NULL DEFAULT 1,
                last_check_data  TEXT NOT NULL DEFAULT '{}',
                last_notified_at TEXT,
                created_at       TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                user_id  INTEGER NOT NULL,
                lpu_id   TEXT NOT NULL,
                lpu_name TEXT NOT NULL,
                PRIMARY KEY (user_id, lpu_id)
            )
        """)
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_subs_user ON subscriptions(user_id)"
        )
        await _migrate(db)
        await db.commit()


async def _migrate(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA table_info(subscriptions)") as cur:
        existing = {row["name"] for row in await cur.fetchall()}

    for column, ddl in _ADDED_COLUMNS.items():
        if column not in existing:
            logger.info("Миграция: добавляю subscriptions.%s", column)
            await db.execute(f"ALTER TABLE subscriptions ADD COLUMN {column} {ddl}")

    # В старой схеме название поликлиники не сохранялось — достаём из избранного.
    await db.execute("""
        UPDATE subscriptions
           SET lpu_name = (
               SELECT f.lpu_name FROM favorites f
                WHERE f.user_id = subscriptions.user_id AND f.lpu_id = subscriptions.lpu_id
           )
         WHERE (lpu_name IS NULL OR lpu_name = '')
           AND EXISTS (
               SELECT 1 FROM favorites f
                WHERE f.user_id = subscriptions.user_id AND f.lpu_id = subscriptions.lpu_id
           )
    """)


# ── Пользователи ────────────────────────────────────────────────────────────

async def register_user(user_id: int, username: str | None, first_name: str | None) -> None:
    async with _db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, created_at) VALUES (?,?,?,?)",
            (user_id, username, first_name, _now()),
        )
        await db.commit()


# ── Подписки ────────────────────────────────────────────────────────────────

async def add_subscription(user_id: int, data: dict) -> tuple[int, bool]:
    """Создаёт подписку. Если такая уже есть — обновляет её окно времени.

    Возвращает (id, создана ли новая).
    """
    async with _db() as db:
        async with db.execute(
            """
            SELECT id FROM subscriptions
             WHERE user_id = ? AND lpu_id = ? AND speciality_id = ?
               AND IFNULL(doctor_id, '') = ?
            """,
            (user_id, data["lpu_id"], data["speciality_id"], data.get("doctor_id") or ""),
        ) as cur:
            existing = await cur.fetchone()

        if existing:
            await db.execute(
                """
                UPDATE subscriptions
                   SET time_from = ?, time_to = ?, is_active = 1, last_check_data = '{}'
                 WHERE id = ?
                """,
                (data["time_from"], data["time_to"], existing["id"]),
            )
            await db.commit()
            return existing["id"], False

        cur = await db.execute(
            """
            INSERT INTO subscriptions (
                user_id, district_id, district_name, lpu_id, lpu_name,
                speciality_id, speciality_name, doctor_id, doctor_name,
                time_from, time_to, is_active, last_check_data, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,1,'{}',?)
            """,
            (
                user_id,
                data.get("district_id"),
                data.get("district_name"),
                data["lpu_id"],
                data.get("lpu_name"),
                data["speciality_id"],
                data["speciality_name"],
                data.get("doctor_id"),
                data.get("doctor_name"),
                data["time_from"],
                data["time_to"],
                _now(),
            ),
        )
        await db.commit()
        return cur.lastrowid, True


async def get_user_subscriptions(user_id: int) -> list[dict]:
    async with _db() as db:
        async with db.execute(
            f"SELECT {SUBSCRIPTION_FIELDS} FROM subscriptions WHERE user_id = ? ORDER BY id DESC",
            (user_id,),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_subscription(subscription_id: int, user_id: int) -> dict | None:
    async with _db() as db:
        async with db.execute(
            f"SELECT {SUBSCRIPTION_FIELDS} FROM subscriptions WHERE id = ? AND user_id = ?",
            (subscription_id, user_id),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def update_subscription(subscription_id: int, user_id: int, changes: dict) -> bool:
    if not changes:
        return False

    fields = ", ".join(f"{key} = ?" for key in changes)
    params = [*changes.values(), subscription_id, user_id]

    # Меняем окно времени — сбрасываем историю, чтобы номерки в новом
    # диапазоне пришли сразу, а не после следующего изменения расписания.
    if "time_from" in changes or "time_to" in changes:
        fields += ", last_check_data = '{}'"

    async with _db() as db:
        cur = await db.execute(
            f"UPDATE subscriptions SET {fields} WHERE id = ? AND user_id = ?", params
        )
        await db.commit()
        return cur.rowcount > 0


async def delete_subscription(subscription_id: int, user_id: int) -> bool:
    async with _db() as db:
        cur = await db.execute(
            "DELETE FROM subscriptions WHERE id = ? AND user_id = ?", (subscription_id, user_id)
        )
        await db.commit()
        return cur.rowcount > 0


async def delete_all_subscriptions(user_id: int) -> int:
    async with _db() as db:
        cur = await db.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        await db.commit()
        return cur.rowcount


async def get_active_subscriptions() -> list[dict]:
    async with _db() as db:
        async with db.execute(
            f"SELECT {SUBSCRIPTION_FIELDS} FROM subscriptions WHERE is_active = 1 ORDER BY lpu_id, speciality_id"
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def save_check_result(subscription_id: int, snapshot: str, notified: bool) -> None:
    async with _db() as db:
        if notified:
            await db.execute(
                "UPDATE subscriptions SET last_check_data = ?, last_notified_at = ? WHERE id = ?",
                (snapshot, _now(), subscription_id),
            )
        else:
            await db.execute(
                "UPDATE subscriptions SET last_check_data = ? WHERE id = ?",
                (snapshot, subscription_id),
            )
        await db.commit()


async def deactivate_user_subscriptions(user_id: int) -> None:
    async with _db() as db:
        await db.execute("UPDATE subscriptions SET is_active = 0 WHERE user_id = ?", (user_id,))
        await db.commit()


# ── Избранные поликлиники ───────────────────────────────────────────────────

async def add_favorite(user_id: int, lpu_id: str, lpu_name: str) -> None:
    async with _db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO favorites (user_id, lpu_id, lpu_name) VALUES (?,?,?)",
            (user_id, lpu_id, lpu_name),
        )
        await db.commit()


async def get_favorites(user_id: int) -> list[dict]:
    async with _db() as db:
        async with db.execute(
            "SELECT lpu_id, lpu_name FROM favorites WHERE user_id = ? ORDER BY lpu_name",
            (user_id,),
        ) as cur:
            return [{"id": row["lpu_id"], "name": row["lpu_name"]} for row in await cur.fetchall()]


async def delete_favorite(user_id: int, lpu_id: str) -> bool:
    async with _db() as db:
        cur = await db.execute(
            "DELETE FROM favorites WHERE user_id = ? AND lpu_id = ?", (user_id, lpu_id)
        )
        await db.commit()
        return cur.rowcount > 0
