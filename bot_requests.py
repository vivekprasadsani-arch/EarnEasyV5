import asyncio
import hashlib
import json
import random
import re
import string
import sys
import time
import imaplib
import email as email_lib
from html import unescape
import logging
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

logger = logging.getLogger(__name__)

# --- Gmail IMAP & Alias Logic ---

class GmailIMAPClient:
    def __init__(self, user, pwd):
        self.user = user
        self.pwd = pwd
        self.mail = None

    def connect(self):
        try:
            self.mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=15)
            self.mail.login(self.user, self.pwd)
            return True
        except Exception as e:
            logger.error(f"Gmail IMAP connection failed for {self.user}: {e}")
            return False

    def get_messages(self, target_email):
        if not self.mail:
            if not self.connect():
                return []
        
        messages = []
        # Search both Inbox and Spam
        for folder in ["INBOX", "[Gmail]/Spam"]:
            try:
                self.mail.select(folder)
                # Search for emails sent to the alias
                status, data = self.mail.search(None, f'TO "{target_email}"')
                if status != 'OK':
                    continue
                
                # Fetch only the last 3 messages to save time
                for m_id in reversed(data[0].split()[-3:]):
                    status, msg_data = self.mail.fetch(m_id, '(RFC822)')
                    msg = email_lib.message_from_bytes(msg_data[0][1])
                    
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode(errors="ignore")
                                break
                    else:
                        body = msg.get_payload(decode=True).decode(errors="ignore")
                    
                    messages.append({
                        "body": body,
                        "subject": str(msg.get("Subject", "")),
                        "messageID": str(m_id)
                    })
            except Exception as e:
                logger.debug(f"IMAP fetch error in {folder}: {e}")
                continue
        return messages

    def close(self):
        try:
            if self.mail:
                self.mail.logout()
        except:
            pass

class GmailEmailClient:
    def __init__(self, site_id, cred):
        """cred = {'email': '...', 'password': '...' (App Password)}"""
        self.site_id = (site_id or "").lower()
        self.cred = cred
        self.imap = GmailIMAPClient(cred['email'], cred['password'])
        # For compatibility with EmailnatorClient interface
        self.provider_name = "gmail_alias"

    def generate_email(self, db_check_func=None):
        """Generates a random dot-alias for the provided Gmail."""
        local_part, domain = self.cred['email'].split('@')
        
        # Limit retries to find a unique alias
        for _ in range(50):
            alias = ""
            # Randomly insert dots
            dots = [random.choice([True, False]) for _ in range(len(local_part)-1)]
            for i in range(len(local_part)-1):
                alias += local_part[i]
                if dots[i]:
                    alias += "."
            alias += local_part[-1]
            email = f"{alias}@{domain}"
            
            if db_check_func:
                if not asyncio.run(db_check_func(email, self.site_id)):
                    return email
            else:
                return email
        return None

    def get_messages(self, email):
        return self.imap.get_messages(email)
        
    def get_message_content(self, email, message_id):
        # The body is already included in get_messages for Gmail logic to speed up
        msgs = self.get_messages(email)
        for m in msgs:
            if m['messageID'] == message_id:
                return m['body']
        return ""

    def close(self):
        self.imap.close()

