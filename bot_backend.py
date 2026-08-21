import asyncio
import logging
import time
from bot_requests import C88ZZClient, DostWaClient, generate_unique_mobile

logger = logging.getLogger(__name__)

COUNTRY_CODES = [
    # 1 digit
    "1", "7",
    # 2 digits
    "20", "27", "30", "31", "32", "33", "34", "36", "39", "40", "41", "43", "44", "45", "46", "47", "48", "49",
    "51", "52", "53", "54", "55", "56", "57", "58", "60", "61", "62", "63", "64", "65", "66", "81", "82", "84",
    "86", "90", "91", "92", "93", "94", "95", "98",
    # 3 digits
    "211", "212", "213", "216", "218", "220", "221", "222", "223", "224", "225", "226", "227", "228", "229",
    "230", "231", "232", "233", "234", "235", "236", "237", "238", "239", "240", "241", "242", "243", "244",
    "245", "246", "247", "248", "249", "250", "251", "252", "253", "254", "255", "256", "257", "258", "260",
    "261", "262", "263", "264", "265", "266", "267", "268", "269", "290", "291", "297", "298", "299", "350",
    "351", "352", "353", "354", "355", "356", "357", "358", "359", "370", "371", "372", "373", "374", "375",
    "376", "377", "378", "379", "380", "381", "382", "383", "385", "386", "387", "389", "420", "421", "423",
    "500", "501", "502", "503", "504", "505", "506", "507", "508", "509", "590", "591", "592", "593", "594",
    "595", "596", "597", "598", "599", "670", "672", "673", "674", "675", "676", "677", "678", "679", "680",
    "681", "682", "683", "685", "686", "687", "688", "689", "690", "691", "692", "850", "852", "853", "855",
    "856", "880", "886", "960", "961", "962", "963", "964", "965", "966", "967", "968", "970", "971", "972",
    "973", "974", "975", "976", "977", "992", "993", "994", "995", "996", "998"
]

def clean_and_normalize_phone(wa_phone: str, default_country_code: str = "92") -> tuple:
    """
    Cleans a phone number input and detects the country code.
    Returns (normalized_phone_with_cc, country_code).
    """
    cleaned = "".join(c for i, c in enumerate(wa_phone) if c.isdigit() or (c == '+' and i == 0))
    if not cleaned:
        return "", default_country_code
        
    has_plus = cleaned.startswith("+")
    digits = cleaned.lstrip("+")
    
    if has_plus:
        for code in sorted(COUNTRY_CODES, key=len, reverse=True):
            if digits.startswith(code):
                return digits, code
        return digits, default_country_code
        
    if digits.startswith(default_country_code) and len(digits) > 10:
        return digits, default_country_code

    if digits.startswith("0"):
        without_zero = digits[1:]
        return f"{default_country_code}{without_zero}", default_country_code
        
    if default_country_code == "92" and len(digits) == 10 and digits.startswith("3"):
        return f"92{digits}", "92"
        
    for code in sorted(COUNTRY_CODES, key=len, reverse=True):
        if digits.startswith(code):
            if code == "34" and default_country_code == "92" and len(digits) == 10:
                continue
            return digits, code
            
    return f"{default_country_code}{digits}", default_country_code

async def create_account(site_id: str, invite_code: str, proxy: str = None, password: str = "53561106Tojo"):
    """Creates a new registered account on c88zz.com."""
    def _sync_create():
        last_error = ""
        # Try up to 3 times to register an account with a unique number
        for attempt in range(3):
            try:
                mobile = generate_unique_mobile(site_id)
                client = C88ZZClient(proxy_url=proxy)
                resp = client.register(mobile, password, invite_code)
                
                if resp.get("code") == 200:
                    logger.info(f"Registered new mobile successfully for {site_id}: {mobile}")
                    client.close()
                    return mobile
                
                # Check if it was already registered (though highly unlikely with random generation)
                msg = str(resp.get("message") or "").lower()
                if "registered" in msg:
                    continue
                
                raise RuntimeError(resp.get("message") or "Unknown registration error")
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Registration attempt {attempt + 1} failed: {e}")
                time.sleep(2)
        raise RuntimeError(f"Account creation failed after retries: {last_error}")

    return await asyncio.to_thread(_sync_create)

