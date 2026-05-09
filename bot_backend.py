import asyncio
import io
import logging
import re
import time
import threading

import requests
from PIL import Image, ImageOps

from bot_requests import DeepEarnClient, EmailnatorClient, generate_pwd
from wa_link_gui import WaLinkClient

logger = logging.getLogger(__name__)

# Global lock to ensure 100% success by serializing registration attempts.
# This prevents multiple threads from hitting the same proxy/API at once.
REGISTRATION_LOCK = threading.Lock()

SITES = {
    "india": "s1.4e22.com",
    "pakistan": "p1.x7bb.com",
    "south_africa": "a1.8xy5.com",
    "nigeria": "n1.9uot.com",
}

OTP_SUBJECT_HINTS = ("verification", "verify", "code", "registration", "otp", "confirm")
OTP_POLL_INTERVAL_SECONDS = 3
OTP_MAX_POLLS = 50
OTP_RESEND_POLLS = {12, 28}
MAX_EMAIL_CANDIDATES_PER_ATTEMPT = 10


def _close_quietly(client):
    if not client:
        return
    try:
        client.close()
    except Exception:
        pass


def _extract_otp(*sources):
    patterns = (
        r">\s*(\d{6})\s*<",
        r"\bcode\b[^0-9]{0,20}(\d{6})\b",
        r"\botp\b[^0-9]{0,20}(\d{6})\b",
        r"\b(\d{6})\b",
    )
    for source in sources:
        if not source:
            continue
        # 1. Try patterns on RAW source (works best for HTML like <b>123456</b>)
        for pattern in patterns:
            match = re.search(pattern, str(source), re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1)
                
        # 2. Try patterns on normalized source (stripped of tags)
        normalized = re.sub(r"<[^>]+>", " ", str(source))
        normalized = re.sub(r"\s+", " ", normalized)
        for pattern in patterns:
            match = re.search(pattern, normalized, re.IGNORECASE | re.DOTALL)
            if match:
                return match.group(1)
    return None


def _message_candidates(messages):
    candidates = [
        message
        for message in (messages or [])
        if isinstance(message, dict) and (message.get("messageID") or message.get("uuid"))
    ]
    if not candidates:
        return []

    prioritized = [
        message
        for message in candidates
        if any(hint in str(message.get("subject", "")).lower() for hint in OTP_SUBJECT_HINTS)
    ]
    selected = prioritized or candidates
    return sorted(
        selected,
        key=lambda message: str(
            message.get("createdAt")
            or message.get("created_at")
            or message.get("receivedAt")
            or message.get("time")
            or message.get("messageID")
            or message.get("uuid")
            or ""
        ),
        reverse=True,
    )


def generate_qr_image(url: str, client: WaLinkClient = None) -> bytes:
    try:
        resp = None
        for attempt in range(10):
            try:
                if client:
                    resp = client.session.get(url, timeout=30)
                else:
                    resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                break
            except requests.exceptions.RequestException as e:
                if attempt < 9:
                    time.sleep(2)
                else:
                    raise e

        if not resp:
            raise RuntimeError("Empty response received")

        img = Image.open(io.BytesIO(resp.content))

        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "RGBA":
                background.paste(img, mask=img.split()[3])
            else:
                background.paste(img, mask=img.convert("RGBA").split()[3])
            img = background
        else:
            img = img.convert("RGB")

        img = ImageOps.expand(img, border=50, fill="white")

        out_io = io.BytesIO()
        img.save(out_io, format="PNG")
        return out_io.getvalue()
    except Exception as e:
        raise RuntimeError(f"Failed to generate QR image: {e}")


