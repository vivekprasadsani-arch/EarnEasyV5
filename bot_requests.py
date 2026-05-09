import asyncio
import hashlib
import json
import os
import random
import re
import string
import sys
import time
import imaplib
import email
from email.header import decode_header
from html import unescape
import logging
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
try:
    from curl_cffi import requests as curl_requests
except ImportError:
    curl_requests = None

logger = logging.getLogger(__name__)

try:
    from botasaurus.browser import browser, Driver
    from botasaurus.request import request as botasaurus_request
except Exception as e:
    # Botasaurus can fail during import if it tries to download binaries and hits GitHub rate limits
    logger.warning(f"Could not import botasaurus (likely GitHub rate limit): {e}")
    browser = None
    botasaurus_request = None


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
                # worker threads don't have their own event loop, so we find the main one
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


def _load_camoufox_bypasser():
    global _CF_BYPASSER_CLASS
    if _CF_BYPASSER_CLASS is not None:
        return _CF_BYPASSER_CLASS

    # Disable Camoufox update checks BEFORE importing to prevent GitHub API rate limit crashes
    os.environ["CAMOUFOX_ALLOW_UPDATE"] = "0"

    repo_dir = Path(__file__).resolve().parent / "CloudflareBypassForScraping-main"
    if not repo_dir.exists():
        raise RuntimeError("CloudflareBypassForScraping-main repo was not found")

    repo_path = str(repo_dir)
    if repo_path not in sys.path:
        sys.path.insert(0, repo_path)

    from cf_bypasser.core.bypasser import CamoufoxBypasser
    
    # Deeply suppress any update checks in camoufox
    try:
        import camoufox.utils as cf_utils
        cf_utils.check_for_updates = lambda *args, **kwargs: None
    except:
        pass

    _CF_BYPASSER_CLASS = CamoufoxBypasser
    return _CF_BYPASSER_CLASS


