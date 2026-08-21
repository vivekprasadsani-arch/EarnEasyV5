import asyncio
import base64
import hashlib
import json
import os
import random
import string
import time
import logging
from urllib.parse import urlparse

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    from Crypto.Cipher import AES as AES_Cipher, PKCS1_v1_5
    from Crypto.PublicKey import RSA
    from Crypto.Util.Padding import pad, unpad
except ImportError:
    from Cryptodome.Cipher import AES as AES_Cipher, PKCS1_v1_5
    from Cryptodome.PublicKey import RSA
    from Cryptodome.Util.Padding import pad, unpad

logger = logging.getLogger(__name__)

def normalize_proxy_url(proxy_url: str) -> str:
    value = (proxy_url or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if not parsed.scheme:
        value = f"http://{value}"
    return value

NOTIFICATION_CALLBACKS = []

def add_notification_callback(callback):
    """Add a callback function to be called when manual intervention is needed."""
    if callback not in NOTIFICATION_CALLBACKS:
        NOTIFICATION_CALLBACKS.append(callback)

def notify_admin(message):
    """Trigger all registered notification callbacks safely across threads."""
    for cb in NOTIFICATION_CALLBACKS:
        try:
            if asyncio.iscoroutinefunction(cb):
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.call_soon_threadsafe(asyncio.create_task, cb(message))
                    else:
                        logger.error(f"Cannot notify admin: Loop not running. Msg: {message}")
                except RuntimeError:
                    logger.error(f"Cannot notify admin: No loop in thread. Msg: {message}")
            else:
                cb(message)
        except Exception as e:
            logger.error(f"Error in notification callback: {e}")

class C88ZZClient:
    def __init__(self, proxy_url=None):
        self.base_url = "https://api.c88zz.com"
        self.api_key = "c1fc73a597fff2ab7eed621ba8cf8014"
        
        # Dynamic Device ID generation
        md5_hash = hashlib.md5(str(time.time()).encode()).hexdigest()[:11]
        self.device_id = f"device_{int(time.time() * 1000)}_{md5_hash}"
        
        self.proxy_url = normalize_proxy_url(proxy_url)
        self.session = requests.Session()
        self.session.verify = False
        self.session.trust_env = False
        
        if self.proxy_url:
            self.session.proxies.update({"http": self.proxy_url, "https": self.proxy_url})
            
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-FR,en;q=0.9,fr-FR;q=0.8,fr;q=0.7",
            "Content-Type": "application/json",
            "Origin": "https://c88zz.com",
            "Referer": "https://c88zz.com/",
            "x-api-key": self.api_key,
            "device-id": self.device_id,
            "device-type": "h5",
            "language": "en",
            "os": "h5"
        }
        self.session.headers.update(self.headers)

    def register(self, mobile, password, invite_code):
        url = f"{self.base_url}/api/v1/auth/register"
        payload = {
            "mobile": str(mobile),
            "password": str(password),
            "invite_code": str(invite_code),
            "channel_invite_code": "",
            "pixel_id": ""
        }
        logger.info(f"Registering mobile {mobile} on C88ZZ...")
        resp = self.session.post(url, json=payload, timeout=25)
        resp.raise_for_status()
        resp_data = resp.json()
        
        if resp_data.get("code") == 200:
            token = resp_data.get("data", {}).get("access_token")
            if token:
                self.session.headers.update({"token": token})
                logger.info("Registration successful, token saved.")
        return resp_data

    def login(self, mobile, password):
        url = f"{self.base_url}/api/v1/auth/login"
        payload = {
            "mobile": str(mobile),
            "password": str(password)
        }
        logger.info(f"Logging in mobile {mobile} on C88ZZ...")
        resp = self.session.post(url, json=payload, timeout=25)
        resp.raise_for_status()
        resp_data = resp.json()
        
        if resp_data.get("code") == 200:
            token = resp_data.get("data", {}).get("access_token")
            if token:
                self.session.headers.update({"token": token})
                logger.info("Login successful, token saved.")
        return resp_data

    def whatsapp_start(self, phone_number, area_code):
        url = f"{self.base_url}/api/v1/ws/start"
        payload = {
            "mobile": str(phone_number),
            "mobile_area_code": str(area_code)
        }
        logger.info(f"Starting WhatsApp link for {phone_number} with area code {area_code}...")
        resp = self.session.post(url, json=payload, timeout=25)
        resp.raise_for_status()
        return resp.json()

    def whatsapp_code(self, session_id, phone_number):
        url = f"{self.base_url}/api/v1/ws/code"
        params = {
            "session_id": str(session_id),
            "mobile": str(phone_number)
        }
        logger.info(f"Fetching pairing code for {phone_number}...")
        resp = self.session.get(url, params=params, timeout=25)
        resp.raise_for_status()
        return resp.json()

    def whatsapp_check(self, session_id):
        url = f"{self.base_url}/api/v1/ws/check"
        params = {"session_id": str(session_id)}
        resp = self.session.get(url, params=params, timeout=25)
        resp.raise_for_status()
        return resp.json()

    def whatsapp_status(self, session_id):
        url = f"{self.base_url}/api/v1/ws/status"
        params = {"session_id": str(session_id)}
        resp = self.session.get(url, params=params, timeout=25)
        resp.raise_for_status()
        return resp.json()

    def user_info(self):
        url = f"{self.base_url}/api/v1/user/info"
        resp = self.session.get(url, timeout=25)
        resp.raise_for_status()
        return resp.json()

    def user_invite_info(self):
        url = f"{self.base_url}/api/v1/user/inviteInfo"
        resp = self.session.get(url, timeout=25)
        resp.raise_for_status()
        return resp.json()

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass


