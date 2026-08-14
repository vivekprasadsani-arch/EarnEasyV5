import asyncio
import logging
import time
from bot_requests import C88ZZClient, generate_unique_mobile

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

def extract_area_code(phone_number):
    cleaned = "".join(c for c in phone_number if c.isdigit())
    for code in sorted(COUNTRY_CODES, key=len, reverse=True):
        if cleaned.startswith(code):
            return code
    return "92" # Default to Pakistan

async def create_account(site_id: str, invite_code: str, proxy: str = None, password: str = "53561106Tojo"):
    """Creates a new registered account on c88zz.com."""
    def _sync_create():
        last_error = ""
        # Try up to 3 times to register an account with a unique number
        for attempt in range(3):
            try:
                mobile = generate_unique_mobile()
                client = C88ZZClient(proxy_url=proxy)
                resp = client.register(mobile, password, invite_code)
                
                if resp.get("code") == 200:
                    logger.info(f"Registered new mobile successfully: {mobile}")
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

async def start_whatsapp_link(username: str, password: str, proxy: str = None, wa_phone: str = ""):
    """Logs into the c88zz.com account, requests the WhatsApp link, and fetches the pairing code."""
    if wa_phone.startswith("+"):
        wa_phone = wa_phone[1:]
        
    area_code = extract_area_code(wa_phone)
    
    def _sync_start():
        client = C88ZZClient(proxy_url=proxy)
        try:
            # Login
            login_res = client.login(username, password)
            if login_res.get("code") != 200:
                raise RuntimeError(f"Login failed: {login_res.get('message')}")
            
            # Start linking
            start_res = client.whatsapp_start(wa_phone, area_code)
            if start_res.get("code") != 200:
                raise RuntimeError(f"WhatsApp start link failed: {start_res.get('message')}")
            
            session_id = start_res.get("data", {}).get("session_id")
            if not session_id:
                raise RuntimeError("No session_id returned from whatsapp_start")
                
            time.sleep(2)
            
            # Get code
            code_res = client.whatsapp_code(session_id, wa_phone)
            if code_res.get("code") != 200:
                raise RuntimeError(f"Failed to fetch pairing code: {code_res.get('message')}")
                
            pair_code = code_res.get("data", {}).get("pair_code") or code_res.get("data", {}).get("code")
            if not pair_code:
                raise RuntimeError("No pairing code returned from whatsapp_code")
                
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
            status_res = client.whatsapp_status(session_id)
            
            # Look at both check and status response
            is_online = check_res.get("data", {}).get("is_online", False)
            status_val = status_res.get("data", {}).get("status", "")
            
            # Return uniform dictionary format for tg_bot to consume
            if is_online or status_val == "WORKING":
                return {"code": 200, "data": {"login_status": 2, "wid": "Linked"}}
            else:
                return {"code": 200, "data": {"login_status": 1, "wid": ""}}
        except Exception as e:
            return {"code": 500, "msg": str(e)}

    return await asyncio.to_thread(_sync_poll)