class LegacyEmailnatorClient:
    def __init__(self, proxy_url=None, allow_proxy_fallback=True):
        self.impersonates = ["chrome110", "chrome101", "safari15_5"]
        self.current_impersonate_idx = 0
        self.allow_proxy_fallback = allow_proxy_fallback
        self.proxy_url = normalize_proxy_url(proxy_url)
        self.using_proxy = bool(self.proxy_url)
        self._init_session_obj(self.proxy_url if self.using_proxy else None)
        self.init_session()

    def _init_session_obj(self, proxy_url=None):
        imp = self.impersonates[self.current_impersonate_idx % len(self.impersonates)]
        
        if curl_requests:
            try:
                # Force http_version=1 (HTTP/1.1) to avoid 'Proxy CONNECT aborted' errors
                self.session = curl_requests.Session(impersonate=imp, http_version=1)
            except Exception as e:
                logger.warning(f"curl_cffi session init failed: {e}. Falling back to requests.")
                self.session = requests.Session()
                self.session.trust_env = False
        else:
            self.session = requests.Session()
            self.session.trust_env = False
        
        if proxy_url:
            p = normalize_proxy_url(proxy_url)
            self.session.proxies.update({"http": p, "https": p})
            
        if not hasattr(self.session, "impersonate"):
            self.session.headers.update({
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest",
            })
        else:
            # When using curl_cffi impersonate, we only add minimal required headers
            self.session.headers.update({"X-Requested-With": "XMLHttpRequest"})

    def is_proxy_error(self, ex: Exception) -> bool:
        """Check if the error is related to proxy connectivity."""
        if isinstance(ex, requests.exceptions.ProxyError):
            return True
        text = str(ex).lower()
        return any(err in text for err in ("proxy", "407", "remote end closed", "tunnel connection failed", "curl error 56", "curl error 35"))

    def _disable_proxy(self):
        self.session.proxies.clear()
        self.using_proxy = False

    def _request(self, method, url, **kwargs):
        last_ex = None
        for attempt in range(5):
            try:
                resp = self.session.request(method, url, **kwargs)
                if resp.status_code == 403 and "just a moment" in resp.text.lower():
                    logger.warning(f"Cloudflare detected on {url}. Changing impersonation...")
                    self.current_impersonate_idx += 1
                    self._init_session_obj(self.proxy_url if self.using_proxy else None)
                    time.sleep(5)
                    continue
                if resp.status_code == 429 and attempt < 4:
                    time.sleep(10 + attempt * 10)
                    continue
                resp.raise_for_status()
                return resp
            except Exception as ex:
                last_ex = ex
                err_str = str(ex).lower()
                # Handle curl_cffi proxy errors
                if any(e in err_str for e in ("curl error 56", "proxy connect aborted", "curl error 35")):
                    logger.warning(f"Proxy error with curl_cffi: {ex}. Attempting protocol switch...")
                    self.current_impersonate_idx += 1
                    self._init_session_obj(self.proxy_url if self.using_proxy else None)
                    
                    if attempt > 2: # After a few tries, fall back to standard requests
                        logger.warning("Switching to standard requests fallback...")
                        old_session = self.session
                        self.session = requests.Session()
                        self.session.trust_env = False
                        if self.using_proxy:
                            p = normalize_proxy_url(self.proxy_url)
                            self.session.proxies.update({"http": p, "https": p})
                        self.session.headers.update({
                            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                            "Accept": "application/json, text/plain, */*",
                            "X-Requested-With": "XMLHttpRequest",
                        })
                        try:
                            res = self.session.request(method, url, **kwargs)
                            res.raise_for_status()
                            return res
                        except Exception:
                            self.session = old_session # Restore if fallback also fails
                            pass

                if self.using_proxy and self.allow_proxy_fallback and self.is_proxy_error(ex):
                    self._disable_proxy()
                    return self._request(method, url, **kwargs)
                if attempt < 4:
                    time.sleep(5)
                    continue
                raise
        raise last_ex or RuntimeError("Max retries exceeded for email client")

    def init_session(self):
        # 1. Load Homepage to get initial cookies
        self._request("GET", "https://www.emailnator.com/", timeout=25)
        self._update_xsrf_token()
        # 2. Hit mailbox endpoint to initialize session for inboxes
        self.session.headers.update({"Referer": "https://www.emailnator.com/"})
        self._request("GET", "https://www.emailnator.com/mailbox/", timeout=25)
        self._update_xsrf_token()

    def _update_xsrf_token(self):
        xsrf_token = self.session.cookies.get("XSRF-TOKEN")
        if xsrf_token:
            # Use raw cookie value for X-XSRF-TOKEN header
            self.session.headers.update({"X-XSRF-TOKEN": requests.utils.unquote(xsrf_token)})

    def generate_email(self):
        # We'll try dotGmail specifically as it's more stable for DeepEarn
        self.session.headers.update({"Referer": "https://www.emailnator.com/"})
        resp = self._request(
            "POST",
            "https://www.emailnator.com/generate-email", 
            json={"email": ["dotGmail"]}, 
            timeout=25
        )
        email = (resp.json().get("email") or [None])[0]
        if not email:
            raise RuntimeError("Emailnator returned empty email list")
        
        # After generating, we 'visit' the mailbox to activate it
        self.session.headers.update({"Referer": "https://www.emailnator.com/"})
        self._request("GET", "https://www.emailnator.com/mailbox/", timeout=25)
        self._update_xsrf_token()
        
        return email

    def get_messages(self, email):
        # Set referer to mailbox as seen in HAR
        self.session.headers.update({
            "Referer": "https://www.emailnator.com/mailbox/",
            "Origin": "https://www.emailnator.com"
        })
        self._update_xsrf_token()
        resp = self._request(
            "POST",
            "https://www.emailnator.com/message-list", json={"email": email}, timeout=25
        )
        return resp.json().get("messageData", [])

    def get_message_content(self, email, message_id):
        # Set referer to mailbox as seen in HAR
        self.session.headers.update({
            "Referer": "https://www.emailnator.com/mailbox/",
            "Origin": "https://www.emailnator.com"
        })
        self._update_xsrf_token()
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


