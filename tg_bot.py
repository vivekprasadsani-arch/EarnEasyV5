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
    waiting_for_payment_details = State()

COUNTRIES = {
    "pakistan": "🇵🇰 Pakistan"
}

def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Add WhatsApp")],
            [KeyboardButton(text="💵 Check Balance"), KeyboardButton(text="📤 Withdraw")],
            [KeyboardButton(text="⚙️ Settings")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )

async def setup_bot_commands():
    await bot.set_my_commands([
        BotCommand(command="start", description="Start the bot"),
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
        
        # Save user to DB as pending immediately so they have a database row
        await db.add_or_update_user(user_id, username, first_name, status="pending")
        await db.update_user_last_request(user_id)
        
        try:
            # Fetch admin's proxy to use as the registration proxy
            admin_data = await db.get_user(config.ADMIN_USER_ID)
            reg_proxy = admin_data.get("proxy") if admin_data else None
            
            # Register a new C88ZZ account under default refer code ZF5998
            main_mobile = await backend.create_account("pakistan", "ZF5998", proxy=reg_proxy, password="53561106@Roni")
            
            # Log in to fetch the generated invite code
            client = backend.C88ZZClient(proxy_url=reg_proxy)
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
                
            # Update user in database with main account details
            await db.update_user_main_account(user_id, main_mobile, main_invite_code)
            
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
                f"✅ **Welcome!** Your registration request has been submitted.\n\n"
                f"⏳ Your account is currently pending admin approval. You will be notified once approved."
            )
        except Exception as e:
            logger.error(f"Start C88ZZ registration failed (will retry later): {e}")
            # Notify Admin without C88ZZ details
            if config.ADMIN_USER_ID != 0:
                try:
                    kb = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Approve ✅", callback_data=f"approve_{user_id}"),
                         InlineKeyboardButton(text="Reject ❌", callback_data=f"reject_{user_id}")]
                    ])
                    await bot.send_message(
                        config.ADMIN_USER_ID, 
                        f"New user request (C88ZZ Main Account Pending creation due to proxy/network error):\nID: {user_id}\nName: {first_name}\nUsername: @{username}",
                        reply_markup=kb
                    )
                except Exception as admin_err:
                    logger.error(f"Failed to notify admin on start: {admin_err}")
                    
            await safe_edit_message(
                status_msg,
                "⏳ **Welcome!** Your request is pending admin approval.\n\n"
                "*(Note: We couldn't register your C88ZZ main account automatically due to Render IP restrictions. "
                "Once the Admin approves you, you can set your proxy in settings and we will automatically retry creating your main C88ZZ account)."
            )
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
    pay_method = user.get("payment_method") or "Not set"
    pay_details = user.get("payment_details") or "Not set"
    
    kb_buttons = [
        [InlineKeyboardButton(text="🌐 Set Proxy", callback_data="set_proxy"),
         InlineKeyboardButton(text="💳 Payment Method", callback_data="set_payment_method")]
    ]
    if user['proxy']:
        kb_buttons.append([InlineKeyboardButton(text="Test Proxy Connection", callback_data="test_proxy")])
        
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    await message.answer(
        f"⚙️ **Settings**\n\n"
        f"🌐 **Current Proxy:** `{proxy}`\n"
        f"💳 **Payment Method:** `{pay_method}`\n"
        f"📱 **Payment Details:** `{pay_details}`",
        reply_markup=kb,
        parse_mode="Markdown"
    )

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

@router.callback_query(F.data == "set_payment_method")
async def prompt_payment_method(cq: CallbackQuery):
    if not await check_user_access(cq.from_user.id, cq.from_user.username or "", cq.from_user.first_name, cq):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="bKash", callback_data="pay_bKash"),
         InlineKeyboardButton(text="Nagad", callback_data="pay_nagad")],
        [InlineKeyboardButton(text="Binance Pay", callback_data="pay_binance"),
         InlineKeyboardButton(text="UPI", callback_data="pay_upi")]
    ])
    await cq.message.answer("💳 **Select your Payment Method:**", reply_markup=kb, parse_mode="Markdown")
    await safe_answer_callback(cq)

@router.callback_query(F.data.startswith("pay_"))
async def select_pay_method(cq: CallbackQuery, state: FSMContext):
    method = cq.data.split("_")[1] # bKash, nagad, binance, upi
    method_name = {
        "bKash": "bKash",
        "nagad": "Nagad",
        "binance": "Binance Pay",
        "upi": "UPI"
    }.get(method, method)
    
    await state.update_data(temp_pay_method=method_name)
    await cq.message.answer(f"📱 Please enter your **{method_name}** number/address details:", reply_markup=ForceReply(), parse_mode="Markdown")
    await state.set_state(BotStates.waiting_for_payment_details)
    await safe_answer_callback(cq)

@router.message(BotStates.waiting_for_payment_details)
async def process_payment_details(message: Message, state: FSMContext):
    details = message.text.strip()
    if not details:
        await message.answer("❌ Details cannot be empty. Please try again:")
        return
        
    data = await state.get_data()
    method = data.get("temp_pay_method", "bKash")
    
    await db.set_user_payment_details(message.from_user.id, method, details)
    await message.answer(f"✅ Payment method saved successfully!\n\n**Method:** {method}\n**Details:** `{details}`", reply_markup=main_keyboard())
    await state.clear()

