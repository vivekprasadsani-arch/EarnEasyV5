import asyncio
import logging
import re
import json
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, 
                           InlineKeyboardMarkup, InlineKeyboardButton, ForceReply, BotCommand)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import CommandStart, Command

import config
import database as db
import bot_backend as backend
from bot_requests import normalize_proxy_url, add_notification_callback

async def notify_admin_handler(message_text):
    """Callback to notify admin when manual intervention is needed."""
    if config.ADMIN_USER_ID:
        try:
            await bot.send_message(config.ADMIN_USER_ID, message_text)
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")

# Register the callback
add_notification_callback(notify_admin_handler)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()
router = Router()

class BotStates(StatesGroup):
    waiting_for_proxy = State()
    waiting_for_whatsapp_number = State()

COUNTRIES = {
    "pakistan": "🇵🇰 Pakistan"
}

def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Add WhatsApp")],
            [KeyboardButton(text="💵 Check Balance"), KeyboardButton(text="⚙️ Settings")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )

async def setup_bot_commands():
    await bot.set_my_commands([
        BotCommand(command="start", description="Start/Register main account"),
    ])

async def safe_edit_message(message: Message, text: str, parse_mode: str = None):
    try:
        return await message.edit_text(text, parse_mode=parse_mode)
    except Exception:
        return await message.answer(text, parse_mode=parse_mode)

async def safe_delete_message(message: Message):
    try:
        await message.delete()
    except Exception:
        pass

async def safe_answer_callback(cq: CallbackQuery, text: str = None, show_alert: bool = False):
    try:
        await cq.answer(text, show_alert=show_alert)
    except Exception:
        pass

async def check_user_access(user_id: int, username: str, first_name: str, message_to_reply=None) -> bool:
    if user_id == config.ADMIN_USER_ID:
        # Admin is instantly approved
        user = await db.get_user(user_id)
        if not user:
            await db.add_or_update_user(user_id, username, first_name, status="approved")
        return True

    user = await db.get_user(user_id)
    if not user:
        # User must type /start first which handles creation
        return False
        
    if user['status'] == 'rejected':
        if message_to_reply:
            if isinstance(message_to_reply, Message):
                await message_to_reply.answer("❌ Your account request was rejected.")
            elif isinstance(message_to_reply, CallbackQuery):
                await message_to_reply.answer("❌ Account rejected.", show_alert=True)
        return False
        
    if user['status'] == 'pending':
        import datetime
        last_req_str = user.get('last_request_at')
        can_request_again = False
        time_diff_msg = ""
        
        if not last_req_str:
            can_request_again = True
        else:
            try:
                last_req = datetime.datetime.fromisoformat(last_req_str.replace("Z", "+00:00"))
                now = datetime.datetime.now(timezone.utc)
                diff = now - last_req
                if diff.total_seconds() >= 3600:
                    can_request_again = True
                else:
                    remaining_seconds = 3600 - diff.total_seconds()
                    remaining_minutes = int(remaining_seconds // 60)
                    time_diff_msg = f"\n⏳ You can send another request in {remaining_minutes} minutes."
            except Exception as e:
                logger.error(f"Error parsing last_request_at: {e}")
                can_request_again = True
                
        if can_request_again:
            await db.update_user_last_request(user_id)
            if config.ADMIN_USER_ID != 0:
                try:
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Approve ✅", callback_data=f"approve_{user_id}"),
                         InlineKeyboardButton(text="Reject ❌", callback_data=f"reject_{user_id}")]
                    ])
                    await bot.send_message(
                        config.ADMIN_USER_ID, 
                        f"Reminder: User request still pending:\nID: {user_id}\nName: {first_name}\nUsername: @{username}",
                        reply_markup=kb
                    )
                except Exception as e:
                    logger.error(f"Failed to notify admin: {e}")
            
            if message_to_reply:
                msg_text = "⏳ Your account is pending admin approval. A reminder has been sent to the admin. Please wait."
                if isinstance(message_to_reply, Message):
                    await message_to_reply.answer(msg_text)
                elif isinstance(message_to_reply, CallbackQuery):
                    await message_to_reply.answer(msg_text, show_alert=True)
        else:
            if message_to_reply:
                msg_text = f"⏳ Your account is still pending admin approval. Please wait.{time_diff_msg}"
                if isinstance(message_to_reply, Message):
                    await message_to_reply.answer(msg_text)
                elif isinstance(message_to_reply, CallbackQuery):
                    await message_to_reply.answer(msg_text, show_alert=True)
        return False
        
    return True