class ManualEmailnatorClient(LegacyEmailnatorClient):
    """Client that uses manually captured cookies from a JSON file."""
    COOKIE_FILE = "manual_emailnator_cookies.json"

    def __init__(self, proxy_url=None, allow_proxy_fallback=True):
        self.impersonates = ["chrome110", "chrome101", "safari15_5"]
        self.current_impersonate_idx = 0
        self.allow_proxy_fallback = allow_proxy_fallback
        self.proxy_url = normalize_proxy_url(proxy_url)
        self.using_proxy = bool(self.proxy_url)
        
        # We manually set up standard requests session for manual cookies
        self.session = requests.Session()
        self.session.trust_env = False
        if self.proxy_url:
            p = normalize_proxy_url(self.proxy_url)
            self.session.proxies.update({"http": p, "https": p})
        
        self.init_session()

    def init_session(self):
        try:
            if not os.path.exists(self.COOKIE_FILE):
                raise FileNotFoundError(f"Manual cookie file {self.COOKIE_FILE} not found")
                
            with open(self.COOKIE_FILE, "r") as f:
                data = json.load(f)
            
            # 1. Handle User-Agent
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            if isinstance(data, dict):
                user_agent = data.get("user_agent", user_agent)
            
            self.session.headers.update({
                "User-Agent": user_agent,
                "X-Requested-With": "XMLHttpRequest"
            })
            
            # 2. Handle Cookies (Dict or List format)
            cookie_dict = {}
            if isinstance(data, list):
                # Standard browser export format: [{"name": "...", "value": "..."}, ...]
                for c in data:
                    if isinstance(c, dict) and 'name' in c and 'value' in c:
                        cookie_dict[c['name']] = c['value']
            elif isinstance(data, dict):
                cookie_dict = data.get("cookies", data)

            for name, value in cookie_dict.items():
                self.session.cookies.set(name, value, domain="www.emailnator.com")
            
            # Verify if cookies work by hitting mailbox
            try:
                self._update_xsrf_token()
                self.session.headers.update({"Referer": "https://www.emailnator.com/"})
                resp = self.session.get("https://www.emailnator.com/mailbox/", timeout=25, proxies=self.session.proxies)
                if resp.status_code == 403:
                    raise RuntimeError("Cloudflare 403 on manual cookies")
                resp.raise_for_status()
                self._update_xsrf_token()
            except Exception as e:
                logger.warning(f"Verification check failed: {e}")
                raise
            
            logger.info("Manual cookies loaded and verified successfully")
        except Exception as e:
            logger.warning(f"Failed to load manual cookies: {e}")
            if "403" in str(e) or "forbidden" in str(e).lower():
                notify_admin("âš ï¸ Manual Emailnator cookies have expired or been blocked. Please capture NEW cookies USING THE PAKISTAN PROXY and upload using /updatecookies")
            raise


class BypassedEmailnatorClient(LegacyEmailnatorClient):
    EMAILNATOR_URL = "https://www.emailnator.com/"

    def init_session(self):
        self._bootstrap_via_cloudflare_bypass()
        self._update_xsrf_token()
        # Ensure session is active for mailboxes
        self.session.headers.update({"Referer": "https://www.emailnator.com/"})
        self._request("GET", "https://www.emailnator.com/mailbox/", timeout=25)
        self._update_xsrf_token()

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
                # Use higher retries for browser bypass on Render
                bypasser = bypasser_cls(max_retries=5, log=True, cache_file=cache_file)
                data = asyncio.run(
                    bypasser.get_or_generate_html(
                        self.EMAILNATOR_URL,
                        proxy=proxy_candidate,
                        bypass_cache=False,
                    )
                )
                if not data or not data.get("cookies"):
                    raise RuntimeError("Cloudflare bypass returned no cookies")

                self.session.headers["User-Agent"] = data.get("user_agent") or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
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


