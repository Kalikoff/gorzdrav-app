import aiosqlite
import json
from pathlib import Path

DB_PATH = Path("/app/data/subscriptions.db")

async def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                lpu_id TEXT NOT NULL,
                speciality_id TEXT NOT NULL,
                doctor_id TEXT,
                speciality_name TEXT NOT NULL,
                doctor_name TEXT,
                last_check_data TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                user_id INTEGER,
                lpu_id TEXT NOT NULL,
                lpu_name TEXT NOT NULL,
                PRIMARY KEY (user_id, lpu_id)
            )
        """)
        await db.commit()

async def add_subscription(user_id, lpu_id, speciality_id, spec_name, doctor_id=None, doctor_name=None):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO subscriptions 
            (user_id, lpu_id, speciality_id, doctor_id, speciality_name, doctor_name, last_check_data)
            VALUES (?, ?, ?, ?, ?, ?, '{}')
        """, (user_id, lpu_id, speciality_id, doctor_id, spec_name, doctor_name))
        await db.commit()

async def get_user_subscriptions(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, lpu_id, speciality_id, doctor_id, speciality_name, doctor_name FROM subscriptions WHERE user_id = ?", 
            (user_id,)
        ) as cursor:
            return await cursor.fetchall()

async def delete_subscription_by_id(sub_id, user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM subscriptions WHERE id = ? AND user_id = ?", (sub_id, user_id))
        await db.commit()

async def remove_all_subscriptions(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        await db.commit()

async def add_favorite(user_id, lpu_id, lpu_name):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR IGNORE INTO favorites (user_id, lpu_id, lpu_name) VALUES (?, ?, ?)", (user_id, lpu_id, lpu_name))
        await db.commit()

async def get_favorites(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT lpu_id, lpu_name FROM favorites WHERE user_id = ?", (user_id,)) as cursor:
            return await cursor.fetchall()

async def get_all_subscriptions_for_scheduler():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT id, user_id, lpu_id, speciality_id, doctor_id, speciality_name, doctor_name, last_check_data FROM subscriptions"
        ) as cursor:
            return await cursor.fetchall()

async def update_subscription_history(sub_id, history_json):
    """Обновляет JSON с историей слотов для конкретной подписки"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE subscriptions SET last_check_data = ? WHERE id = ?", (history_json, sub_id))
        await db.commit()