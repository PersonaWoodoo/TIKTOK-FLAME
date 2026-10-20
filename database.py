"""Асинхронная работа с SQLite через aiosqlite."""

import os
import logging
import aiosqlite
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "data/bot.db")


async def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                referrer_id INTEGER,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                password TEXT NOT NULL,
                is_running INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id INTEGER PRIMARY KEY,
                expires_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                method TEXT NOT NULL,
                amount INTEGER NOT NULL,
                status TEXT NOT NULL,
                screenshot TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referrals (
                invited_id INTEGER PRIMARY KEY,
                referrer_id INTEGER NOT NULL,
                confirmed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                confirmed_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS referrals_stat (
                user_id INTEGER PRIMARY KEY,
                reward_7d_granted INTEGER NOT NULL DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.commit()
    logger.info("БД инициализирована: %s", DB_PATH)


def _now() -> str:
    return datetime.utcnow().isoformat()


# ───────────── Пользователи ─────────────

async def upsert_user(user_id: int, username: str | None, first_name: str | None,
                      referrer_id: int | None = None) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, first_name, referrer_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name
        """, (user_id, username, first_name, referrer_id, _now()))
        await db.commit()


async def get_user(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_all_users() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users ORDER BY created_at")
        return [dict(r) for r in await cur.fetchall()]


async def count_users() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM users")
        (n,) = await cur.fetchone()
        return int(n)


# ───────────── Рефералы ─────────────

async def add_referral(invited_id: int, referrer_id: int) -> bool:
    """Регистрирует приглашение. Возвращает True, если запись создана впервые."""
    if invited_id == referrer_id:
        return False
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT 1 FROM referrals WHERE invited_id=?", (invited_id,)
        )
        if await cur.fetchone():
            return False
        await db.execute("""
            INSERT INTO referrals (invited_id, referrer_id, confirmed, created_at)
            VALUES (?, ?, 0, ?)
        """, (invited_id, referrer_id, _now()))
        await db.commit()
        return True


async def confirm_referral(invited_id: int) -> bool:
    """Помечает реферала подтверждённым (подписался на канал). True — если только что подтверждён."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT confirmed FROM referrals WHERE invited_id=?", (invited_id,)
        )
        row = await cur.fetchone()
        if not row:
            return False
        if row[0] == 1:
            return False
        await db.execute("""
            UPDATE referrals SET confirmed=1, confirmed_at=? WHERE invited_id=?
        """, (_now(), invited_id))
        await db.commit()
        return True


async def count_confirmed_referrals(referrer_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id=? AND confirmed=1",
            (referrer_id,),
        )
        (n,) = await cur.fetchone()
        return int(n)


async def is_reward_granted(user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT reward_7d_granted FROM referrals_stat WHERE user_id=?", (user_id,)
        )
        row = await cur.fetchone()
        return bool(row and row[0] == 1)


async def mark_reward_granted(user_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO referrals_stat (user_id, reward_7d_granted)
            VALUES (?, 1)
            ON CONFLICT(user_id) DO UPDATE SET reward_7d_granted=1
        """, (user_id,))
        await db.commit()


# ───────────── Аккаунты ─────────────

async def add_account(user_id: int, username: str, password: str) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO accounts (user_id, username, password, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, username, password, _now()))
        await db.commit()
        return cur.lastrowid


async def get_user_accounts(user_id: int) -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM accounts WHERE user_id=? ORDER BY id", (user_id,))
        return [dict(r) for r in await cur.fetchall()]


async def get_account(account_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM accounts WHERE id=?", (account_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def delete_account(account_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM accounts WHERE id=? AND user_id=?", (account_id, user_id))
        await db.commit()
        return cur.rowcount > 0


async def set_account_running(account_id: int, running: bool) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE accounts SET is_running=? WHERE id=?",
                         (1 if running else 0, account_id))
        await db.commit()


async def count_accounts() -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM accounts")
        (n,) = await cur.fetchone()
        return int(n)


async def stop_all_accounts() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE accounts SET is_running=0")
        await db.commit()


# ───────────── Подписки ─────────────

async def get_subscription(user_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM subscriptions WHERE user_id=?", (user_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def is_subscription_active(user_id: int) -> bool:
    sub = await get_subscription(user_id)
    if not sub:
        return False
    try:
        expires = datetime.fromisoformat(sub["expires_at"])
    except ValueError:
        return False
    return expires > datetime.utcnow()


async def extend_subscription(user_id: int, days: int) -> str:
    now = datetime.utcnow()
    sub = await get_subscription(user_id)
    base = now
    if sub:
        try:
            current = datetime.fromisoformat(sub["expires_at"])
            if current > now:
                base = current
        except ValueError:
            pass
    new_expires = base + timedelta(days=days)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO subscriptions (user_id, expires_at, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                expires_at=excluded.expires_at,
                updated_at=excluded.updated_at
        """, (user_id, new_expires.isoformat(), _now()))
        await db.commit()
    return new_expires.isoformat()


async def create_payment(user_id: int, method: str, amount: int, status: str = "pending",
                         screenshot: str | None = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO payments (user_id, method, amount, status, screenshot, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (user_id, method, amount, status, screenshot, _now(), _now()))
        await db.commit()
        return cur.lastrowid


async def get_payment(payment_id: int) -> dict | None:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM payments WHERE id=?", (payment_id,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def set_payment_status(payment_id: int, status: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE payments SET status=?, updated_at=? WHERE id=?",
                         (status, _now(), payment_id))
        await db.commit()


async def count_payments(status: str | None = None) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        if status:
            cur = await db.execute("SELECT COUNT(*) FROM payments WHERE status=?", (status,))
        else:
            cur = await db.execute("SELECT COUNT(*) FROM payments")
        (n,) = await cur.fetchone()
        return int(n)


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
        """, (key, value))
        await db.commit()


async def get_setting(key: str, default: str | None = None) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT value FROM settings WHERE key=?", (key,))
        row = await cur.fetchone()
        return row[0] if row else default
