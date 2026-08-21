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
    def _sync_add():
        rows = _request(
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
            prefer="return=representation",
        )
        return rows[0]['id'] if rows else None
    return await asyncio.to_thread(_sync_add)


async def get_account_by_id(account_id: int):
    """Retrieve a specific account record by its ID."""
    return await asyncio.to_thread(
        _fetch_first,
        "accounts",
        filters={"id": f"eq.{int(account_id)}"}
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

async def get_all_accounts(user_id: int):
    return await asyncio.to_thread(
        _select,
        "accounts",
        filters={
            "user_id": f"eq.{int(user_id)}",
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


async def update_user_last_request(user_id: int):
    """Updates the last_request_at timestamp for the user."""
    import datetime
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    await asyncio.to_thread(
        _request,
        "PATCH",
        "users",
        params={"user_id": f"eq.{int(user_id)}"},
        json={"last_request_at": now_iso},
        prefer="return=minimal",
    )


async def update_user_main_account(user_id: int, main_mobile: str, main_invite_code: str):
    """Updates the user's main C88ZZ account phone number and invite code."""
    await asyncio.to_thread(
        _request,
        "PATCH",
        "users",
        params={"user_id": f"eq.{int(user_id)}"},
        json={"main_mobile": main_mobile, "main_invite_code": main_invite_code},
        prefer="return=minimal",
    )


async def get_all_users_admin():
    """Fetches all registered users in the database for the admin panel."""
    return await asyncio.to_thread(
        _select,
        "users",
        order="user_id.desc",
    )


async def get_all_accounts_admin():
    """Fetches all C88ZZ accounts/links from all users for the admin panel."""
    return await asyncio.to_thread(
        _select,
        "accounts",
        order="id.desc",
    )


async def update_admin_credentials(username: str, password: str):
    """Updates the admin panel username and password."""
    await asyncio.to_thread(
        _request,
        "PATCH",
        "users",
        params={"user_id": f"eq.{int(config.ADMIN_USER_ID)}"},
        json={"admin_panel_user": username, "admin_panel_pass": password},
        prefer="return=minimal",
    )


async def set_user_payment_details(user_id: int, method: str, details: str):
    """Updates the user's payment method and number/address."""
    await asyncio.to_thread(
        _request,
        "PATCH",
        "users",
        params={"user_id": f"eq.{int(user_id)}"},
        json={"payment_method": method, "payment_details": details},
        prefer="return=minimal",
    )


async def add_withdrawal_request(user_id: int, amount_points: int, amount_usd: float, method: str, details: str):
    """Inserts a new pending withdrawal request."""
    return await asyncio.to_thread(
        _request,
        "POST",
        "withdrawals",
        json=[{
            "user_id": int(user_id),
            "amount_points": int(amount_points),
            "amount_usd": float(amount_usd),
            "payment_method": method,
            "payment_details": details,
            "status": "pending"
        }],
        prefer="return=representation"
    )


async def update_withdrawal_status(wd_id: int, status: str):
    """Updates the status of a specific withdrawal request."""
    await asyncio.to_thread(
        _request,
        "PATCH",
        "withdrawals",
        params={"id": f"eq.{int(wd_id)}"},
        json={"status": status},
        prefer="return=minimal",
    )


async def get_withdrawal_by_id(wd_id: int):
    """Fetches a specific withdrawal request details."""
    return await asyncio.to_thread(
        _fetch_first,
        "withdrawals",
        filters={"id": f"eq.{int(wd_id)}"},
    )


async def get_all_withdrawals_admin():
    """Fetches all withdrawal requests for the admin panel."""
    return await asyncio.to_thread(
        _select,
        "withdrawals",
        order="id.desc",
    )


async def get_latest_account_by_site(user_id: int, site_id: str):
    """Fetches the latest registered account for a specific user and country."""
    def _sync_get():
        return _fetch_first(
            "accounts",
            filters={
                "user_id": f"eq.{int(user_id)}",
                "site_id": f"eq.{site_id}",
            },
            order="id.desc",
        )
    return await asyncio.to_thread(_sync_get)


async def update_account_own_invite_code(account_id: int, own_invite_code: str):
    """Updates the registered account's own invite code in the database."""
    await asyncio.to_thread(
        _request,
        "PATCH",
        "accounts",
        params={"id": f"eq.{int(account_id)}"},
        json={"own_invite_code": own_invite_code},
        prefer="return=minimal",
    )


async def update_account_session_id(account_id: int, session_id: str):
    """Updates the registered account's WhatsApp session ID in the database."""
    await asyncio.to_thread(
        _request,
        "PATCH",
        "accounts",
        params={"id": f"eq.{int(account_id)}"},
        json={"session_id": session_id},
        prefer="return=minimal",
    )


async def update_account_linked_status(account_id: int, is_linked: bool):
    """Updates the linked status of the account in the database."""
    await asyncio.to_thread(
        _request,
        "PATCH",
        "accounts",
        params={"id": f"eq.{int(account_id)}"},
        json={"is_linked": is_linked},
        prefer="return=minimal",
    )




