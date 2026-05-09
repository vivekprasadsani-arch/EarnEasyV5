import asyncio

import requests

import config

_session = None


def _get_base_url() -> str:
    if not config.SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL is not set. Add it to your .env or Render environment variables.")
    return config.SUPABASE_URL.rstrip("/") + "/rest/v1"


def _get_api_key() -> str:
    key = (config.SUPABASE_KEY or "").strip()
    if not key:
        raise RuntimeError(
            "SUPABASE_KEY is not set. Use your Supabase anon/service_role key, or a publishable key with open RLS policies."
        )
    return key


def _get_session() -> requests.Session:
    global _session
    if _session is not None:
        return _session

    session = requests.Session()
    session.trust_env = False
    api_key = _get_api_key()
    session.headers.update(
        {
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
    )
    _session = session
    return _session


def _request(method: str, table_name: str, *, params=None, json=None, prefer=None):
    session = _get_session()
    headers = {}
    if prefer:
        headers["Prefer"] = prefer

    response = session.request(
        method=method,
        url=f"{_get_base_url()}/{table_name}",
        params=params,
        json=json,
        headers=headers,
        timeout=30,
    )

    if response.status_code >= 400:
        try:
            details = response.json()
        except ValueError:
            details = response.text
        raise RuntimeError(f"Supabase request failed for {table_name}: {details}")

    if not response.content:
        return []

    try:
        return response.json()
    except ValueError:
        return []


def _select(table_name: str, *, columns="*", filters=None, order=None, limit=None):
    params = {"select": columns}
    if filters:
        params.update(filters)
    if order:
        params["order"] = order
    if limit is not None:
        params["limit"] = str(limit)
    return _request("GET", table_name, params=params)


def _fetch_first(table_name: str, *, columns="*", filters=None, order=None):
    rows = _select(table_name, columns=columns, filters=filters, order=order, limit=1)
    return rows[0] if rows else None


async def init_db():
    def _sync_init():
        try:
            _select("users", columns="user_id", limit=1)
            _select("accounts", columns="id", limit=1)
        except Exception as exc:
            raise RuntimeError(
                "Supabase API is reachable but tables or policies are not ready. Run supabase_schema.sql in Supabase SQL Editor first."
            ) from exc

    await asyncio.to_thread(_sync_init)


async def ping():
    await asyncio.to_thread(lambda: _select("users", columns="user_id", limit=1))


async def get_user(user_id: int):
    return await asyncio.to_thread(
        _fetch_first,
        "users",
        filters={"user_id": f"eq.{int(user_id)}"},
    )


async def add_or_update_user(user_id: int, username: str, first_name: str, status: str = "pending"):
    def _sync_upsert():
        existing = _fetch_first("users", columns="status", filters={"user_id": f"eq.{int(user_id)}"})
        effective_status = status
        if status == "pending" and existing and existing.get("status"):
            effective_status = existing["status"]

        _request(
            "POST",
            "users",
            params={"on_conflict": "user_id"},
            json=[
                {
                    "user_id": int(user_id),
                    "username": username,
                    "first_name": first_name,
                    "status": effective_status,
                }
            ],
            prefer="resolution=merge-duplicates,return=minimal",
        )

    await asyncio.to_thread(_sync_upsert)


async def update_user_status(user_id: int, status: str):
    await asyncio.to_thread(
        _request,
        "PATCH",
        "users",
        params={"user_id": f"eq.{int(user_id)}"},
        json={"status": status},
        prefer="return=minimal",
    )


async def set_user_password(user_id: int, custom_password: str):
    await asyncio.to_thread(
        _request,
        "PATCH",
        "users",
        params={"user_id": f"eq.{int(user_id)}"},
        json={"custom_password": custom_password},
        prefer="return=minimal",
    )


async def set_user_proxy(user_id: int, proxy: str):
    await asyncio.to_thread(
        _request,
        "PATCH",
        "users",
        params={"user_id": f"eq.{int(user_id)}"},
        json={"proxy": proxy},
        prefer="return=minimal",
    )


async def add_account(user_id: int, site_id: str, email: str, password: str, invite_code: str):
    await asyncio.to_thread(
        _request,
        "POST",
        "accounts",
        json=[
            {
                "user_id": int(user_id),
                "site_id": site_id,
                "email": email,
                "password": password,
                "invite_code": invite_code,
            }
        ],
        prefer="return=minimal",
    )


async def mark_account_linked(user_id: int, site_id: str, email: str):
    def _sync_mark_linked():
        target = _fetch_first(
            "accounts",
            columns="id",
            filters={
                "user_id": f"eq.{int(user_id)}",
                "site_id": f"eq.{site_id}",
                "email": f"eq.{email}",
                "is_linked": "eq.false",
            },
            order="created_at.desc",
        )
        if not target:
            target = _fetch_first(
                "accounts",
                columns="id",
                filters={
                    "user_id": f"eq.{int(user_id)}",
                    "site_id": f"eq.{site_id}",
                    "email": f"eq.{email}",
                },
                order="created_at.desc",
            )
        if not target:
            return

        _request(
            "PATCH",
            "accounts",
            params={"id": f"eq.{int(target['id'])}"},
            json={"is_linked": True},
            prefer="return=minimal",
        )

    await asyncio.to_thread(_sync_mark_linked)


async def get_accounts_by_site(user_id: int, site_id: str):
    return await asyncio.to_thread(
        _select,
        "accounts",
        filters={
            "user_id": f"eq.{int(user_id)}",
            "site_id": f"eq.{site_id}",
        },
        order="created_at.desc",
    )


async def get_latest_account_by_email(user_id: int, email: str):
    return await asyncio.to_thread(
        _fetch_first,
        "accounts",
        filters={
            "user_id": f"eq.{int(user_id)}",
            "email": f"eq.{email}",
        },
        order="created_at.desc",
    )


async def is_email_used_on_site(site_id: str, email: str):
    """Checks if a specific email (alias) has already been successfully linked on a specific site."""
    def _sync_check():
        target = _fetch_first(
            "accounts",
            columns="id",
            filters={
                "site_id": f"eq.{site_id}",
                "email": f"eq.{email}",
                "is_linked": "eq.true",
            }
        )
        return target is not None
    return await asyncio.to_thread(_sync_check)
