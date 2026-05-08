import asyncio
import io
import re
import time
from PIL import Image, ImageOps
import requests

from bot_requests import DeepEarnClient, EmailnatorClient, generate_pwd
from wa_link_gui import WaLinkClient

SITES = {
    "india": "s1.4e22.com",
    "pakistan": "p1.x7bb.com",
    "south_africa": "a1.8xy5.com",
    "nigeria": "n1.9uot.com"
}


def _close_quietly(client):
    if not client:
        return
    try:
        client.close()
    except Exception:
        pass

def generate_qr_image(url: str, client: WaLinkClient = None) -> bytes:
    try:
        resp = None
        # Retry up to 10 times to download the image using the proxy
        for attempt in range(10):
            try:
                if client:
                    resp = client.session.get(url, timeout=30)
                else:
                    resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                break # Success!
            except requests.exceptions.RequestException as e:
                if attempt < 9: # Wait and retry
                    time.sleep(2)
                else:
                    raise e # Exhausted max retries
                    
        if not resp:
            raise RuntimeError("Empty response received")
            
        img = Image.open(io.BytesIO(resp.content))
        
        # Ensure pure white background if transparency exists
        if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'RGBA':
                background.paste(img, mask=img.split()[3])
            else:
                background.paste(img, mask=img.convert('RGBA').split()[3])
            img = background
        else:
            img = img.convert('RGB')

        # Add a large white border
        img = ImageOps.expand(img, border=50, fill='white')

        out_io = io.BytesIO()
        img.save(out_io, format='PNG')
        return out_io.getvalue()
    except Exception as e:
        raise RuntimeError(f"Failed to generate QR image: {e}")

async def create_account(site_id: str, invite_code: str, proxy: str = None, password: str = "53561106Tojo"):
    domain = SITES.get(site_id)
    if not domain:
        raise ValueError(f"Unknown site: {site_id}")

    def _sync_create():
        last_error = ""
        current_step = "starting"
        for attempt in range(3):
            email_client = None
            earn_client = None
            try:
                current_step = "email generation"
                email_client = EmailnatorClient(proxy_url=proxy, allow_proxy_fallback=True)
                email = email_client.generate_email()
                if not email:
                    raise RuntimeError("Failed to generate email")

                current_step = "otp send"
                earn_client = DeepEarnClient(
                    inviter_code=invite_code,
                    proxy_url=proxy,
                    domain=domain,
                    allow_proxy_fallback=True,
                )
                otp_resp = earn_client.send_otp(email)
                if otp_resp.get("code") != 200:
                    raise RuntimeError(f"OTP send failed: {otp_resp.get('msg')}")

                otp = None
                current_step = "otp inbox polling"
                for _ in range(20):
                    time.sleep(1.5)
                    msgs = email_client.get_messages(email)
                    for m in msgs:
                        if "Verification code" in m.get("subject", "") or "registration" in m.get("subject", "").lower() or "code" in m.get("subject", "").lower():
                            message_id = m.get("messageID") or m.get("uuid")
                            if not message_id:
                                continue
                            content = email_client.get_message_content(email, message_id)
                            match = re.search(r">(\d{6})<", content) or re.search(r"color: red.*?(\d{6})", content) or re.search(r'\b\d{6}\b', content)
                            if match:
                                otp = match.group(1) if match.lastindex else match.group(0)
                                break
                    if otp:
                        break

                if not otp:
                    raise RuntimeError("Timeout waiting for OTP email")

                current_step = "registration"
                reg_resp = earn_client.register(email, password, otp)
                if reg_resp.get("code") != 200:
                    raise RuntimeError(f"Registration failed: {reg_resp.get('msg')}")
                
                return email
            except Exception as e:
                last_error = str(e)
                # Hide urls or proxy info
                last_error = re.sub(r'https?://[^\s<]+', '<hidden_url>', last_error)
                if "ProxyError" in last_error or "Remote end closed" in last_error:
                    last_error = f"{current_step}: Proxy connection dropped."
                elif "proxy" in last_error.lower() and "dropped" in last_error.lower():
                    last_error = f"{current_step}: Proxy connection dropped."
                else:
                    last_error = f"{current_step}: {last_error}"
                time.sleep(2)
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
                
                # Poll if the returned URL is still the loading image
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
                last_error = re.sub(r'https?://[^\s<]+', '<hidden_url>', last_error)
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