async def start_whatsapp_link(site_id: str, username: str, password: str, proxy: str = None, wa_phone: str = ""):
    """Logs into the c88zz.com account, requests the WhatsApp link, and fetches the pairing code."""
    # Robustly clean and normalize the WhatsApp number using site specific country code prefix
    cc_map = {"india": "91", "pakistan": "92", "south_africa": "27", "nigeria": "234"}
    default_cc = cc_map.get(site_id.lower(), "92")
    normalized_phone, area_code = clean_and_normalize_phone(wa_phone, default_country_code=default_cc)
    
    def _sync_start():
        client = C88ZZClient(proxy_url=proxy)
        try:
            # Login
            login_res = client.login(username, password)
            if login_res.get("code") != 200:
                raise RuntimeError(f"Login failed: {login_res.get('message')}")
            
            # Start linking
            start_res = client.whatsapp_start(normalized_phone, area_code)
            if start_res.get("code") != 200:
                raise RuntimeError(f"WhatsApp start link failed: {start_res.get('message')}")
            
            session_id = start_res.get("data", {}).get("session_id")
            if not session_id:
                raise RuntimeError("No session_id returned from whatsapp_start")
                
            # Poll for the pairing code up to 5 times (total 10 seconds)
            # This handles server load where pairing codes take some time to generate
            pair_code = None
            for attempt in range(5):
                time.sleep(2)
                code_res = client.whatsapp_code(session_id, normalized_phone)
                if code_res.get("code") == 200:
                    pair_code = code_res.get("data", {}).get("pair_code") or code_res.get("data", {}).get("code")
                    if pair_code:
                        break
                logger.warning(f"Pairing code not ready on attempt {attempt+1}, retrying...")
                
            if not pair_code:
                raise RuntimeError("No pairing code returned from whatsapp_code after retries")
                
            return client, session_id, pair_code
        except Exception as e:
            client.close()
            raise e

    return await asyncio.to_thread(_sync_start)

async def poll_wa_status(client: C88ZZClient, session_id: str):
    """Checks the linking status of the session."""
    def _sync_poll():
        try:
            # Check online status
            check_res = client.whatsapp_check(session_id)
            
            is_online = check_res.get("data", {}).get("is_online", False)
            
            # Return uniform dictionary format for tg_bot to consume
            if is_online:
                return {"code": 200, "data": {"login_status": 2, "wid": "Linked"}}
            else:
                return {"code": 200, "data": {"login_status": 1, "wid": ""}}
        except Exception as e:
            return {"code": 500, "msg": str(e)}

    return await asyncio.to_thread(_sync_poll)


async def get_main_account_balance(username: str, password: str = "53561106@Roni", proxy: str = None):
    """Logs in and retrieves the balance from user info."""
    def _sync_balance():
        client = C88ZZClient(proxy_url=proxy)
        try:
            login_res = client.login(username, password)
            if login_res.get("code") != 200:
                raise RuntimeError(f"Login failed: {login_res.get('message')}")
            info_res = client.user_info()
            if info_res.get("code") != 200:
                raise RuntimeError(f"Failed to fetch user info: {info_res.get('message')}")
            return info_res.get("data", {}).get("balance", 0)
        finally:
            client.close()
    return await asyncio.to_thread(_sync_balance)


async def keep_alive_account(username: str, password: str = "53561106@Roni", proxy: str = None) -> bool:
    """Logs in to the account and requests profile info to keep the session alive."""
    def _sync_keep_alive():
        client = C88ZZClient(proxy_url=proxy)
        try:
            login_res = client.login(username, password)
            if login_res.get("code") != 200:
                logger.warning(f"Keep-alive login failed for {username}: {login_res.get('message')}")
                return False
            info_res = client.user_info()
            if info_res.get("code") == 200:
                logger.info(f"Keep-alive successful for {username}. Balance: {info_res.get('data', {}).get('balance', 0)}")
                return True
            else:
                logger.warning(f"Keep-alive user info failed for {username}: {info_res.get('message')}")
                return False
        except Exception as e:
            logger.error(f"Keep-alive exception for {username}: {e}")
            return False
        finally:
            client.close()
    return await asyncio.to_thread(_sync_keep_alive)


# ═══════════════════════════════════════════════════════════════════════════════
# DostWa (Pakistan 2) Backend Functions
# ═══════════════════════════════════════════════════════════════════════════════

