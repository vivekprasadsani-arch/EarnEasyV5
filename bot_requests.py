import hashlib
import json
import random
import re
import string
import time
from html import unescape
from urllib.parse import quote, urlparse

import requests


class DeepEarnSigner:
    def __init__(self, anon_uid="1771160569236UVOKPblRUTmiBtiGPDg1hTGbmcSTGJmb"):
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

    def generate_email(self):
        self._bootstrap_session()
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
            raise RuntimeError("EmailMux returned an empty email address")
        self._activate_email(email)
        return email

    def get_messages(self, email):
        resp = self._request(
            "GET",
            f"{self.BASE_URL}/emails?email={quote(email)}",
            headers=self._signed_headers(email),
            timeout=25,
        )
        data = resp.json()
        return data if isinstance(data, list) else []

    def get_message_content(self, email, message_id):
        resp = self._request(
            "GET",
            f"{self.BASE_URL}/{self.LOCALE}/email/{message_id}",
            headers=self._signed_headers(email),
            timeout=25,
        )
        return self._extract_email_html(resp.text)


class EmailnatorClient:
    def __init__(self, proxy_url=None, allow_proxy_fallback=True):
        self.proxy_url = proxy_url
        self.allow_proxy_fallback = allow_proxy_fallback
        self._active_client = None

    def _fallback_client(self):
        return EmailMuxClient(proxy_url=self.proxy_url, allow_proxy_fallback=self.allow_proxy_fallback)

    def generate_email(self):
        legacy_error = None
        try:
            self._active_client = LegacyEmailnatorClient(
                proxy_url=self.proxy_url,
                allow_proxy_fallback=self.allow_proxy_fallback,
            )
            return self._active_client.generate_email()
        except Exception as ex:
            legacy_error = ex

        self._active_client = self._fallback_client()
        try:
            return self._active_client.generate_email()
        except Exception as fallback_error:
            if legacy_error:
                raise RuntimeError(
                    f"Legacy Emailnator failed: {legacy_error}; EmailMux fallback failed: {fallback_error}"
                ) from fallback_error
            raise

    def get_messages(self, email):
        if not self._active_client:
            raise RuntimeError("Email client is not initialized")
        return self._active_client.get_messages(email)

    def get_message_content(self, email, message_id):
        if not self._active_client:
            raise RuntimeError("Email client is not initialized")
        return self._active_client.get_message_content(email, message_id)


class DeepEarnClient:
    def __init__(self, inviter_code="57146564", proxy_url=None, domain="s1.ug5d.com", allow_proxy_fallback=True):
        self.inviter_code = inviter_code
        self.domain = domain
        self.base_url = f"https://{domain}"
        self.session = requests.Session()
        self.session.trust_env = False
        self.allow_proxy_fallback = allow_proxy_fallback
        self.proxy_url = normalize_proxy_url(proxy_url)
        self.using_proxy = bool(self.proxy_url)
        if self.proxy_url:
            self.session.proxies.update({"http": self.proxy_url, "https": self.proxy_url})
        self.signer = DeepEarnSigner()
        self.anon_uid = self.signer.anon_uid
        self.version = "13.5.1"
        self.default_headers = {
            "Accept": "*/*",
            "Content-Type": "application/json;charset=UTF-8",
            "Device-Type": "android",
            "Device-Model": "Nexus 5",
            "Language": "en",
            "Anonymous-Uid": self.anon_uid,
            "User-Language": "en",
            "Network-Type": "unknown",
            "Version": self.version,
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) "
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
            return resp.json()
        except requests.exceptions.RequestException as ex:
            if self.using_proxy and self.allow_proxy_fallback and self.is_proxy_error(ex):
                self._disable_proxy()
                resp = self._request_post(path, data)
                resp.raise_for_status()
                return resp.json()
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
            "currency": "India",
            "email": email,
            "password": password,
            "password_confirm": password,
            "inviter_invite_code": self.inviter_code,
            "cf_token": "",
            "cf_key": "0x4AAAAAABHJvPhqTR_a9Mwu",
            "code": otp,
        }
        return self._post(path, data)


def generate_pwd(length=8):
    return "".join(random.choices(string.digits, k=length))