class DostWaClient:
    """
    API client for dostwa.com (Pakistan 2 site).
    
    Uses AES-256-ECB + RSA hybrid encryption matching the JS frontend:
    - Request: AES-ECB encrypt payload with random 32-byte key, RSA-encrypt the
      base64-encoded AES key, send via 'encrypt-key' header.
    - Response: RSA-decrypt the 'encrypt-key' header to recover AES key,
      then AES-ECB decrypt the response body.
    """
    
    # RSA public key used to encrypt the AES key in requests (from JS: nte)
    RSA_PUBLIC_KEY_B64 = (
        "MFwwDQYJKoZIhvcNAQEBBQADSwAwSAJBAKoR8mX0rGKLqzcWmOzbfj64K8ZIgOdH"
        "nzkXSOVOZbFu/TJhZ7rFAN+eaGkl3C4buccQd/EjEsj9ir7ijT7h96MCAwEAAQ=="
    )
    # RSA private key used to decrypt the AES key in responses (from JS: rte)
    RSA_PRIVATE_KEY_B64 = (
        "MIIBVAIBADANBgkqhkiG9w0BAQEFAASCAT4wggE6AgEAAkEAmc3CuPiGL/LcIIm7"
        "zryCEIbl1SPzBkr75E2VMtxegyZ1lYRD+7TZGAPkvIsBcaMs6Nsy0L78n2qh+lIZ"
        "MpLH8wIDAQABAkEAk82Mhz0tlv6IVCyIcw/s3f0E+WLmtPFyR9/WtV3Y5aaejUkU"
        "60JpX4m5xNR2VaqOLTZAYjW8Wy0aXr3zYIhhQQIhAMfqR9oFdYw1J9SsNc+Crhu"
        "gAvKTi0+BF6VoL6psWhvbAiEAxPPNTmrkmrXwdm/pQQu3UOQmc2vCZ5tiKpW10Cg"
        "Ji8kCIFGkL6utxw93Ncj4exE/gPLvKcT+1Emnoox+O9kRXss5AiAMtYLJDaLEzPr"
        "AWcZeeSgSIzbL+ecokmFKSDDcRske6QIgSMkHedwND1olF8vlKsJUGK3BcdtM8w4X"
        "q7BpSBwsloE="
    )
    CLIENT_ID = "209ef407810e3856f40870a9f0e769d7"
    TENANT_ID = "000000"

    def __init__(self, proxy_url=None):
        self.base_url = "https://api.dostwa.com/prod-api"
        self.proxy_url = normalize_proxy_url(proxy_url)
        self.session = requests.Session()
        self.session.verify = False
        self.session.trust_env = False
        self.token = None

        if self.proxy_url:
            self.session.proxies.update({"http": self.proxy_url, "https": self.proxy_url})

        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36 Edg/147.0.0.0",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json;charset=utf-8",
            "Origin": "https://dostwa.com",
            "Referer": "https://dostwa.com/",
            "clientid": self.CLIENT_ID,
            "Content-Language": "en_US",
        })

    # ── Encryption helpers ──────────────────────────────────────────────

    @staticmethod
    def _generate_aes_key_bytes():
        """Generate random 32-char alphanumeric string as AES key bytes (JS: Yee → Xee)."""
        chars = string.ascii_letters + string.digits
        key_str = ''.join(random.choice(chars) for _ in range(32))
        return key_str.encode('utf-8')

    @staticmethod
    def _aes_ecb_encrypt(plaintext: str, key_bytes: bytes) -> str:
        """AES-256-ECB encrypt with PKCS7 padding, return base64 (JS: e_)."""
        cipher = AES_Cipher.new(key_bytes, AES_Cipher.MODE_ECB)
        padded = pad(plaintext.encode('utf-8'), AES_Cipher.block_size)
        return base64.b64encode(cipher.encrypt(padded)).decode('utf-8')

    @staticmethod
    def _aes_ecb_decrypt(ciphertext_b64: str, key_bytes: bytes) -> str:
        """AES-256-ECB decrypt with PKCS7 unpadding (JS: Jee)."""
        cipher = AES_Cipher.new(key_bytes, AES_Cipher.MODE_ECB)
        decrypted = cipher.decrypt(base64.b64decode(ciphertext_b64))
        return unpad(decrypted, AES_Cipher.block_size).decode('utf-8')

    @classmethod
    def _rsa_encrypt(cls, plaintext: str) -> str:
        """RSA-encrypt with the public key, return base64 (JS: ote)."""
        der = base64.b64decode(cls.RSA_PUBLIC_KEY_B64)
        key = RSA.import_key(der)
        cipher = PKCS1_v1_5.new(key)
        return base64.b64encode(cipher.encrypt(plaintext.encode('utf-8'))).decode('utf-8')

    @classmethod
    def _rsa_decrypt(cls, ciphertext_b64: str) -> str:
        """RSA-decrypt with the private key (JS: ate)."""
        der = base64.b64decode(cls.RSA_PRIVATE_KEY_B64)
        key = RSA.import_key(der)
        cipher = PKCS1_v1_5.new(key)
        return cipher.decrypt(base64.b64decode(ciphertext_b64), sentinel=None).decode('utf-8')

    def _encrypted_post(self, url: str, data: dict, use_token: bool = True) -> dict:
        """
        POST with the DostWa hybrid encryption scheme.
        Matches the JS request interceptor for isEncrypt:true requests.
        """
        # Generate AES key
        aes_key = self._generate_aes_key_bytes()
        aes_key_b64 = base64.b64encode(aes_key).decode('utf-8')

        # Build headers
        headers = {
            "encrypt-key": self._rsa_encrypt(aes_key_b64),
        }
        if use_token and self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        # Encrypt body
        body = self._aes_ecb_encrypt(json.dumps(data, separators=(',', ':')), aes_key)

        resp = self.session.post(url, data=body, headers=headers, timeout=25)
        resp.raise_for_status()

        # Decrypt response if encrypt-key header is present
        resp_encrypt_key = resp.headers.get("encrypt-key")
        if resp_encrypt_key:
            resp_aes_key_b64 = self._rsa_decrypt(resp_encrypt_key)
            resp_aes_key = base64.b64decode(resp_aes_key_b64)
            decrypted_body = self._aes_ecb_decrypt(resp.text, resp_aes_key)
            return json.loads(decrypted_body)
        else:
            return resp.json()

    def _plain_post(self, url: str, data: dict, use_token: bool = True) -> dict:
        """POST without encryption (for endpoints that don't use isEncrypt)."""
        headers = {}
        if use_token and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        resp = self.session.post(url, json=data, headers=headers, timeout=25)
        resp.raise_for_status()
        return resp.json()

    def _plain_get(self, url: str, params: dict = None, use_token: bool = True) -> dict:
        """GET without encryption."""
        headers = {}
        if use_token and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        resp = self.session.get(url, params=params, headers=headers, timeout=25)
        resp.raise_for_status()
        return resp.json()

    # ── Public API methods ──────────────────────────────────────────────

    def register(self, mobile, password, invite_code="K7MBKZ"):
        """Register a new account (encrypted)."""
        url = f"{self.base_url}/auth/register"
        payload = {
            "tenantId": self.TENANT_ID,
            "username": f"92{mobile}" if not str(mobile).startswith("92") else str(mobile),
            "password": str(password),
            "confirmPassword": str(password),
            "code": "",
            "uuid": "",
            "inviteCode": str(invite_code),
            "userType": "web_user",
            "clientId": self.CLIENT_ID,
            "grantType": "password",
        }
        logger.info(f"Registering mobile {mobile} on DostWa...")
        resp_data = self._encrypted_post(url, payload, use_token=False)

        if resp_data.get("code") == 200:
            logger.info(f"DostWa registration successful for {mobile}")
        return resp_data

    def login(self, mobile, password):
        """Login to an existing account (encrypted)."""
        url = f"{self.base_url}/auth/login"
        username = f"92{mobile}" if not str(mobile).startswith("92") else str(mobile)
        payload = {
            "tenantId": self.TENANT_ID,
            "username": username,
            "password": str(password),
            "rememberMe": False,
            "code": "",
            "uuid": "",
            "clientId": self.CLIENT_ID,
            "grantType": "password",
        }
        logger.info(f"Logging in mobile {mobile} on DostWa...")
        resp_data = self._encrypted_post(url, payload, use_token=False)

        if resp_data.get("code") == 200:
            token = resp_data.get("data", {}).get("access_token")
            if token:
                self.token = token
                logger.info("DostWa login successful, token saved.")
        return resp_data

    def get_pairing_code(self, phone_number, country_code="92"):
        """Request a WhatsApp pairing code (plain POST, requires auth)."""
        url = f"{self.base_url}/bulk/account/getPairingCode"
        payload = {
            "countryCode": str(country_code),
            "phoneNumber": str(phone_number),
        }
        logger.info(f"Requesting pairing code for {phone_number} on DostWa...")
        return self._plain_post(url, payload)

    def get_account_status(self, phone_number):
        """Check the WhatsApp account binding status (plain POST, requires auth)."""
        url = f"{self.base_url}/bulk/account/getAccountStatus"
        payload = {"phoneNumber": str(phone_number)}
        return self._plain_post(url, payload)

    def user_info(self):
        """Get user profile info (plain GET, requires auth)."""
        url = f"{self.base_url}/system/user/getInfo"
        return self._plain_get(url)

    def captcha_code(self):
        """Get captcha info for registration (plain GET, no auth)."""
        url = f"{self.base_url}/auth/code"
        return self._plain_get(url, use_token=False)

    def close(self):
        try:
            self.session.close()
        except Exception:
            pass

def generate_pwd(length=8):
    return "".join(random.choices(string.digits, k=length))

def generate_unique_mobile(site_id="pakistan"):
    site = (site_id or "pakistan").lower()
    if site == "pakistan":
        return f"3{random.randint(100000000, 999999999)}"
    elif site == "india":
        return f"{random.choice([7, 8, 9])}{random.randint(100000000, 999999999)}"
    elif site == "south_africa":
        return f"{random.choice([6, 7, 8])}{random.randint(10000000, 99999999)}"
    elif site == "nigeria":
        return f"{random.choice([7, 8, 9])}{random.randint(100000000, 999999999)}"
    else:
        return f"3{random.randint(100000000, 999999999)}"