# --- Existing DeepEarn Signer ---


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

    @staticmethod
    def _is_forbidden_error(ex: Exception) -> bool:
        if isinstance(ex, requests.exceptions.HTTPError):
            response = getattr(ex, "response", None)
            if response is not None and response.status_code == 403:
                return True
        text = str(ex).lower()
        return "403" in text and "forbidden" in text

    def _recovery_candidates(self):
        legacy_factory = lambda: LegacyEmailnatorClient(
            proxy_url=self.proxy_url,
            allow_proxy_fallback=self.allow_proxy_fallback,
        )
        provider_map = {
            "legacy_emailnator": [
                ("bypassed_emailnator", self._bypassed_emailnator_client),
                ("legacy_emailnator", legacy_factory),
            ],
            "bypassed_emailnator": [
                ("bypassed_emailnator", self._bypassed_emailnator_client),
                ("legacy_emailnator", legacy_factory),
            ],
        }
        return provider_map.get(self._provider_name, [])

    def _recover_inbox_client(self, email: str) -> bool:
        previous_provider = self._provider_name
        previous_client = self._active_client
        for provider_name, factory in self._recovery_candidates():
            client = None
            try:
                client = factory()
                self._set_active_client(client, provider_name)
                logger.info("Recovered inbox client for %s: %s -> %s", email, previous_provider, provider_name)
                return True
            except Exception as recovery_error:
                self._close_client(client)
                logger.warning(
                    "Inbox recovery failed for %s via %s: %s",
                    email,
                    provider_name,
                    recovery_error,
                )

        self._active_client = previous_client
        self._provider_name = previous_provider
        return False

    def generate_email(self):
        self.close()

        legacy_error = None
        bypass_error = None

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

        if legacy_error or bypass_error:
            raise RuntimeError(
                f"Legacy Emailnator failed: {legacy_error}; "
                f"Bypassed Emailnator failed: {bypass_error}"
            )
        return None

    def get_messages(self, email):
        if not self._active_client:
            raise RuntimeError("Email client is not initialized")
        try:
            return self._active_client.get_messages(email)
        except Exception as ex:
            if self._is_forbidden_error(ex) and self._recover_inbox_client(email):
                return self._active_client.get_messages(email)
            raise

    def get_message_content(self, email, message_id):
        if not self._active_client:
            raise RuntimeError("Email client is not initialized")
        try:
            return self._active_client.get_message_content(email, message_id)
        except Exception as ex:
            if self._is_forbidden_error(ex) and self._recover_inbox_client(email):
                return self._active_client.get_message_content(email, message_id)
            raise

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


class DeepEarnClientGmail:
    def __init__(self, inviter_code, proxy_url, domain):
        self.inviter_code = inviter_code
        self.domain = domain
        self.base_url = f"https://{domain}"
        self.session = requests.Session()
        self.session.trust_env = False
        if proxy_url:
            p = normalize_proxy_url(proxy_url)
            self.session.proxies = {"http": p, "https": p}
        
        self.uid = str(int(time.time()*1000)) + "".join(random.choices(string.ascii_letters+string.digits, k=34))
        self.headers = {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Device-Type": "android",
            "Anonymous-Uid": self.uid,
            "Version": "13.5.1",
            "User-Agent": "Mozilla/5.0"
        }

    def _post(self, path, data):
        for attempt in range(3):
            try:
                timestamp = str(int(time.time()*1000))
                sorted_keys = sorted(data.keys())
                payload_str = "&" + "&".join([f"{k}={data[k]}" for k in sorted_keys if data[k] not in (None, "")]) if sorted_keys else ""
                signature = hashlib.md5(f"{path}#{self.uid}#{timestamp}#{payload_str}".encode()).hexdigest()
                
                h = self.headers.copy()
                h.update({"Request-Time": timestamp, "X-Sign": signature})
                
                resp = self.session.post(f"{self.base_url}{path}", json=data, headers=h, timeout=20)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if attempt < 2:
                    time.sleep(1)
                    continue
                raise

    def send_otp(self, email):
        return self._post("/api/v1/member/email/get?version=13.5.1", {"email": email, "cf_token": "", "cf_key": "0x4AAAAAABHJvPhqTR_a9Mwu"})

    def register(self, email, password, otp):
        return self._post("/api/v1/member/reg?version=13.5.1", {
            "currency": "India", # Default for Gmail logic in PC tool
            "email": email,
            "password": password,
            "password_confirm": password,
            "inviter_invite_code": self.inviter_code,
            "cf_token": "",
            "cf_key": "0x4AAAAAABHJvPhqTR_a9Mwu",
            "code": otp
        })

    def login(self, email, password):
        resp = self._post("/api/v1/member/login?version=13.5.1", {"account": email, "password": password, "cf_token": "", "cf_key": "0x4AAAAAABHJvPhqTR_a9Mwu"})
        if resp.get("code") == 200 and resp.get("data", {}).get("token"):
            self.session.headers["Authorization"] = f"Bearer {resp['data']['token']}"
        return resp

    def get_info(self):
        return self._post("/api/v1/member/info?version=13.5.1", {})

    def close(self):
        try:
            self.session.close()
        except:
            pass

class DeepEarnClientEmailnator:
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
