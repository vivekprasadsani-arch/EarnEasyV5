import hashlib
import re
import random
import string
import time
import uuid

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


class EmailnatorClient:
    def __init__(self, proxy_url=None, allow_proxy_fallback=True):
        self.session = requests.Session()
        self.session.trust_env = False
        self.allow_proxy_fallback = allow_proxy_fallback
        self.using_proxy = bool(proxy_url)
        if proxy_url:
            self.session.proxies.update({"http": proxy_url, "https": proxy_url})
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


class DeepEarnClient:
    def __init__(self, inviter_code="57146564", proxy_url=None, domain="s1.ug5d.com", allow_proxy_fallback=True):
        self.inviter_code = inviter_code
        self.domain = domain
        self.base_url = f"https://{domain}"
        self.session = requests.Session()
        self.session.trust_env = False
        self.allow_proxy_fallback = allow_proxy_fallback
        self.using_proxy = bool(proxy_url)
        if proxy_url:
            self.session.proxies.update({"http": proxy_url, "https": proxy_url})
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