class GmailIMAPClient:
    """Client that uses personal Gmail via IMAP for 100% success and Dot-Trick support."""
    
    # HARDCODED PRIMARY GMAIL - You can move this to config.py if needed
    GMAIL_USER = "gulzarprasaddhar@gmail.com"
    GMAIL_PASS = "aruvfhldkvynituj"

    def __init__(self, proxy_url=None, allow_proxy_fallback=True):
        self.proxy_url = proxy_url
        self.allow_proxy_fallback = allow_proxy_fallback
        self.active_alias = None

    def generate_email(self):
        """Generates a unique Dot-Alias that hasn't been used yet."""
        name, domain = self.GMAIL_USER.split('@')
        n = len(name)
        
        # Logic to generate a random dot variation
        dots = [random.choice([True, False]) for _ in range(n - 1)]
        alias_name = name[0]
        for i, dot in enumerate(dots):
            if dot:
                alias_name += '.'
            alias_name += name[i+1]
            
        self.active_alias = alias_name + '@' + domain
        return self.active_alias

    def get_messages(self, email_addr):
        """Polls Inbox and Spam for messages sent to the specific alias."""
        folders = ["INBOX", '"[Gmail]/Spam"']
        messages_found = []
        
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(self.GMAIL_USER, self.GMAIL_PASS)
            
            for folder in folders:
                try:
                    status, _ = mail.select(folder, readonly=True)
                    if status != 'OK': continue
                    
                    status, messages = mail.search(None, 'ALL')
                    mail_ids = messages[0].split()
                    
                    # Check latest 15 messages
                    for m_id in reversed(mail_ids[-15:]):
                        status, data = mail.fetch(m_id, "(RFC822)")
                        for response_part in data:
                            if isinstance(response_part, tuple):
                                msg = email.message_from_bytes(response_part[1])
                                
                                # CRITICAL: Validate RECIPIENT to ensure isolation
                                to_header = str(msg.get("To", "")).lower()
                                if email_addr.lower() not in to_header:
                                    continue
                                
                                subject, encoding = decode_header(msg["Subject"])[0]
                                if isinstance(subject, bytes):
                                    subject = subject.decode(encoding or "utf-8", errors='ignore')
                                
                                # Format matching Emailnator output for compatibility
                                messages_found.append({
                                    "messageID": m_id.decode(),
                                    "subject": subject,
                                    "from": msg.get("From"),
                                    "to": to_header,
                                    "receivedAt": msg.get("Date")
                                })
                except:
                    continue
            
            mail.logout()
        except Exception as e:
            logger.error(f"Gmail IMAP Error: {e}")
            
        return messages_found

    def get_message_content(self, email_addr, message_id):
        """Retrieves raw content of a specific message via IMAP."""
        folders = ["INBOX", '"[Gmail]/Spam"']
        try:
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(self.GMAIL_USER, self.GMAIL_PASS)
            
            for folder in folders:
                try:
                    mail.select(folder, readonly=True)
                    status, data = mail.fetch(str(message_id).encode(), "(RFC822)")
                    for response_part in data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])
                            if msg.is_multipart():
                                for part in msg.walk():
                                    if part.get_content_type() == "text/html":
                                        return part.get_payload(decode=True).decode(errors='ignore')
                            else:
                                return msg.get_payload(decode=True).decode(errors='ignore')
                except:
                    continue
            mail.logout()
        except Exception as e:
            logger.error(f"Gmail Content Error: {e}")
        return ""

    def close(self):
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

    @staticmethod
    def _is_forbidden_error(ex: Exception) -> bool:
        if isinstance(ex, requests.exceptions.HTTPError):
            response = getattr(ex, "response", None)
            if response is not None and response.status_code in (401, 403):
                return True
        text = str(ex).lower()
        return any(err in text for err in ("401", "403", "forbidden", "unauthorized"))

    def _recovery_candidates(self):
        # We now prioritize Gmail as it is 100% reliable
        gmail_factory = lambda: GmailIMAPClient(
            proxy_url=self.proxy_url,
            allow_proxy_fallback=self.allow_proxy_fallback,
        )
        manual_factory = lambda: ManualEmailnatorClient(
            proxy_url=self.proxy_url,
            allow_proxy_fallback=self.allow_proxy_fallback,
        )
        legacy_factory = lambda: LegacyEmailnatorClient(
            proxy_url=self.proxy_url,
            allow_proxy_fallback=self.allow_proxy_fallback,
        )
        
        return [
            ("gmail_imap", gmail_factory),
            ("manual_emailnator", manual_factory),
            ("bypassed_emailnator", self._bypassed_emailnator_client),
            ("legacy_emailnator", legacy_factory)
        ]

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

        # 1. Try Gmail IMAP first - It is now the primary method as per user request
        email, gmail_error = self._attempt_generate(
            "gmail_imap",
            lambda: GmailIMAPClient(
                proxy_url=self.proxy_url,
                allow_proxy_fallback=self.allow_proxy_fallback,
            ),
        )
        if email:
            return email

        # 2. Try Manual Cookies
        if os.path.exists("manual_emailnator_cookies.json"):
            email, manual_error = self._attempt_generate(
                "manual_emailnator",
                lambda: ManualEmailnatorClient(
                    proxy_url=self.proxy_url,
                    allow_proxy_fallback=self.allow_proxy_fallback,
                ),
            )
            if email:
                return email

        # 3. Try Bypassed Emailnator (Camoufox)
        email, bypass_error = self._attempt_generate(
            "bypassed_emailnator",
            self._bypassed_emailnator_client,
        )
        if email:
            return email
        
        # 4. Try Legacy Emailnator (Direct Requests)
        email, legacy_error = self._attempt_generate(
            "legacy_emailnator",
            lambda: LegacyEmailnatorClient(
                proxy_url=self.proxy_url,
                allow_proxy_fallback=self.allow_proxy_fallback,
            ),
        )
        if email:
            return email

        raise RuntimeError(f"All email sources failed. Gmail: {gmail_error}; Bypass: {bypass_error}; Legacy: {legacy_error}")

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


