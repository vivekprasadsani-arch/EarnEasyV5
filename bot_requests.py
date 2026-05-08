import asyncio
import hashlib
import json
import random
import re
import string
import sys
import time
from html import unescape
from pathlib import Path
from urllib.parse import quote, urlparse

import requests


class DeepEarnSigner:
    def __init__(self, anon_uid=None):
        if anon_uid is None:
            # Default static uid kept for legacy compat, but each new client should pass its own
            anon_uid = "1771160569236UVOKPblRUTmiBtiGPDg1hTGbmcSTGJmb"
        self.anon_uid = anon_uid

    @staticmethod
    def get_md5(s):
        return hashlib.md5(s.encode("utf-8")).hexdigest()

    def sign(self, path, data, timestamp):
        sorted_keys = sorted(data.keys())
        payload = ""
        if sorted_keys:
            payload = "&" + "&".join([f"{k}={data[k]}" for k in sorted_keys if data[k] not in (None, "")])
        return self.get_md5(f"{path}#{self.anon_uid}#{timestamp}#{payload}")


def normalize_proxy_url(proxy_url: str) -> str:
    value = (proxy_url or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if not parsed.scheme:
        value = f"http://{value}"
    return value


_CF_BYPASSER_CLASS = None


def _load_camoufox_bypasser():
    global _CF_BYPASSER_CLASS
    if _CF_BYPASSER_CLASS is not None:
        return _CF_BYPASSER_CLASS

    repo_dir = Path(__file__).resolve().parent / "CloudflareBypassForScraping-main"
    if not repo_dir.exists():
        raise RuntimeError("CloudflareBypassForScraping-main repo was not found")

    repo_path = str(repo_dir)
    if repo_path not in sys.path:
        sys.path.insert(0, repo_path)

    from cf_bypasser.core.bypasser import CamoufoxBypasser

    _CF_BYPASSER_CLASS = CamoufoxBypasser
    return _CF_BYPASSER_CLASS


class LegacyEmailnatorClient:
    def __init__(self, proxy_url=None, allow_proxy_fallback=True):
        self.session = requests.Session()
        self.session.trust_env = False
        self.allow_proxy_fallback = allow_proxy_fallback
        self.proxy_url = normalize_proxy_url(proxy_url)
        self.using_proxy = bool(self.proxy_url)
        if self.proxy_url:
            self.session.proxies.update({"http": self.proxy_url, "https": self.proxy_url})
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        self.init_session()

    @staticmethod
    def is_proxy_error(ex: Exception) -> bool:
        if isinstance(ex, requests.exceptions.ProxyError):
            return True
        text = str(ex).lower()
        return (
            "proxy" in text
            or "407" in text
            or "remote end closed" in text
            or "tunnel connection failed" in text
        )

    def _disable_proxy(self):
        self.session.proxies.clear()
        self.using_proxy = False

    def _request(self, method, url, **kwargs):
        try:
            resp = self.session.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as ex:
            if self.using_proxy and self.allow_proxy_fallback and self.is_proxy_error(ex):
                self._disable_proxy()
                resp = self.session.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            raise

    def init_session(self):
        self._request("GET", "https://www.emailnator.com/", timeout=25)
        xsrf_token = self.session.cookies.get("XSRF-TOKEN")
        if xsrf_token:
            self.session.headers.update({"X-XSRF-TOKEN": requests.utils.unquote(xsrf_token)})

    def generate_email(self):
        resp = self._request(
            "POST",
            "https://www.emailnator.com/generate-email", json={"email": ["dotGmail"]}, timeout=25
        )
        return (resp.json().get("email") or [None])[0]

    def get_messages(self, email):
        resp = self._request(
            "POST",
            "https://www.emailnator.com/message-list", json={"email": email}, timeout=25
        )
        return resp.json().get("messageData", [])

    def get_message_content(self, email, message_id):
        resp = self._request(
            "POST",
            "https://www.emailnator.com/message-list",
            json={"email": email, "messageID": message_id},
            timeout=25,
        )
        return resp.text

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass


class BypassedEmailnatorClient(LegacyEmailnatorClient):
    EMAILNATOR_URL = "https://www.emailnator.com/"

    def init_session(self):
        self._bootstrap_via_cloudflare_bypass()
        xsrf_token = self.session.cookies.get("XSRF-TOKEN")
        if xsrf_token:
            self.session.headers.update({"X-XSRF-TOKEN": requests.utils.unquote(xsrf_token)})

    def _bootstrap_via_cloudflare_bypass(self):
        last_error = None
        if self.proxy_url and self.allow_proxy_fallback:
            proxy_candidates = [self.proxy_url, None]
        elif self.proxy_url:
            proxy_candidates = [self.proxy_url]
        else:
            proxy_candidates = [None]

        for proxy_candidate in proxy_candidates:
            try:
                bypasser_cls = _load_camoufox_bypasser()
                cache_file = str(Path(__file__).resolve().with_name("cf_emailnator_cookie_cache.json"))
                bypasser = bypasser_cls(max_retries=3, log=False, cache_file=cache_file)
                data = asyncio.run(
                    bypasser.get_or_generate_html(
                        self.EMAILNATOR_URL,
                        proxy=proxy_candidate,
                        bypass_cache=False,
                    )
                )
                if not data or not data.get("cookies"):
                    raise RuntimeError("Cloudflare bypass returned no cookies")

                self.session.headers["User-Agent"] = data.get("user_agent") or self.session.headers["User-Agent"]
                self.session.headers["Referer"] = self.EMAILNATOR_URL
                self.session.headers["Origin"] = self.EMAILNATOR_URL.rstrip("/")

                self.session.cookies.clear()
                for cookie_name, cookie_value in (data.get("cookies") or {}).items():
                    self.session.cookies.set(cookie_name, cookie_value, domain="www.emailnator.com")

                if proxy_candidate:
                    self.session.proxies.update({"http": proxy_candidate, "https": proxy_candidate})
                    self.using_proxy = True
                else:
                    self.session.proxies.clear()
                    self.using_proxy = False
                return
            except Exception as ex:
                last_error = ex

        if last_error:
            raise last_error
        raise RuntimeError("Cloudflare bypass bootstrap failed")


class EmailMuxClient:
    BASE_URL = "https://emailmux.com"
    API_SECRET = "yjd683c@47"
    LOCALE = "en"

    def __init__(self, proxy_url=None, allow_proxy_fallback=True):
        self.session = requests.Session()
        self.session.trust_env = False
        self.allow_proxy_fallback = allow_proxy_fallback
        self.proxy_url = normalize_proxy_url(proxy_url)
        self.using_proxy = bool(self.proxy_url)
        self._active_email = ""
        self._active_email_ts = 0.0
        if self.proxy_url:
            self.session.proxies.update({"http": self.proxy_url, "https": self.proxy_url})
        self.session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Referer": f"{self.BASE_URL}/{self.LOCALE}/temporary-gmail",
            }
        )

    @staticmethod
    def is_proxy_error(ex: Exception) -> bool:
        if isinstance(ex, requests.exceptions.ProxyError):
            return True
        text = str(ex).lower()
        return (
            "proxy" in text
            or "407" in text
            or "remote end closed" in text
            or "tunnel connection failed" in text
        )

    def _disable_proxy(self):
        self.session.proxies.clear()
        self.using_proxy = False

    def _request(self, method, url, **kwargs):
        try:
            resp = self.session.request(method, url, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as ex:
            if self.using_proxy and self.allow_proxy_fallback and self.is_proxy_error(ex):
                self._disable_proxy()
                resp = self.session.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            raise

    def _signed_headers(self, email: str) -> dict:
        timestamp = str(int(time.time() * 1000))
        signature = hashlib.md5(f"{self.API_SECRET}{email}{timestamp}".encode("utf-8")).hexdigest()
        return {
            "Content-Type": "application/json",
            "X-API-Timestamp": timestamp,
            "X-API-Signature": signature,
        }

    def _bootstrap_session(self):
        self._request("GET", f"{self.BASE_URL}/domains", timeout=25)

    def _activate_email(self, email: str):
        resp = self._request(
            "GET",
            f"{self.BASE_URL}/use-email?email={quote(email)}",
            headers=self._signed_headers(email),
            timeout=25,
        )
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(f"EmailMux activation failed: {data.get('msg') or 'unknown error'}")
        self._active_email = email
        self._active_email_ts = time.time()

    def _ensure_email_active(self, email: str, *, force: bool = False):
        same_email = email and email == self._active_email
        recently_activated = (time.time() - self._active_email_ts) < 20
        if force or not same_email or not recently_activated:
            self._activate_email(email)

    @staticmethod
    def _extract_email_html(page_html: str) -> str:
        match = re.search(
            r'<script id="email-html-data" type="application/json">\s*"(.*?)"\s*</script>',
            page_html,
            re.S,
        )
        if not match:
            return page_html
        encoded = f'"{match.group(1)}"'
        return unescape(json.loads(encoded))

    @staticmethod
    def _is_deepearn_compatible_email(email: str) -> bool:
        local_part, _, domain = (email or "").partition("@")
        return bool(local_part) and domain.lower() == "gmail.com" and "+" not in local_part

    def generate_email(self):
        self._bootstrap_session()
        last_error = None
        for _ in range(8):
            resp = self._request(
                "POST",
                f"{self.BASE_URL}/generate-email",
                json={"domains": ["gmail"]},
                timeout=25,
            )
            data = resp.json()
            if data.get("status") != "success":
                raise RuntimeError(f"EmailMux generation failed: {data.get('msg') or 'unknown error'}")
            email = (data.get("email") or "").strip()
            if not email:
                last_error = RuntimeError("EmailMux returned an empty email address")
                continue
            self._ensure_email_active(email, force=True)
            if self._is_deepearn_compatible_email(email):
                return email
            last_error = RuntimeError(f"EmailMux generated unsupported alias: {email}")

        if last_error:
            raise last_error
        raise RuntimeError("EmailMux could not generate a supported gmail address")

    def get_messages(self, email):
        self._ensure_email_active(email)
        resp = self._request(
            "GET",
            f"{self.BASE_URL}/emails?email={quote(email)}",
            headers=self._signed_headers(email),
            timeout=25,
        )
        data = resp.json()
        if not isinstance(data, list):
            return []
        normalized = []
        for item in data:
            if isinstance(item, dict):
                clone = dict(item)
                if clone.get("uuid") and not clone.get("messageID"):
                    clone["messageID"] = clone["uuid"]
                normalized.append(clone)
        return normalized

    def get_message_content(self, email, message_id):
        self._ensure_email_active(email)
        resp = self._request(
            "GET",
            f"{self.BASE_URL}/{self.LOCALE}/email/{message_id}",
            headers=self._signed_headers(email),
            timeout=25,
        )
        return self._extract_email_html(resp.text)

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass


class EmailnatorClient:
    def __init__(self, proxy_url=None, allow_proxy_fallback=True):
        self.proxy_url = proxy_url
        self.allow_proxy_fallback = allow_proxy_fallback
        self._active_client = None
        self._provider_name = "uninitialized"

    @property
    def provider_name(self):
        return self._provider_name

    def _close_client(self, client):
        if not client:
            return
        try:
            client.close()
        except Exception:
            pass

    def _set_active_client(self, client, provider_name):
        if self._active_client and self._active_client is not client:
            self._close_client(self._active_client)
        self._active_client = client
        self._provider_name = provider_name

    def _attempt_generate(self, provider_name, factory):
        client = None
        try:
            client = factory()
            email = client.generate_email()
            if not email:
                raise RuntimeError(f"{provider_name} returned an empty email address")
            self._set_active_client(client, provider_name)
            return email, None
        except Exception as ex:
            self._close_client(client)
            return None, ex

    def _bypassed_emailnator_client(self):
        return BypassedEmailnatorClient(
            proxy_url=self.proxy_url,
            allow_proxy_fallback=self.allow_proxy_fallback,
        )

    def _fallback_client(self):
        return EmailMuxClient(proxy_url=self.proxy_url, allow_proxy_fallback=self.allow_proxy_fallback)

    def generate_email(self):
        import os
        # If SKIP_BROWSER_BYPASS=1 (e.g. on Render free tier to avoid OOM),
        # skip Camoufox entirely and go straight to EmailMuxClient.
        skip_browser = os.getenv("SKIP_BROWSER_BYPASS", "").strip() in ("1", "true", "yes")
        self.close()

        legacy_error = None
        bypass_error = None

        if not skip_browser:
            email, legacy_error = self._attempt_generate(
                "legacy_emailnator",
                lambda: LegacyEmailnatorClient(
                    proxy_url=self.proxy_url,
                    allow_proxy_fallback=self.allow_proxy_fallback,
                ),
            )
            if email:
                return email

            email, bypass_error = self._attempt_generate(
                "bypassed_emailnator",
                self._bypassed_emailnator_client,
            )
            if email:
                return email
        else:
            # Skip legacy + browser bypass, log why
            legacy_error = RuntimeError("Skipped (SKIP_BROWSER_BYPASS=1)")
            bypass_error = RuntimeError("Skipped (SKIP_BROWSER_BYPASS=1)")

        email, fallback_error = self._attempt_generate("emailmux_fallback", self._fallback_client)
        if email:
            return email

        if legacy_error or bypass_error:
            raise RuntimeError(
                f"Legacy Emailnator failed: {legacy_error}; "
                f"Bypassed Emailnator failed: {bypass_error}; "
                f"EmailMux fallback failed: {fallback_error}"
            ) from fallback_error
        raise fallback_error

    def get_messages(self, email):
        if not self._active_client:
            raise RuntimeError("Email client is not initialized")
        return self._active_client.get_messages(email)

    def get_message_content(self, email, message_id):
        if not self._active_client:
            raise RuntimeError("Email client is not initialized")
        return self._active_client.get_message_content(email, message_id)

    def close(self):
        self._close_client(self._active_client)
        self._active_client = None
        self._provider_name = "uninitialized"


DOMAIN_CURRENCY_MAP = {
    "p1.x7bb.com":   "Pakistan",
    "s1.4e22.com":   "India",
    "a1.8xy5.com":   "SouthAfrica",
    "n1.9uot.com":   "Nigeria",
}


class DeepEarnClient:
    def __init__(self, inviter_code="57146564", proxy_url=None, domain="s1.ug5d.com", allow_proxy_fallback=True):
        self.inviter_code = inviter_code
        self.domain = domain
        self.base_url = f"https://{domain}"
        self.currency = DOMAIN_CURRENCY_MAP.get((domain or "").strip().lower(), "India")
        self.session = requests.Session()
        self.session.trust_env = False
        self.allow_proxy_fallback = allow_proxy_fallback
        self.proxy_url = normalize_proxy_url(proxy_url)
        self.using_proxy = bool(self.proxy_url)
        if self.proxy_url:
            self.session.proxies.update({"http": self.proxy_url, "https": self.proxy_url})
        # Each client instance gets its own unique anon_uid — sessions are fully isolated
        import uuid as _uuid
        ts = int(time.time() * 1000)
        rand = ''.join(random.choices(string.ascii_letters + string.digits, k=24))
        self.anon_uid = f"{ts}{rand}"
        self.signer = DeepEarnSigner(anon_uid=self.anon_uid)
        self.version = "13.5.1"
        self.default_headers = {
            "Accept": "*/*",
            "Content-Type": "application/json;charset=UTF-8",
            "Device-Type": "android",
            "Device-Model": "Pixel 6",
            "Language": "en",
            "Anonymous-Uid": self.anon_uid,
            "User-Language": "en",
            "Network-Type": "unknown",
            "Version": self.version,
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 12; Pixel 6 Build/SQ3A.220705.004) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Mobile Safari/537.36"
            ),
        }

    @staticmethod
    def is_proxy_error(ex: Exception) -> bool:
        if isinstance(ex, requests.exceptions.ProxyError):
            return True
        text = str(ex).lower()
        return (
            "proxy" in text
            or "407" in text
            or "remote end closed" in text
            or "tunnel connection failed" in text
        )

    def _disable_proxy(self):
        self.session.proxies.clear()
        self.using_proxy = False

    def _request_post(self, path, data):
        return self.session.post(
            f"{self.base_url}{path}", json=data, headers=self.prepare_headers(path, data), timeout=25
        )

    def _post(self, path, data):
        try:
            resp = self._request_post(path, data)
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError:
                preview = resp.text[:300].strip()
                return {"code": -1, "msg": f"Non-JSON response (HTTP {resp.status_code}): {preview}"}
        except requests.exceptions.RequestException as ex:
            if self.using_proxy and self.allow_proxy_fallback and self.is_proxy_error(ex):
                self._disable_proxy()
                resp = self._request_post(path, data)
                resp.raise_for_status()
                try:
                    return resp.json()
                except ValueError:
                    preview = resp.text[:300].strip()
                    return {"code": -1, "msg": f"Non-JSON response after proxy fallback (HTTP {resp.status_code}): {preview}"}
            raise

    def prepare_headers(self, path, data):
        timestamp = str(int(time.time() * 1000))
        headers = self.default_headers.copy()
        headers["Request-Time"] = timestamp
        headers["X-Sign"] = self.signer.sign(path, data, timestamp)
        return headers

    def send_otp(self, email):
        path = f"/api/v1/member/email/get?version={self.version}"
        data = {"email": email, "cf_token": "", "cf_key": "0x4AAAAAABHJvPhqTR_a9Mwu"}
        return self._post(path, data)

    def register(self, email, password, otp):
        path = f"/api/v1/member/reg?version={self.version}"
        data = {
            "currency": self.currency,  # Correct per-domain currency
            "email": email,
            "password": password,
            "password_confirm": password,
            "inviter_invite_code": self.inviter_code,
            "cf_token": "",
            "cf_key": "0x4AAAAAABHJvPhqTR_a9Mwu",
            "code": otp,
        }
        return self._post(path, data)

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass


def generate_pwd(length=8):
    return "".join(random.choices(string.digits, k=length))
