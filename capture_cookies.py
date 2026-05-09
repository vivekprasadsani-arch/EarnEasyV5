# -*- coding: utf-8 -*-
"""
Improved Manual Cookie Capture Script using Camoufox (Same as Bot)
Run this locally on your PC.
Requirements: pip install camoufox[geoip] playwright-captcha
"""
import json
import time
import asyncio
import os
import sys
from pathlib import Path

# Add project path to sys.path so we can import from the repo
project_path = os.getcwd()
sys.path.insert(0, project_path)
repo_path = os.path.join(project_path, "CloudflareBypassForScraping-main")
sys.path.insert(0, repo_path)

from camoufox.async_api import AsyncCamoufox
from playwright_captcha import CaptchaType, ClickSolver, FrameworkType
from playwright_captcha.utils.camoufox_add_init_script.add_init_script import get_addon_path

# Try to import utils from repo if possible, otherwise use fallback
try:
    from cf_bypasser.utils.misc import get_browser_init_lock
    from cf_bypasser.utils.config import BrowserConfig
except ImportError:
    # Minimal fallback if imports fail
    def get_browser_init_lock():
        import threading
        return threading.Lock()
    class BrowserConfig:
        @staticmethod
        def generate_random_config(os_name, lang):
            return {}

ADDON_PATH = get_addon_path()

async def capture():
    proxy_url = input("Enter your Pakistan proxy (e.g. http://user:pass@host:port): ").strip()
    if not proxy_url:
        print("Proxy is required to ensure cookies are valid for your Pakistan session.")
        return

    print("\nLaunching Camoufox in VISIBLE mode... Please wait.")
    
    # Generate a realistic config
    # We use windows to match most PC browsers
    random_config = {
        'window.outerWidth': 1920,
        'window.outerHeight': 1080,
    }

    # Setup proxy
    from urllib.parse import urlparse
    parsed = urlparse(proxy_url)
    proxy_config = {
        "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
    }
    if parsed.username and parsed.password:
        proxy_config["username"] = parsed.username
        proxy_config["password"] = parsed.password

    # Use lock like the bot
    lock = get_browser_init_lock()
    if hasattr(lock, '__enter__'):
        ctx = lock
    else:
        # It's the async lock from my previous fix
        ctx = None

    async with AsyncCamoufox(
        headless=False, # HEADFUL so you can solve and click
        geoip=True,
        proxy=proxy_config,
        i_know_what_im_doing=True,
        config={'forceScopeAccess': True, **random_config},
    ) as browser:
        
        context = await browser.new_context(proxy=proxy_config)
        page = await context.new_page()
        
        print("Navigating to Emailnator...")
        await page.goto("https://www.emailnator.com/", wait_until="domcontentloaded")
        
        print("\n" + "!"*50)
        print("INSTRUCTIONS:")
        print("1. If a Cloudflare checkbox appears, the script will try to click it.")
        print("2. If it fails, you can MANUALLY click it in the browser window.")
        print("3. Once you see the email address or 'Go' button, come back here.")
        print("!"*50 + "\n")

        # Try automatic solve using repo's logic
        try:
            print("Attempting automatic click...")
            async with ClickSolver(framework=FrameworkType.CAMOUFOX, page=page, max_attempts=3, attempt_delay=3) as solver:
                await solver.solve_captcha(
                    captcha_container=page,
                    captcha_type=CaptchaType.CLOUDFLARE_TURNSTILE)
                print("Automatic solve attempt finished.")
        except Exception as e:
            print(f"Automatic solve error (ignore if you solved it manually): {e}")

        input("Press Enter here AFTER the page is fully loaded and captcha is solved...")
        
        # Get cookies and User-Agent
        cookies = await context.cookies()
        user_agent = await page.evaluate("navigator.userAgent")
        
        cookie_dict = {c['name']: c['value'] for c in cookies}
        
        output = {
            "cookies": cookie_dict,
            "user_agent": user_agent,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        filename = "manual_emailnator_cookies.json"
        with open(filename, "w") as f:
            json.dump(output, f, indent=4)
            
        print(f"\nSUCCESS! Cookies saved to {filename}")
        print("Use /updatecookies in Telegram to upload this file.")

if __name__ == "__main__":
    try:
        asyncio.run(capture())
    except Exception as e:
        print(f"Error: {e}")
        input("Press Enter to exit...")