@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or ""
    first_name = message.from_user.first_name or ""
    
    user = await db.get_user(user_id)
    if not user:
        status_msg = await message.answer("🔄 Welcome! Setting up your main C88ZZ account, please wait...")
        try:
            # Register a new C88ZZ account under default refer code ZF5998
            main_mobile = await backend.create_account("pakistan", "ZF5998", proxy=None, password="53561106@Roni")
            
            # Log in to fetch the generated invite code
            client = backend.C88ZZClient()
            try:
                login_res = client.login(main_mobile, "53561106@Roni")
                if login_res.get("code") != 200:
                    raise RuntimeError("Login to fetch invite code failed")
                info_res = client.user_info()
                main_invite_code = info_res.get("data", {}).get("invite_code")
                if not main_invite_code:
                    raise RuntimeError("Failed to fetch invite code")
            finally:
                client.close()
                
            # Create user in database
            await db.add_or_update_user(user_id, username, first_name, status="pending")
            await db.update_user_main_account(user_id, main_mobile, main_invite_code)
            await db.update_user_last_request(user_id)
            
            # Notify Admin
            if config.ADMIN_USER_ID != 0:
                try:
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Approve ✅", callback_data=f"approve_{user_id}"),
                         InlineKeyboardButton(text="Reject ❌", callback_data=f"reject_{user_id}")]
                    ])
                    await bot.send_message(
                        config.ADMIN_USER_ID, 
                        f"New user registration request:\nID: {user_id}\nName: {first_name}\nUsername: @{username}\nC88ZZ Mobile: {main_mobile}\nC88ZZ Invite Code: {main_invite_code}",
                        reply_markup=kb
                    )
                except Exception as admin_err:
                    logger.error(f"Failed to notify admin on start: {admin_err}")
            
            await safe_edit_message(
                status_msg,
                f"✅ **Main Account Created!**\n\n"
                f"Your C88ZZ Refer Code is: `{main_invite_code}`\n\n"
                f"⏳ Your account is currently pending admin approval. You will be notified once approved."
            )
        except Exception as e:
            logger.error(f"Start registration failed: {e}")
            await safe_edit_message(status_msg, "❌ Failed to automatically register your main account. Please type /start to retry.")
        return
        
    has_access = await check_user_access(user_id, username, first_name, message)
    if has_access:
        await message.answer("🎉 Welcome back! Select an option below to get started.", reply_markup=main_keyboard())

@router.callback_query(F.data.startswith("approve_"))
async def approve_user(cq: CallbackQuery):
    if cq.from_user.id != config.ADMIN_USER_ID:
        return
    uid = int(cq.data.split("_")[1])
    await db.update_user_status(uid, "approved")
    await cq.message.edit_text(cq.message.text + "\n\n✅ Approved.")
    try:
        await bot.send_message(uid, "🎉 **Congratulations!** Your account has been approved by the Admin!\nUse the menu below to add WhatsApp and start earning.", reply_markup=main_keyboard())
    except:
        pass
    await safe_answer_callback(cq, "User approved.")

@router.callback_query(F.data.startswith("reject_"))
async def reject_user(cq: CallbackQuery):
    if cq.from_user.id != config.ADMIN_USER_ID:
        return
    uid = int(cq.data.split("_")[1])
    await db.update_user_status(uid, "rejected")
    await cq.message.edit_text(cq.message.text + "\n\n❌ Rejected.")
    await safe_answer_callback(cq, "User rejected.")

@router.message(F.text == "⚙️ Settings")
async def show_settings(message: Message):
    if not await check_user_access(message.from_user.id, message.from_user.username or "", message.from_user.first_name, message):
        return
    user = await db.get_user(message.from_user.id)
    proxy = user['proxy'] if user['proxy'] else "Not set"
    
    kb_buttons = [
        [InlineKeyboardButton(text="Set Proxy", callback_data="set_proxy")]
    ]
    if user['proxy']:
        kb_buttons.append([InlineKeyboardButton(text="Test Proxy", callback_data="test_proxy")])
        
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    await message.answer(f"⚙️ **Settings**\n\nCurrent Proxy: `{proxy}`", reply_markup=kb, parse_mode="Markdown")

@router.callback_query(F.data == "set_proxy")
async def prompt_proxy(cq: CallbackQuery, state: FSMContext):
    if not await check_user_access(cq.from_user.id, cq.from_user.username or "", cq.from_user.first_name, cq):
        return
    await cq.message.answer("🌐 Please send your proxy in the format `http://user:pass@host:port` (or type 'clear' to remove):", parse_mode="Markdown")
    await state.set_state(BotStates.waiting_for_proxy)
    await safe_answer_callback(cq)

