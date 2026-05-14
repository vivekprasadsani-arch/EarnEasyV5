import hashlib
import time
import uuid
from urllib.parse import urlparse

import requests


def make_anonymous_uid() -> str:
    return f"{int(time.time() * 1000)}{uuid.uuid4().hex[:36]}"


class Signer:
    @staticmethod
    def sign(path: str, data: dict, anonymous_uid: str, request_time: str) -> str:
        keys = sorted(data.keys())
        payload = ""
        if keys:
            payload = "&" + "&".join(f"{k}={data[k]}" for k in keys if data[k] not in (None, ""))
        raw = f"{path}#{anonymous_uid}#{request_time}#{payload}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()


class WaLinkClient:
    DOMAIN_VERSION_MAP = {
        "p1.x7bb.com":   "13.1.1", # Pakistan uses older version
        "p1.3dp9.com":   "13.1.1", 
        "s1.n8o9.com":   "13.5.1",
        "s1.4e22.com":   "13.5.1",
        "a1.8xy5.com":   "13.5.1",
        "n1.9uot.com":   "13.5.1"
    }

    def __init__(self, domain: str, proxy_url: str = "", allow_proxy_fallback: bool = True):
        self.domain = ""
        self.version = "13.5.1"
        self.base_url = ""
        self.anonymous_uid = make_anonymous_uid()
        self.device_id = str(uuid.uuid4())
        self.allow_proxy_fallback = allow_proxy_fallback
        self.proxy_url = self.normalize_proxy_url(proxy_url)
        self.token = "X"
        self.login_email = ""
        self.login_password = ""
        self.session = requests.Session()
        self.session.trust_env = False
        self.using_proxy = bool(self.proxy_url)
        if self.using_proxy:
            self.session.proxies.update({"http": self.proxy_url, "https": self.proxy_url})
        self.default_headers = {
            "Accept": "*/*",
            "Content-Type": "application/json;charset=UTF-8",
            "Device-Id": self.device_id,
            "Device-Model": "PC",
            "Device-Type": "windows",
            "Language": "en",
            "Network-Type": "unknown",
            "SDK-Type": "h5",
            "SDK-Version": "0.0.0",
            "Anonymous-Uid": self.anonymous_uid,
            "User-Language": "en",
            "Version": self.version,
            "Wgt-Version": "0.0.0",
            "Authorization": f"Bearer {self.token}",
            "Origin": "",
            "Referer": "",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
            ),
        }
        self.set_domain(domain.strip() or "s1.n8o9.com")

    @staticmethod
    def normalize_proxy_url(proxy_url: str) -> str:
        value = (proxy_url or "").strip()
        if not value:
            return ""
        parsed = urlparse(value)
        if not parsed.scheme:
            value = f"http://{value}"
        return value

    @staticmethod
    def is_proxy_error(ex: Exception) -> bool:
        if isinstance(ex, requests.exceptions.ProxyError):
            return True
        text = str(ex).lower()
        return "proxy" in text or "407" in text

    def _disable_proxy(self):
        self.session.proxies.clear()
        self.using_proxy = False

    @classmethod
    def _version_for_domain(cls, domain: str) -> str:
        domain = (domain or "").strip().lower()
        # Explicit check for Pakistan domains starting with p1.
        if domain.startswith("p1."):
            return "13.1.1"
        return cls.DOMAIN_VERSION_MAP.get(domain, "13.5.1")

    def set_domain(self, domain: str):
        clean = (domain or "").strip()
        if not clean:
            clean = "s1.n8o9.com"
        self.domain = clean
        self.version = self._version_for_domain(clean)
        self.base_url = f"https://{clean}"
        self.default_headers["Version"] = self.version
        self.default_headers["Origin"] = self.base_url
        self.default_headers["Referer"] = f"{self.base_url}/index.html"

    def _headers(self, path: str, data: dict) -> dict:
        request_time = str(int(time.time() * 1000))
        headers = self.default_headers.copy()
        headers["Authorization"] = f"Bearer {self.token or 'X'}"
        headers["Request-Time"] = request_time
        headers["X-Sign"] = Signer.sign(path, data, self.anonymous_uid, request_time)
        return headers

    def _request_post(self, path: str, data: dict) -> requests.Response:
        return self.session.post(
            f"{self.base_url}{path}",
            json=data,
            headers=self._headers(path, data),
            timeout=30,
        )

    def _mask_error(self, ex: Exception) -> str:
        import re
        error_str = str(ex)
        if "ProxyError" in error_str or "Remote end closed" in error_str:
            return "Proxy connection failed or timed out."
        error_str = re.sub(r'https?://[^\s<]+', '<hidden_server_url>', error_str)
        return error_str

    def _post(self, path: str, data: dict) -> dict:
        try:
            resp = self._request_post(path, data)
            resp.raise_for_status()
            try:
                return resp.json()
            except ValueError:
                return {"code": -1, "msg": f"Invalid JSON response (len {len(resp.text)}). Status: {resp.status_code}"}
        except requests.exceptions.RequestException as ex:
            if self.using_proxy and self.allow_proxy_fallback and self.is_proxy_error(ex):
                self._disable_proxy()
                try:
                    resp = self._request_post(path, data)
                    resp.raise_for_status()
                    try:
                        return resp.json()
                    except ValueError:
                        return {"code": -1, "msg": f"Invalid JSON response (fallback). Status: {resp.status_code}"}
                except requests.exceptions.RequestException as fallback_ex:
                    raise RuntimeError(self._mask_error(fallback_ex))
            raise RuntimeError(self._mask_error(ex))

    def login(self, email: str, password: str) -> dict:
        self.login_email = email
        self.login_password = password
        path = f"/api/v1/member/login?version={self.version}"
        data = {
            "email": email,
            "password": password,
            "cf_token": "",
            "cf_key": "0x4AAAAAABHJ0R4Z4KgSD0lS",
        }
        result = self._post(path, data)
        if result.get("code") == 200:
            token = str((result.get("data") or {}).get("token") or "").strip()
            if token:
                self.token = token
                self.default_headers["Authorization"] = f"Bearer {self.token}"
        return result

    def relogin(self) -> dict:
        if not self.login_email or not self.login_password:
            raise RuntimeError("Missing cached login credentials for re-login")
        return self.login(self.login_email, self.login_password)

    def apply_server(self) -> dict:
        path = f"/api/v1/wa-account/applyServer?version={self.version}"
        return self._post(path, {})

    def wa_login(self) -> dict:
        path = f"/api/v1/wa-account/login?version={self.version}"
        return self._post(path, {})

    def wa_qrcode(self, device_id: str, invite_code: str) -> dict:
        path = f"/api/v1/wa-account/qrcode?version={self.version}"
        data = {"device_id": device_id, "invite_code": invite_code}
        return self._post(path, data)

    def ping(self) -> dict:
        path = f"/api/v1/h5/version?version={self.version}&t={int(time.time() * 1000)}"
        return self._post(path, {})

    def _is_login_required_response(self, result: dict) -> bool:
        msg = str(result.get("msg") or "").strip().lower()
        return "please login" in msg or "login first" in msg

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass


if __name__ == "__main__":
    pass
