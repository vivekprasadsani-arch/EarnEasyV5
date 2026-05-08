# -*- coding: utf-8 -*-
"""
Test: 5 accounts on p1.x7bb.com
- Uses existing EmailnatorClient (legacy -> CF bypass -> EmailMux fallback)
- Fresh session + fresh proxy connection per account (no reuse)
- Unique anon_uid / fingerprint per account
- Retries new email if "already registered"
- Detailed per-step logging
"""
import re
import sys
import time

# Make sure project root is importable
sys.path.insert(0, ".")

from bot_requests import EmailnatorClient, DeepEarnClient

# ───────────────── CONFIG ─────────────────
PROXY_URL    = "http://2HPFf1pbZR90_custom_zone_PK:2966098@change4.owlproxy.com:7778"
DOMAIN       = "p1.x7bb.com"
INVITE_CODE  = "96568524"
PASSWORD     = "53561106Tojo"
NUM_ACCOUNTS = 5
# ──────────────────────────────────────────


def _close(client):
    try:
        client.close()
    except Exception:
        pass


def create_one_account(index: int) -> dict:
    """
    Fully isolated account creation:
      - Fresh EmailnatorClient  (new session, new cookies, new CF bypass if needed)
      - Fresh DeepEarnClient    (new session, new anon_uid, new UA)
      - Retry up to 5 different emails if "already registered"
    """
    print(f"\n[{index}] === Starting account #{index} ===")

    email_client = None
    earn_client  = None

    try:
        # ── Step 1: Create fresh DeepEarn client ──────────────────────────
        earn_client = DeepEarnClient(
            inviter_code=INVITE_CODE,
            proxy_url=PROXY_URL,
            domain=DOMAIN,
            allow_proxy_fallback=True,
        )
        print(f"[{index}] DeepEarn client ready | currency={earn_client.currency} | anon_uid={earn_client.anon_uid[:20]}...")

        # ── Step 2: Generate a fresh unregistered email ───────────────────
        email = None
        for email_try in range(5):
            # Fresh email client each attempt → no cookie/session reuse
            if email_client:
                _close(email_client)
            print(f"[{index}] Email attempt {email_try + 1}/5 ...")
            email_client = EmailnatorClient(
                proxy_url=PROXY_URL,
                allow_proxy_fallback=True,
            )
            candidate = email_client.generate_email()
            print(f"[{index}]   Generated email: {candidate}")

            # Test OTP send to check if email is available
            otp_resp = earn_client.send_otp(candidate)
            print(f"[{index}]   OTP send response: {otp_resp}")

            if otp_resp.get("code") == 200:
                email = candidate
                print(f"[{index}]   Email is fresh and usable!")
                break
            elif "registered" in str(otp_resp.get("msg", "")).lower():
                print(f"[{index}]   Email already registered, trying another...")
                time.sleep(1)
                continue
            else:
                raise RuntimeError(f"OTP send failed: {otp_resp.get('msg')}")

        if not email:
            raise RuntimeError("Could not get an unregistered email after 5 tries")

        # ── Step 3: Poll inbox for OTP ────────────────────────────────────
        print(f"[{index}] Polling inbox for OTP ...")
        otp = None
        for poll in range(20):
            time.sleep(2)
            msgs = email_client.get_messages(email)
            for m in msgs:
                subj = m.get("subject", "")
                if any(kw in subj.lower() for kw in ["verification", "code", "registration"]):
                    mid = m.get("messageID") or m.get("uuid")
                    if not mid:
                        continue
                    content = email_client.get_message_content(email, mid)
                    match = (
                        re.search(r">(\d{6})<", content)
                        or re.search(r"color:\s*red.*?(\d{6})", content, re.S)
                        or re.search(r"\b(\d{6})\b", content)
                    )
                    if match:
                        otp = match.group(1)
                        print(f"[{index}]   OTP found after {poll+1} polls: {otp}")
                        break
            if otp:
                break

        if not otp:
            raise RuntimeError("Timeout: OTP email never arrived (20 polls x 2s)")

        # ── Step 4: Register ──────────────────────────────────────────────
        print(f"[{index}] Registering account ...")
        reg_resp = earn_client.register(email, PASSWORD, otp)
        print(f"[{index}]   Register response: {reg_resp}")

        if reg_resp.get("code") != 200:
            raise RuntimeError(f"Registration failed: {reg_resp.get('msg')}")

        print(f"[{index}] SUCCESS! email={email}")
        return {"index": index, "email": email, "success": True, "error": ""}

    except Exception as exc:
        err = re.sub(r'https?://\S+', '<url>', str(exc))
        print(f"[{index}] FAILED: {err}")
        return {"index": index, "email": "", "success": False, "error": err}

    finally:
        _close(email_client)
        _close(earn_client)


def main():
    print("=" * 60)
    print(f"  Target : {DOMAIN}")
    print(f"  Invite : {INVITE_CODE}")
    print(f"  Proxy  : ...{PROXY_URL[-30:]}")
    print(f"  Count  : {NUM_ACCOUNTS} accounts")
    print("=" * 60)

    results = []
    for i in range(1, NUM_ACCOUNTS + 1):
        result = create_one_account(i)
        results.append(result)
        if i < NUM_ACCOUNTS:
            print(f"\n  Waiting 4s before next account...\n")
            time.sleep(4)

    print("\n" + "=" * 60)
    print("  FINAL RESULTS")
    print("=" * 60)
    ok  = [r for r in results if r["success"]]
    bad = [r for r in results if not r["success"]]
    print(f"  SUCCESS: {len(ok)}/{NUM_ACCOUNTS}")
    print(f"  FAILED:  {len(bad)}/{NUM_ACCOUNTS}")
    print()
    for r in results:
        mark   = "OK " if r["success"] else "ERR"
        detail = r["email"] if r["success"] else r["error"]
        print(f"  [{mark}] #{r['index']} : {detail}")
    print("=" * 60)


if __name__ == "__main__":
    main()