@router.message(F.text == "💵 Check Balance")
async def cmd_check_balance(message: Message):
    user_id = message.from_user.id
    if not await check_user_access(user_id, message.from_user.username or "", message.from_user.first_name, message):
        return
        
    user = await db.get_user(user_id)
    main_mobile = user.get("main_mobile")
    main_invite_code = user.get("main_invite_code")
    proxy = user.get("proxy")
    password = user.get("custom_password") or "53561106@Roni"
    
    status_msg = await message.answer("🔄 Connecting to C88ZZ...")
    
    # Self-healing: If the main account was not created during /start, create it now
    if not main_mobile:
        status_msg = await safe_edit_message(status_msg, "🔄 Registering your main C88ZZ account first... please wait.")
        try:
            main_mobile = await backend.create_account("pakistan", "ZF5998", proxy, password)
            client = backend.C88ZZClient(proxy_url=proxy)
            try:
                login_res = client.login(main_mobile, password)
                if login_res.get("code") != 200:
                    raise RuntimeError("Login to fetch invite code failed")
                info_res = client.user_info()
                main_invite_code = info_res.get("data", {}).get("invite_code")
                if not main_invite_code:
                    raise RuntimeError("Failed to fetch invite code")
            finally:
                client.close()
                
            await db.update_user_main_account(user_id, main_mobile, main_invite_code)
            status_msg = await safe_edit_message(status_msg, "🔄 Main account registered successfully. Fetching balance...")
        except Exception as e:
            logger.error(f"Balance check auto-registration failed: {e}")
            await safe_edit_message(status_msg, f"❌ Failed to register main account: {str(e)}\n\n*(Hint: If you need a proxy to register, please check settings).*")
            return
            
    try:
        balance = await backend.get_main_account_balance(main_mobile, password, proxy)
        try:
            points = float(balance)
            usd_val = (points * 0.05) / 278.0
        except Exception:
            usd_val = 0.0
        await safe_edit_message(
            status_msg,
            f"💵 **Your Balance:** `${usd_val:.2f} USD` ({balance} Points)",
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
    password = user_data.get("custom_password") or "53561106@Roni"
    main_invite_code = user_data.get('main_invite_code')
    main_mobile = user_data.get('main_mobile')
    
    # Self-healing: If main account was never created, create it now
    if not main_mobile or not main_invite_code:
        status_msg = await safe_edit_message(status_msg, "🔄 Registering your main C88ZZ account first... please wait.")
        try:
            main_mobile = await backend.create_account("pakistan", "ZF5998", proxy, password)
            client = backend.C88ZZClient(proxy_url=proxy)
            try:
                login_res = client.login(main_mobile, password)
                if login_res.get("code") != 200:
                    raise RuntimeError("Login to fetch invite code failed")
                info_res = client.user_info()
                main_invite_code = info_res.get("data", {}).get("invite_code")
                if not main_invite_code:
                    raise RuntimeError("Failed to fetch invite code")
            finally:
                client.close()
                
            await db.update_user_main_account(user_id, main_mobile, main_invite_code)
            status_msg = await safe_edit_message(status_msg, "🔄 Main account registered successfully. Preparing referral account...")
        except Exception as e:
            logger.error(f"Linking flow auto-registration failed: {e}")
            await safe_edit_message(status_msg, f"❌ Failed to register main account: {str(e)}\n\n*(Hint: If you need a proxy to register, please check settings).*")
            return

    try:
        # Create a new C88ZZ referral account registered under the user's main invite code
        new_mobile = await backend.create_account("pakistan", main_invite_code, proxy, password)
        await db.add_account(user_id, "pakistan", new_mobile, password, main_invite_code)
                
        status_msg = await safe_edit_message(
            status_msg,
            f"🔄 Account prepared. Requesting linking code for `{wa_phone}`...",
            parse_mode="Markdown"
        )
        
        # Start link and get pairing code
        client, session_id, pair_code = await backend.start_whatsapp_link(new_mobile, password, proxy, wa_phone)
        
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

@router.message(F.text == "📤 Withdraw")
async def cmd_withdraw(message: Message):
    user_id = message.from_user.id
    if not await check_user_access(user_id, message.from_user.username or "", message.from_user.first_name, message):
        return
        
    user = await db.get_user(user_id)
    main_mobile = user.get("main_mobile")
    proxy = user.get("proxy")
    password = user.get("custom_password") or "53561106@Roni"
    pay_method = user.get("payment_method")
    pay_details = user.get("payment_details")
    
    if not pay_method or not pay_details:
        await message.answer("❌ Please set your **Payment Method** first in **⚙️ Settings** before requesting a withdrawal.")
        return
        
    if not main_mobile:
        await message.answer("❌ Main account not registered. Please check your balance first to register.")
        return
        
    status_msg = await message.answer("🔄 Checking C88ZZ account balance...")
    try:
        balance_points_str = await backend.get_main_account_balance(main_mobile, password, proxy)
        try:
            balance_points = int(balance_points_str)
        except Exception:
            balance_points = 0
            
        if balance_points < 4000:
            usd_needed = (4000 * 0.05) / 278.0
            usd_current = (balance_points * 0.05) / 278.0
            await safe_edit_message(
                status_msg, 
                f"❌ **Withdrawal Failed**\n\n"
                f"Minimum withdrawal threshold is **4000 Points** (${usd_needed:.2f} USD).\n"
                f"Your current balance: **{balance_points} Points** (${usd_current:.2f} USD)."
            )
            return
            
        # Calculate USD
        usd_val = (float(balance_points) * 0.05) / 278.0
        
        # Insert withdrawal request in DB
        inserted = await db.add_withdrawal_request(user_id, balance_points, usd_val, pay_method, pay_details)
        
        # Get inserted request ID
        wd_id = 0
        if inserted and isinstance(inserted, list) and len(inserted) > 0:
            wd_id = inserted[0].get("id", 0)
            
        # Notify admin bot
        if config.ADMIN_USER_ID != 0:
            try:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Approve Withdrawal ✅", callback_data=f"wd_approve_{wd_id}"),
                     InlineKeyboardButton(text="Reject Withdrawal ❌", callback_data=f"wd_reject_{wd_id}")]
                ])
                await bot.send_message(
                    config.ADMIN_USER_ID,
                    f"📤 **New Withdrawal Request!**\n\n"
                    f"User ID: `{user_id}`\n"
                    f"Name: {message.from_user.first_name}\n"
                    f"Username: @{message.from_user.username or 'None'}\n"
                    f"Amount: `{balance_points} Points` (${usd_val:.2f} USD)\n"
                    f"Payment Method: **{pay_method}**\n"
                    f"Payment Details: `{pay_details}`",
                    reply_markup=kb
                )
            except Exception as admin_err:
                logger.error(f"Failed to notify admin on withdrawal: {admin_err}")
                
        await safe_edit_message(
            status_msg,
            f"✅ **Withdrawal Request Submitted!**\n\n"
            f"Requested: `{balance_points} Points` (${usd_val:.2f} USD)\n"
            f"Method: **{pay_method}**\n"
            f"Details: `{pay_details}`\n\n"
            f"⏳ Your request is currently pending admin approval and processing."
        )
        
    except Exception as e:
        logger.error(f"Withdrawal request failed: {e}")
        await safe_edit_message(status_msg, f"❌ Failed to request withdrawal: {str(e)}")