@router.message(BotStates.waiting_for_proxy)
async def process_proxy(message: Message, state: FSMContext):
    proxy = message.text.strip()
    if proxy.lower() == 'clear':
        await db.set_user_proxy(message.from_user.id, None)
        await message.answer("✅ Proxy cleared.", reply_markup=main_keyboard())
    else:
        await db.set_user_proxy(message.from_user.id, proxy)
        await message.answer("✅ Proxy saved.", reply_markup=main_keyboard())
    await state.clear()

@router.callback_query(F.data == "test_proxy")
async def test_proxy_connection(cq: CallbackQuery):
    if not await check_user_access(cq.from_user.id, cq.from_user.username or "", cq.from_user.first_name, cq):
        return
        
    user = await db.get_user(cq.from_user.id)
    proxy = normalize_proxy_url(user['proxy'])
    if not proxy:
        await safe_answer_callback(cq, "No proxy set to test.", show_alert=True)
        return
        
    await safe_answer_callback(cq, "Testing proxy... please wait.", show_alert=False)
    
    def _sync_test():
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        try:
            res = requests.get(
                "https://httpbin.org/ip",
                proxies={"http": proxy, "https": proxy},
                timeout=15,
                verify=False
            )
            res.raise_for_status()
            origin_ip = (res.json() or {}).get("origin", "unknown")
            return True, f"✅ Proxy is working!\n\nIP: `{origin_ip}`"
        except Exception as e:
            return False, f"❌ Proxy failed.\n\n`{str(e)}`"

    success, msg = await asyncio.to_thread(_sync_test)
    await cq.message.answer(msg, parse_mode="Markdown")

@router.message(F.text == "💵 Check Balance")
async def cmd_check_balance(message: Message):
    user_id = message.from_user.id
    if not await check_user_access(user_id, message.from_user.username or "", message.from_user.first_name, message):
        return
        
    user = await db.get_user(user_id)
    main_mobile = user.get("main_mobile")
    main_invite_code = user.get("main_invite_code")
    proxy = user.get("proxy")
    
    if not main_mobile:
        await message.answer("❌ Main account not registered. Please type /start to create your main account.")
        return
        
    status_msg = await message.answer("🔄 Fetching balance from your main account...")
    try:
        balance = await backend.get_main_account_balance(main_mobile, "53561106@Roni", proxy)
        await safe_edit_message(
            status_msg,
            f"👤 **Main Account details**\n\n"
            f"Mobile: `{main_mobile}`\n"
            f"Refer Code: `{main_invite_code}`\n"
            f"Balance: `{balance}` PKR",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Failed to fetch balance: {e}")
        await safe_edit_message(status_msg, f"❌ Failed to fetch balance: {str(e)}")

# MAIN ADD WHATSAPP FLOW
@router.message(F.text == "📱 Add WhatsApp")
async def add_whatsapp_menu(message: Message):
    if not await check_user_access(message.from_user.id, message.from_user.username or "", message.from_user.first_name, message):
        return
    await message.answer("📱 Please enter the WhatsApp number you want to link (e.g. +923XXXXXXXXX):", reply_markup=ForceReply())
    await state_set_whatsapp_number(message.from_user.id)

async def state_set_whatsapp_number(user_id: int):
    # Set FSM state for user
    state = dp.fsm.resolve_context(bot, user_id, user_id)
    await state.set_state(BotStates.waiting_for_whatsapp_number)

@router.message(BotStates.waiting_for_whatsapp_number)
async def process_whatsapp_number(message: Message, state: FSMContext):
    wa_phone = message.text.strip()
    # Strip formatting characters like spaces, brackets, hyphens, but keep leading '+'
    cleaned_phone = "".join(c for i, c in enumerate(wa_phone) if c.isdigit() or (c == '+' and i == 0))
    if not cleaned_phone:
        await message.answer("❌ Invalid phone number. Please try again:")
        return
        
    await state.clear()
    
    asyncio.create_task(
        start_pairing_flow(
            message,
            user_id=message.from_user.id,
            wa_phone=cleaned_phone
        )
    )

async def start_pairing_flow(message: Message, user_id: int, wa_phone: str, message_to_edit: Message = None):
    if message_to_edit:
        try:
            await message_to_edit.delete()
        except:
            pass
            
    status_msg = await message.answer(f"🔄 Preparing referral account... Please wait.")
    
    user_data = await db.get_user(user_id)
    proxy = user_data['proxy']
    main_invite_code = user_data.get('main_invite_code') or "ZF5998"
    
    try:
        # Create a new C88ZZ referral account registered under the user's main invite code
        new_mobile = await backend.create_account("pakistan", main_invite_code, proxy, "53561106@Roni")
        await db.add_account(user_id, "pakistan", new_mobile, "53561106@Roni", main_invite_code)
                
        status_msg = await safe_edit_message(
            status_msg,
            f"🔄 Account created. Requesting linking code for `{wa_phone}`...",
            parse_mode="Markdown"
        )
        
        # Start link and get pairing code
        client, session_id, pair_code = await backend.start_whatsapp_link(new_mobile, "53561106@Roni", proxy, wa_phone)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Copy Code", switch_inline_query=pair_code)]
        ])
        
        sent_msg = await message.answer(
            f"📱 **WhatsApp Link Code Ready**\n\n"
            f"WhatsApp: `{wa_phone}`\n"
            f"Code: `{pair_code}`\n\n"
            f"**How to link:**\n"
            f"1. Open WhatsApp -> Settings -> Linked Devices\n"
            f"2. Tap **Link a Device** -> **Link with phone number instead**\n"
            f"3. Enter the 8-digit code: `{pair_code}`",
            parse_mode="Markdown",
            reply_markup=kb
        )
        await safe_delete_message(status_msg)
        
        # Start polling for success
        asyncio.create_task(
            poll_for_success(
                sent_msg,
                client,
                session_id,
                main_invite_code,
                new_mobile,
                "pakistan"
            )
        )
        
    except Exception as e:
        logger.error(f"Error starting pairing: {e}")
        await safe_edit_message(status_msg, f"❌ Error during account linking.\n\n`{str(e)}`", parse_mode="Markdown")

