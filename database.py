import asyncio
import threading
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

import config

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    custom_password TEXT,
    proxy TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    site_id TEXT NOT NULL,
    email TEXT NOT NULL,
    password TEXT NOT NULL,
    invite_code TEXT NOT NULL,
    is_linked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_accounts_user_site_created
ON accounts (user_id, site_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_accounts_user_email_created
ON accounts (user_id, email, created_at DESC);
"""

_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is not None:
        return _pool

    with _pool_lock:
        if _pool is None:
            if not config.DATABASE_URL:
                raise RuntimeError("DATABASE_URL is not set. Add your Supabase Postgres URL to .env.")
            _pool = ThreadedConnectionPool(
                minconn=1,
                maxconn=10,
                dsn=config.DATABASE_URL,
                sslmode="require",
                connect_timeout=10,
                application_name="deep-earn-bot",
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5,
            )
    return _pool


@contextmanager
def _pooled_connection():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
    finally:
        pool.putconn(conn)


def _run_fetchone(query, params=()):
    with _pooled_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, params)
            return cursor.fetchone()


def _run_fetchall(query, params=()):
    with _pooled_connection() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query, params)
            return cursor.fetchall()


def _run_execute(query, params=()):
    with _pooled_connection() as conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute(query, params)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _warmup_pool():
    with _pooled_connection():
        return None


def _run_ping():
    with _pooled_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()


async def init_db():
    await asyncio.to_thread(_warmup_pool)
    await asyncio.to_thread(_run_execute, SCHEMA_SQL)


async def ping():
    await asyncio.to_thread(_run_ping)


async def get_user(user_id: int):
    return await asyncio.to_thread(
        _run_fetchone,
        "SELECT * FROM users WHERE user_id = %s",
        (user_id,),
    )


async def add_or_update_user(user_id: int, username: str, first_name: str, status: str = "pending"):
    await asyncio.to_thread(
        _run_execute,
        """
        INSERT INTO users (user_id, username, first_name, status)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id) DO UPDATE SET
            username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            status = CASE
                WHEN EXCLUDED.status <> 'pending' THEN EXCLUDED.status
                ELSE users.status
            END
        """,
        (user_id, username, first_name, status),
    )


async def update_user_status(user_id: int, status: str):
    await asyncio.to_thread(
        _run_execute,
        "UPDATE users SET status = %s WHERE user_id = %s",
        (status, user_id),
    )


async def set_user_password(user_id: int, custom_password: str):
    await asyncio.to_thread(
        _run_execute,
        "UPDATE users SET custom_password = %s WHERE user_id = %s",
        (custom_password, user_id),
    )


async def set_user_proxy(user_id: int, proxy: str):
    await asyncio.to_thread(
        _run_execute,
        "UPDATE users SET proxy = %s WHERE user_id = %s",
        (proxy, user_id),
    )


async def add_account(user_id: int, site_id: str, email: str, password: str, invite_code: str):
    await asyncio.to_thread(
        _run_execute,
        """
        INSERT INTO accounts (user_id, site_id, email, password, invite_code)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (user_id, site_id, email, password, invite_code),
    )


async def mark_account_linked(user_id: int, site_id: str, email: str):
    await asyncio.to_thread(
        _run_execute,
        """
        UPDATE accounts
        SET is_linked = TRUE
        WHERE user_id = %s AND site_id = %s AND email = %s
        """,
        (user_id, site_id, email),
    )


async def get_accounts_by_site(user_id: int, site_id: str):
    return await asyncio.to_thread(
        _run_fetchall,
        """
        SELECT *
        FROM accounts
        WHERE user_id = %s AND site_id = %s
        ORDER BY created_at DESC
        """,
        (user_id, site_id),
    )


async def get_latest_account_by_email(user_id: int, email: str):
    return await asyncio.to_thread(
        _run_fetchone,
        """
        SELECT *
        FROM accounts
        WHERE user_id = %s AND email = %s
        ORDER BY created_at DESC
        LIMIT 1
        """,
        (user_id, email),
    )