async def create_account(site_id: str, invite_code: str, proxy: str = None, password: str = "53561106Tojo"):
    domain = SITES.get(site_id)
    if not domain:
        raise ValueError(f"Unknown site: {site_id}")

    def _sync_create():
        with REGISTRATION_LOCK:
            last_error = ""
            current_step = "starting"
            for attempt in range(3):
                email_client = None
                earn_client = None
                email = ""
                try:
                    earn_client = DeepEarnClient(
                        inviter_code=invite_code,
                        proxy_url=proxy,
                        domain=domain,
                        allow_proxy_fallback=True,
                    )

                    current_step = "email generation"
                    for email_try in range(MAX_EMAIL_CANDIDATES_PER_ATTEMPT):
                        _close_quietly(email_client)
                        email_client = EmailnatorClient(proxy_url=proxy, allow_proxy_fallback=True)
                        email = email_client.generate_email()
                        if not email:
                            raise RuntimeError("Failed to generate email")

                        current_step = "otp send"
                        otp_resp = earn_client.send_otp(email)
                        if otp_resp.get("code") == 200:
                            break

                        otp_error = str(otp_resp.get("msg") or "").strip()
                        if "registered" in otp_error.lower():
                            logger.info(
                                "Generated email already registered on attempt %s candidate %s: %s",
                                attempt + 1,
                                email_try + 1,
                                email,
                            )
                            current_step = "email generation"
                            continue

                        raise RuntimeError(f"OTP send failed: {otp_error or 'unknown error'}")
                    else:
                        raise RuntimeError(
                            f"Could not get an unregistered email after {MAX_EMAIL_CANDIDATES_PER_ATTEMPT} tries"
                        )

                    otp = None
                    current_step = "otp inbox polling"
                    provider_name = getattr(email_client, "provider_name", "unknown")
                    logger.info("OTP polling started for %s via %s", email, provider_name)

                    for poll_index in range(OTP_MAX_POLLS):
                        if poll_index:
                            time.sleep(OTP_POLL_INTERVAL_SECONDS)

                        if poll_index in OTP_RESEND_POLLS:
                            resend_resp = earn_client.send_otp(email)
                            if resend_resp.get("code") != 200:
                                logger.warning("OTP resend failed for %s via %s: %s", email, provider_name, resend_resp)
                            else:
                                logger.info("OTP resent for %s via %s on poll %s", email, provider_name, poll_index)

                        msgs = email_client.get_messages(email)
                        if not msgs:
                            continue

                        for message in _message_candidates(msgs):
                            otp = _extract_otp(
                                message.get("subject"),
                                message.get("text"),
                                message.get("preview"),
                                message.get("snippet"),
                            )
                            if otp:
                                break

                            message_id = message.get("messageID") or message.get("uuid")
                            if not message_id:
                                continue

                            try:
                                content = email_client.get_message_content(email, message_id)
                            except Exception as content_error:
                                logger.warning(
                                    "OTP content fetch failed for %s via %s: %s",
                                    message_id,
                                    provider_name,
                                    content_error,
                                )
                                continue

                            otp = _extract_otp(content)
                            if otp:
                                break

                        if otp:
                            break

                    if not otp:
                        raise RuntimeError(
                            f"Timeout waiting for OTP email via {provider_name} "
                            f"after {OTP_MAX_POLLS * OTP_POLL_INTERVAL_SECONDS}s"
                        )

                    current_step = "registration"
                    # Small sleep to ensure backend is ready for the code
                    time.sleep(2)
                    reg_resp = earn_client.register(email, password, otp)
                    if reg_resp.get("code") != 200:
                        raise RuntimeError(f"Registration failed: {reg_resp.get('msg')}")

                    return email
                except Exception as e:
                    last_error = str(e)
                    last_error = re.sub(r"https?://[^\s<]+", "<hidden_url>", last_error)
                    if "ProxyError" in last_error or "Remote end closed" in last_error:
                        last_error = f"{current_step}: Proxy connection dropped."
                    elif "proxy" in last_error.lower() and "dropped" in last_error.lower():
                        last_error = f"{current_step}: Proxy connection dropped."
                    else:
                        last_error = f"{current_step}: {last_error}"
                    logger.warning("Account creation attempt %s failed: %s", attempt + 1, last_error)
                    time.sleep(5) # Delay before retry
                finally:
                    _close_quietly(email_client)
                    _close_quietly(earn_client)

            raise RuntimeError(f"Account creation failed after retries: {last_error}")

    return await asyncio.to_thread(_sync_create)


async def generate_wa_qr(site_id: str, email: str, password: str, proxy: str = None):
    domain = SITES.get(site_id)

    def _sync_qr():
        last_error = ""
        for attempt in range(2):
            client = None
            try:
                client = WaLinkClient(domain=domain, proxy_url=proxy, allow_proxy_fallback=True)
                login_res = client.login(email, password)
                if login_res.get("code") != 200:
                    raise RuntimeError(f"Login failed: {login_res.get('msg')}")

                invite_code = str((login_res.get("data") or {}).get("invite_code", "")).strip()

                apply_res = client.apply_server()
                if apply_res.get("code") != 200 and client._is_login_required_response(apply_res):
                    client.relogin()
                    client.apply_server()

                wa_login = client.wa_login()
                if wa_login.get("code") != 200 and client._is_login_required_response(wa_login):
                    client.relogin()
                    wa_login = client.wa_login()

                device_id = str((wa_login.get("data") or {}).get("device_id") or "").strip()
                if not device_id:
                    raise RuntimeError("No device_id found")

                qr_res = client.wa_qrcode(device_id, invite_code)
                if qr_res.get("code") != 200 and client._is_login_required_response(qr_res):
                    client.relogin()
                    qr_res = client.wa_qrcode(device_id, invite_code)

                qr_url = str((qr_res.get("data") or {}).get("qr_code", "")).strip()

                for _ in range(15):
                    if qr_url and "loading.gif" not in qr_url:
                        break
                    time.sleep(2)
                    qr_res = client.wa_qrcode(device_id, invite_code)
                    qr_url = str((qr_res.get("data") or {}).get("qr_code", "")).strip()

                if not qr_url or "loading.gif" in qr_url:
                    raise RuntimeError("No valid QR code URL found after polling.")

                qr_bytes = generate_qr_image(qr_url, client)
                if not qr_bytes:
                    raise RuntimeError("Failed to download QR image")

                return client, device_id, invite_code, qr_bytes
            except Exception as e:
                last_error = str(e)
                last_error = re.sub(r"https?://[^\s<]+", "<hidden_url>", last_error)
                if "ProxyError" in last_error or "Remote end closed" in last_error:
                    last_error = "Proxy connection dropped."
                _close_quietly(client)
                time.sleep(2)

        raise RuntimeError(f"QR Generation failed after retries: {last_error}")

    return await asyncio.to_thread(_sync_qr)


async def poll_wa_status(client: WaLinkClient, device_id: str, invite_code: str):
    def _sync_poll():
        try:
            res = client.wa_qrcode(device_id, invite_code)
            return res
        except Exception as e:
            return {"code": 500, "msg": str(e)}

    return await asyncio.to_thread(_sync_poll)