class DeepEarnClient:
    def __init__(self, inviter_code="57146564", proxy_url=None, domain="s1.ug5d.com", allow_proxy_fallback=True):
        self.inviter_code = inviter_code
        self.domain = domain
        self.base_url = f"https://{domain}"
        self.currency = DOMAIN_CURRENCY_MAP.get((domain or "").strip().lower(), "India")
        self.proxy_url = normalize_proxy_url(proxy_url)
        self.using_proxy = bool(self.proxy_url)
        
        if curl_requests:
            # Use http_version=1 to avoid 'Proxy CONNECT aborted' errors
            self.session = curl_requests.Session(impersonate="chrome110", http_version=1)
        else:
            self.session = requests.Session()
            self.session.trust_env = False

        self.allow_proxy_fallback = allow_proxy_fallback
        if self.using_proxy:
            self.session.proxies.update({"http": self.proxy_url, "https": self.proxy_url})
        # Each client instance gets its own unique anon_uid — sessions are fully isolated
        import uuid as _uuid
        ts = int(time.time() * 1000)
        rand = _uuid.uuid4().hex
        self.anon_uid = f"{ts}{rand}"
        self.signer = DeepEarnSigner(anon_uid=self.anon_uid)
        self.version = "13.5.1"
        self.default_headers = {
            "Accept": "*/*",
            "Content-Type": "application/json;charset=UTF-8",
            "Device-Type": "android",
            "Device-Model": random.choice(["Pixel 6", "Pixel 7", "SM-S901B", "SM-G991B", "OnePlus 10", "Redmi Note 11", "Vivo V23"]),
            "Language": random.choice(["en", "en-US", "en-GB"]),
            "Anonymous-Uid": self.anon_uid,
            "User-Language": random.choice(["en", "en-US"]),
            "Network-Type": random.choice(["wifi", "4g", "5g"]),
            "Version": self.version,
            "User-Agent": random.choice([
                "Mozilla/5.0 (Linux; Android 12; Pixel 6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
                "Mozilla/5.0 (Linux; Android 13; SM-S901B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
                "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36",
                "Mozilla/5.0 (Linux; Android 11; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Mobile Safari/537.36",
                "Mozilla/5.0 (Linux; Android 12; Redmi Note 11) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Mobile Safari/537.36"
            ]),
            "X-Requested-With": "com.deepearn.app",
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
        last_error = None
        for attempt in range(3):
            try:
                if attempt > 0:
                    time.sleep(random.uniform(5, 15))
                resp = self._request_post(path, data)
                if resp.status_code in (403, 429) and attempt < 2:
                    logger.warning(f"Got {resp.status_code} on attempt {attempt+1}, retrying...")
                    continue
                resp.raise_for_status()
                try:
                    res_json = resp.json()
                    # Check for business-level rate limits
                    msg = str(res_json.get("msg") or "").lower()
                    if "frequent" in msg and attempt < 2:
                        logger.warning(f"Business rate limit hit: {msg}. Sleeping longer...")
                        time.sleep(20 + attempt * 10) # Increased sleep
                        continue
                    return res_json
                except ValueError:
                    preview = resp.text[:300].strip()
                    return {"code": -1, "msg": f"Non-JSON response (HTTP {resp.status_code}): {preview}"}
            except Exception as ex:
                last_error = ex
                if self.using_proxy and self.allow_proxy_fallback and self.is_proxy_error(ex):
                    self._disable_proxy()
                    # Re-try immediately once without proxy
                    return self._post(path, data)
                if attempt < 2:
                    time.sleep(random.uniform(5, 10))
                    continue
                raise last_error
        if last_error:
            raise last_error
        return {"code": -1, "msg": "Max retries exceeded"}

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