async def dostwa_create_account(invite_code: str = "K7MBKZ", proxy: str = None,
                                password: str = "53561106@Roni"):
    """Creates a new registered account on dostwa.com (Pakistan 2)."""
    def _sync_create():
        last_error = ""
        for attempt in range(3):
            try:
                mobile = generate_unique_mobile("pakistan")
                client = DostWaClient(proxy_url=proxy)
                resp = client.register(mobile, password, invite_code)

                if resp.get("code") == 200:
                    logger.info(f"DostWa registered new mobile successfully: {mobile}")
                    client.close()
                    return mobile

                msg = str(resp.get("msg") or resp.get("message") or "").lower()
                if "registered" in msg or "exist" in msg:
                    continue

                raise RuntimeError(resp.get("msg") or resp.get("message") or "Unknown registration error")
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Pakistan 2 registration attempt {attempt + 1} failed: {e}")
                time.sleep(2)
        raise RuntimeError(f"Pakistan 2 account creation failed after retries: {last_error}")

    return await asyncio.to_thread(_sync_create)


async def dostwa_start_whatsapp_link(username: str, password: str, proxy: str = None,
                                     wa_phone: str = ""):
    """Logs into DostWa, requests the WhatsApp pairing code."""
    cc_map = {"pakistan": "92"}
    default_cc = "92"
    normalized_phone, area_code = clean_and_normalize_phone(wa_phone, default_country_code=default_cc)

    # For DostWa, the phone number sent to getPairingCode should NOT have the country code prefix
    # (based on HAR: phoneNumber = "3477152690", countryCode = "92")
    phone_without_cc = normalized_phone
    if phone_without_cc.startswith(area_code):
        phone_without_cc = phone_without_cc[len(area_code):]

    def _sync_start():
        client = DostWaClient(proxy_url=proxy)
        try:
            # Login
            login_res = client.login(username, password)
            if login_res.get("code") != 200:
                raise RuntimeError(f"Pakistan 2 login failed: {login_res.get('msg')}")

            # Request pairing code
            pair_res = client.get_pairing_code(phone_without_cc, area_code)
            if pair_res.get("code") != 200:
                raise RuntimeError(f"Pakistan 2 getPairingCode failed: {pair_res.get('msg')}")

            pair_code = pair_res.get("data", {}).get("pairingCode")
            if not pair_code:
                raise RuntimeError("No pairing code returned from Pakistan 2 server")

            # Use phone_without_cc as session_id equivalent for status polling
            return client, phone_without_cc, pair_code
        except Exception as e:
            client.close()
            raise e

    return await asyncio.to_thread(_sync_start)


async def dostwa_poll_wa_status(client: DostWaClient, phone_number: str):
    """Checks the WhatsApp binding status on DostWa."""
    def _sync_poll():
        try:
            status_res = client.get_account_status(phone_number)
            status = status_res.get("data", {}).get("status", "")

            # DostWa returns status: "Bindok" when linked, "Binding" while pending
            if status.lower() in ("bindok", "online", "connected"):
                return {"code": 200, "data": {"login_status": 2, "wid": "Linked"}}
            else:
                return {"code": 200, "data": {"login_status": 1, "wid": ""}}
        except Exception as e:
            return {"code": 500, "msg": str(e)}

    return await asyncio.to_thread(_sync_poll)


async def dostwa_get_balance(username: str, password: str = "53561106@Roni", proxy: str = None):
    """Logs in and retrieves balance/points from DostWa user info."""
    def _sync_balance():
        client = DostWaClient(proxy_url=proxy)
        try:
            login_res = client.login(username, password)
            if login_res.get("code") != 200:
                raise RuntimeError(f"Pakistan 2 login failed: {login_res.get('msg')}")
            info_res = client.user_info()
            if info_res.get("code") != 200:
                raise RuntimeError(f"Failed to fetch Pakistan 2 user info: {info_res.get('msg')}")
            user_data = info_res.get("data", {}).get("user", {})
            return user_data.get("points", 0)
        finally:
            client.close()
    return await asyncio.to_thread(_sync_balance)


async def dostwa_keep_alive(username: str, password: str = "53561106@Roni", proxy: str = None) -> bool:
    """Logs in to DostWa and requests profile info to keep the session alive."""
    def _sync_keep_alive():
        client = DostWaClient(proxy_url=proxy)
        try:
            login_res = client.login(username, password)
            if login_res.get("code") != 200:
                logger.warning(f"DostWa keep-alive login failed for {username}: {login_res.get('msg')}")
                return False
            info_res = client.user_info()
            if info_res.get("code") == 200:
                points = info_res.get("data", {}).get("user", {}).get("points", 0)
                logger.info(f"DostWa keep-alive successful for {username}. Points: {points}")
                return True
            else:
                logger.warning(f"DostWa keep-alive user info failed for {username}: {info_res.get('msg')}")
                return False
        except Exception as e:
            logger.error(f"DostWa keep-alive exception for {username}: {e}")
            return False
        finally:
            client.close()
    return await asyncio.to_thread(_sync_keep_alive)
