import asyncio
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

def generate_pwd(length=8):
    return "".join(random.choices(string.digits, k=length))

def generate_unique_mobile():
    """Generate a unique Pakistan mobile number starting with 3 followed by 9 random digits."""
    return f"3{random.randint(100000000, 999999999)}"
