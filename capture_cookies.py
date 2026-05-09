# -*- coding: utf-8 -*-
"""
Manual Cookie Capture Script for Emailnator
Run this locally on your PC.
Requirements: pip install playwright && playwright install chromium
"""
import json
import time
from playwright.sync_api import sync_playwright

def capture():
    proxy_url = input("Enter your proxy (e.g. http://user:pass@host:port) or leave empty: ").strip()
    
    print("Launching browser... Please wait.")
    with sync_playwright() as p:
        launch_args = {}
        if proxy_url:
            launch_args['proxy'] = {'server': proxy_url}
            
        # Launch browser with stealth arguments
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                "--no-sandbox"
            ],
            **launch_args
        )
        context = browser.new_context(
            viewport=None, # Use maximized window
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        page = context.new_page()
        
        print("Navigating to Emailnator...")
        page.goto("https://www.emailnator.com/")
        
        print("\n" + "!"*50)
        print("INSTRUCTIONS:")
        print("1. Solve the Cloudflare captcha manually in the browser window.")
        print("2. Once you see the generated email or the 'Go' button, come back here.")
        print("3. DO NOT close the browser yet.")
        print("!"*50 + "\n")
        
        input("Press Enter here AFTER you have solved the captcha and the page is fully loaded...")
        
        # Get cookies and User-Agent
        cookies = context.cookies()
        user_agent = page.evaluate("navigator.userAgent")
        
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
        print("Copy this file to your bot's root directory or use the /updatecookies command.")
        
        browser.close()

if __name__ == "__main__":
    try:
        capture()
    except Exception as e:
        print(f"Error: {e}")
        input("Press Enter to exit...")