@router.callback_query(F.data.startswith("wd_approve_"))
async def approve_withdrawal_cb(cq: CallbackQuery):
    if cq.from_user.id != config.ADMIN_USER_ID:
        return
    wd_id = int(cq.data.split("_")[2])
    
    wd = await db.get_withdrawal_by_id(wd_id)
    if not wd:
        await safe_answer_callback(cq, "❌ Request not found.", show_alert=True)
        return
        
    if wd.get('status') != 'pending':
        await safe_answer_callback(cq, f"❌ Request already {wd.get('status')}.", show_alert=True)
        return
        
    await db.update_withdrawal_status(wd_id, "approved")
    await cq.message.edit_text(cq.message.text + "\n\n✅ Withdrawal Approved.")
    
    try:
        await bot.send_message(
            wd.get('user_id'), 
            f"🎉 **Withdrawal Approved!**\n\n"
            f"Your request for `{wd.get('amount_points')} Points` (${wd.get('amount_usd')} USD) via **{wd.get('payment_method')}** has been approved and paid by the Admin!"
        )
    except Exception as notify_err:
        logger.error(f"Failed to notify user on withdrawal approval: {notify_err}")
    await safe_answer_callback(cq, "Withdrawal approved.")

@router.callback_query(F.data.startswith("wd_reject_"))
async def reject_withdrawal_cb(cq: CallbackQuery):
    if cq.from_user.id != config.ADMIN_USER_ID:
        return
    wd_id = int(cq.data.split("_")[2])
    
    wd = await db.get_withdrawal_by_id(wd_id)
    if not wd:
        await safe_answer_callback(cq, "❌ Request not found.", show_alert=True)
        return
        
    if wd.get('status') != 'pending':
        await safe_answer_callback(cq, f"❌ Request already {wd.get('status')}.", show_alert=True)
        return
        
    await db.update_withdrawal_status(wd_id, "rejected")
    await cq.message.edit_text(cq.message.text + "\n\n❌ Withdrawal Rejected.")
    
    try:
        await bot.send_message(
            wd.get('user_id'), 
            f"❌ **Withdrawal Rejected**\n\n"
            f"Your request for `{wd.get('amount_points')} Points` (${wd.get('amount_usd')} USD) via **{wd.get('payment_method')}** was rejected by the Admin."
        )
    except Exception as notify_err:
        logger.error(f"Failed to notify user on withdrawal rejection: {notify_err}")
    await safe_answer_callback(cq, "Withdrawal rejected.")

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