async def poll_for_success(message: Message, client, session_id, invite_code, email, country_code):
    user_id = message.chat.id
    try:
        for _ in range(60): # Poll for max length (roughly 2 minutes)
            await asyncio.sleep(2)
            res = await backend.poll_wa_status(client, session_id)
            
            if res.get("code") == 200:
                res_data = res.get("data", {})
                status = int(res_data.get("login_status", 0))
                
                if status == 2:
                    await db.mark_account_linked(user_id, country_code, email)
                    
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Next ➡️ (Link New Number)", callback_data="next_link_number")]
                    ])
                    
                    await message.edit_text(
                        f"✅ **Success!**\n\nWhatsApp has been successfully linked!\n\nUse the Next button to link another WhatsApp number.", 
                        parse_mode="Markdown",
                        reply_markup=kb
                    )
                    return
    finally:
        try:
            client.close()
        except Exception:
            pass

@router.callback_query(F.data == "next_link_number")
async def handle_next_action(cq: CallbackQuery):
    if not await check_user_access(cq.from_user.id, cq.from_user.username or "", cq.from_user.first_name, cq):
        return
        
    await cq.message.answer("📱 Please enter the WhatsApp number you want to link (e.g. +923XXXXXXXXX):", reply_markup=ForceReply())
    await state_set_whatsapp_number(cq.from_user.id)
    try:
        await cq.message.delete()
    except:
        pass

async def start_background_monitoring():
    """Background task to periodically log into all registered C88ZZ accounts to keep sessions active."""
    logger.info("Background keep-alive monitor task initialized.")
    while True:
        # Run every 4 hours
        await asyncio.sleep(4 * 3600)
        try:
            accounts = await db.get_all_accounts_admin()
            logger.info(f"Monitor: Running keep-alive for {len(accounts)} accounts...")
            for acc in accounts:
                username = acc.get("email") # stored in email column
                password = acc.get("password") or "53561106@Roni"
                user_id = acc.get("user_id")
                user_data = await db.get_user(user_id)
                proxy = user_data.get("proxy") if user_data else None
                
                # Fire and forget keep-alive login to C88ZZ
                await backend.keep_alive_account(username, password, proxy)
                await asyncio.sleep(2) # 2s rate-limiting delay between accounts
        except Exception as e:
            logger.error(f"Monitor loop error: {e}")

async def main():
    await db.init_db()
    await setup_bot_commands()
    dp.include_router(router)
    
    # Start Web Server (Admin Panel) in the background
    import web_server
    asyncio.create_task(web_server.start_server())
    
    # Start 24/7 Keep-alive Account Monitoring Task
    asyncio.create_task(start_background_monitoring())
    
    # Start bot
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
