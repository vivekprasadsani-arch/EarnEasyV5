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
from aiogram.filters import CommandStart, Command, StateFilter

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
    waiting_for_password = State()
    waiting_for_invite = State()
    waiting_for_whatsapp_number = State()

COUNTRIES = {
    "pakistan": "🇵🇰 Pakistan",
    "pakistan_2": "🇵🇰 Pakistan 2"
}

def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Add WhatsApp")],
            [KeyboardButton(text="👤 My Account"), KeyboardButton(text="⚙️ Settings")]
        ],
        resize_keyboard=True,
        is_persistent=True
    )

async def setup_bot_commands():
    await bot.set_my_commands([
        BotCommand(command="start", description="Start the bot"),
        BotCommand(command="setpassword", description="Set your custom default account password"),
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
        # Save user to DB as pending immediately so they have a database row
        await db.add_or_update_user(user_id, username, first_name, status="pending")
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
                    f"New user registration request:\nID: {user_id}\nName: {first_name}\nUsername: @{username}",
                    reply_markup=kb
                )
            except Exception as admin_err:
                logger.error(f"Failed to notify admin on start: {admin_err}")
        
        await message.answer(
            f"⏳ **Welcome!** Your registration request has been submitted.\n\n"
            f"Your account is currently pending admin approval. You will be notified once approved."
        )
        return
        
    has_access = await check_user_access(user_id, username, first_name, message)
    if has_access:
        await message.answer("🎉 Welcome back! Select an option below to get started.", reply_markup=main_keyboard())

@router.message(Command("setpassword"))
async def cmd_setpassword(message: Message, state: FSMContext):
    if not await check_user_access(message.from_user.id, message.from_user.username or "", message.from_user.first_name, message):
        return
    await message.answer("🔑 Enter your new custom default password for accounts:")
    await state.set_state(BotStates.waiting_for_password)

@router.message(BotStates.waiting_for_password)
async def process_password(message: Message, state: FSMContext):
    password = message.text.strip()
    await db.set_user_password(message.from_user.id, password)
    await message.answer("✅ Custom password saved successfully!", reply_markup=main_keyboard())
    await state.clear()

@router.message(StateFilter(None), F.text, ~F.text.in_({"⚙️ Settings", "👤 My Account", "📱 Add WhatsApp"}))
async def handle_text_username_lookup(message: Message, state: FSMContext):
    if not await check_user_access(message.from_user.id, message.from_user.username or "", message.from_user.first_name, message):
        return
        
    text = message.text.strip()
    cleaned = "".join(c for c in text if c.isdigit())
    if not cleaned or len(cleaned) < 5:
        await message.answer("❓ I didn't understand that. Please use the menu buttons below.", reply_markup=main_keyboard())
        return

    # Fetch all accounts to see if this username matches
    accounts = await db.get_all_accounts(message.from_user.id)
    matched_accs = []
    for acc in accounts:
        acc_email = acc.get("email", "")
        acc_cleaned = "".join(c for c in acc_email if c.isdigit())
        if cleaned in acc_cleaned or acc_cleaned in cleaned:
            matched_accs.append(acc)
            
    if not matched_accs:
        await message.answer(
            "❌ This username is not registered under your account. "
            "Please register it first by clicking **Add WhatsApp**.",
            reply_markup=main_keyboard()
        )
        return
        
    if len(matched_accs) == 1:
        acc = matched_accs[0]
        email = acc.get("email")
        country_code = acc.get("site_id", "pakistan")
        own_invite_code = acc.get("own_invite_code")
        account_id = acc.get("id")
        
        await state.update_data(
            current_email=email,
            country_code=country_code,
            own_invite_code=own_invite_code,
            account_id=account_id,
            method="sas"
        )
        
        await message.answer(
            f"✅ **Account Found!** ({COUNTRIES.get(country_code, country_code)})\n\n"
            f"👤 **Username:** `{email}`\n"
            f"🔑 **Invite Code:** `{own_invite_code or 'Pending'}`\n\n"
            f"📱 Please enter the WhatsApp number you want to link (e.g. +923XXXXXXXXX):",
            reply_markup=ForceReply(),
            parse_mode="Markdown"
        )
        await state.set_state(BotStates.waiting_for_whatsapp_number)
    else:
        buttons = []
        for acc in matched_accs:
            cc = acc.get("site_id", "pakistan")
            buttons.append([InlineKeyboardButton(text=f"Link to {COUNTRIES.get(cc, cc)}", callback_data=f"fastlink_{acc.get('id')}")])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(
            f"✅ **Multiple Accounts Found!**\n\n"
            f"We found `{len(matched_accs)}` sites registered with this username.\n"
            f"Please select which site you want to link a WhatsApp number to:",
            reply_markup=kb,
            parse_mode="Markdown"
        )

@router.callback_query(F.data.startswith("fastlink_"))
async def fast_link_account(cq: CallbackQuery, state: FSMContext):
    acc_id = int(cq.data.replace("fastlink_", ""))
    acc = await db.get_account_by_id(acc_id)
    if not acc:
        await safe_answer_callback(cq, "Account not found.", show_alert=True)
        return
        
    email = acc.get("email")
    country_code = acc.get("site_id", "pakistan")
    own_invite_code = acc.get("own_invite_code")
    
    await state.update_data(
        current_email=email,
        country_code=country_code,
        own_invite_code=own_invite_code,
        account_id=acc_id,
        method="sas"
    )
    
    await cq.message.edit_text(
        f"✅ **Account Selected!** ({COUNTRIES.get(country_code, country_code)})\n\n"
        f"👤 **Username:** `{email}`\n"
        f"🔑 **Invite Code:** `{own_invite_code or 'Pending'}`\n\n"
        f"📱 Please enter the WhatsApp number you want to link (e.g. +923XXXXXXXXX):",
        parse_mode="Markdown"
    )
    await state.set_state(BotStates.waiting_for_whatsapp_number)
    await safe_answer_callback(cq)

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
        [InlineKeyboardButton(text="🌐 Set Proxy", callback_data="set_proxy")]
    ]
    if user['proxy']:
        kb_buttons.append([InlineKeyboardButton(text="Test Proxy Connection", callback_data="test_proxy")])
        
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)
    await message.answer(
        f"⚙️ **Settings**\n\n"
        f"🌐 **Current Proxy:** `{proxy}`",
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

# Removed old payment callbacks

@router.message(F.text == "👤 My Account")
async def my_account_menu(message: Message):
    if not await check_user_access(message.from_user.id, message.from_user.username or "", message.from_user.first_name, message):
        return
    
    buttons = [
        [InlineKeyboardButton(text=name, callback_data=f"my_account_{code}")]
        for code, name in COUNTRIES.items()
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("👤 Select a region to view your generated accounts:", reply_markup=kb)

@router.callback_query(F.data.startswith("my_account_"))
async def my_account_detail(cq: CallbackQuery):
    if not await check_user_access(cq.from_user.id, cq.from_user.username or "", cq.from_user.first_name, cq):
        return
    country_code = cq.data.replace("my_account_", "")
    accounts = await db.get_accounts_by_site(cq.from_user.id, country_code)
    
    if not accounts:
        await cq.message.edit_text(
            f"📉 You have no accounts registered for {COUNTRIES.get(country_code, country_code)}.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="back_my_account")]])
        )
        await safe_answer_callback(cq)
        return
        
    total_accs = len(accounts)
    online_accs = sum(1 for a in accounts if a.get("is_linked") is True)
    offline_accs = total_accs - online_accs
    
    text = f"👤 **My Accounts for {COUNTRIES.get(country_code, country_code)}**\n\n"
    text += f"📊 **Total Accounts:** `{total_accs}`\n"
    text += f"🟢 **Online:** `{online_accs}`\n"
    text += f"🔴 **Offline:** `{offline_accs}`\n\n"
    text += "**Account List:**\n"
    
    serial = 1
    for a in accounts:
        status_emoji = "🟢" if a.get("is_linked") else "🔴"
        text += f"{serial}. {status_emoji} `{a.get('email')}` (Code: `{a.get('own_invite_code') or 'Pending'}`)\n"
        serial += 1
        
    await cq.message.edit_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="back_my_account")]])
    )
    await safe_answer_callback(cq)

@router.callback_query(F.data == "back_my_account")
async def back_my_account(cq: CallbackQuery):
    buttons = [
        [InlineKeyboardButton(text=name, callback_data=f"my_account_{code}")]
        for code, name in COUNTRIES.items()
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await cq.message.edit_text("👤 Select a region to view your generated accounts:", reply_markup=kb)
    await safe_answer_callback(cq)

# MAIN ADD WHATSAPP FLOW
@router.message(F.text == "📱 Add WhatsApp")
async def add_whatsapp_menu(message: Message):
    if not await check_user_access(message.from_user.id, message.from_user.username or "", message.from_user.first_name, message):
        return
    buttons = [
        [InlineKeyboardButton(text=name, callback_data=f"add_country_{code}")]
        for code, name in COUNTRIES.items()
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer("📱 Select the region to add a WhatsApp number:", reply_markup=kb)

@router.callback_query(F.data.startswith("add_country_"))
async def select_method(cq: CallbackQuery, state: FSMContext):
    if not await check_user_access(cq.from_user.id, cq.from_user.username or "", cq.from_user.first_name, cq):
        return
    country_code = cq.data.replace("add_country_", "")
    await state.update_data(country_code=country_code)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="SAS Method (Same Account)", callback_data="method_sas")],
        [InlineKeyboardButton(text="MAR Method (New Account)", callback_data="method_mar")]
    ])
    await cq.message.edit_text(f"Region selected: {COUNTRIES[country_code]}\n\nPlease select the registration method:", reply_markup=kb)
    await safe_answer_callback(cq)

@router.callback_query(F.data.startswith("method_"))
async def ask_invite_code(cq: CallbackQuery, state: FSMContext):
    method = cq.data.split("_")[1] # sas or mar
    await state.update_data(method=method)
    
    await cq.message.answer("📝 Please enter your Refer Code:", reply_markup=ForceReply())
    await state.set_state(BotStates.waiting_for_invite)
        
    await cq.message.delete()
    await safe_answer_callback(cq)

@router.message(BotStates.waiting_for_invite)
async def process_invite(message: Message, state: FSMContext):
    text = message.text.strip()
    data = await state.get_data()
    method = data.get("method")
    country_code = data.get("country_code")
    
    if not country_code or not method:
        await bot.send_message(chat_id=message.chat.id, text="❌ Error: Region or Method lost. Please start over using 'Add WhatsApp'.")
        await state.clear()
        return

    # Extract all possible codes
    invite_codes = re.findall(r'\w+', text)
    if not invite_codes:
        invite_codes = [text]
    
    invite_code = invite_codes[-1]
    await state.update_data(invite_code=invite_code)
    
    # Process registration
    user_id = message.from_user.id
    user_data = await db.get_user(user_id)
    proxy = user_data.get('proxy')
    password = user_data.get('custom_password') or "53561106@Roni"
    
    status_msg = await bot.send_message(chat_id=message.chat.id, text=f"🔄 Preparing account for {COUNTRIES.get(country_code, country_code)}... Please wait.")
    
    email = None
    own_invite_code = None
    account_id = None
    
    if method == "sas":
        # SAS: Same Account, New WhatsApp Link (re-uses existing email/mobile for this country if exists)
        latest_acc = await db.get_latest_account_by_site(user_id, country_code)
        if latest_acc:
            email = latest_acc.get("email") # stored in email column
            own_invite_code = latest_acc.get("own_invite_code")
            account_id = latest_acc.get("id")
            # If own_invite_code is not in DB, try to fetch it via login
            if not own_invite_code:
                try:
                    if country_code == "pakistan_2":
                        from bot_requests import DostWaClient
                        client = DostWaClient(proxy_url=proxy)
                        try:
                            login_res = client.login(email, password)
                            if login_res.get("code") == 200:
                                info_res = client.user_info()
                                own_invite_code = info_res.get("data", {}).get("user", {}).get("inviteCode")
                        finally:
                            client.close()
                    else:
                        client = backend.C88ZZClient(proxy_url=proxy)
                        try:
                            login_res = client.login(email, password)
                            if login_res.get("code") == 200:
                                invite_info_res = client.user_invite_info()
                                invite_data = invite_info_res.get("data", {})
                                own_invite_code = invite_data.get("invite_code") or invite_data.get("code")
                                if not own_invite_code:
                                    info_res = client.user_info()
                                    own_invite_code = info_res.get("data", {}).get("invite_code")
                        finally:
                            client.close()
                    if own_invite_code:
                        await db.update_account_own_invite_code(latest_acc.get("id"), own_invite_code)
                except Exception as e:
                    logger.error(f"Failed to fetch SAS own invite: {e}")
            
    if not email:
        # Create a new account (branched by site)
        try:
            if country_code == "pakistan_2":
                email = await backend.dostwa_create_account(invite_code, proxy, password)
                # Log in and fetch own invite code
                try:
                    from bot_requests import DostWaClient
                    client = DostWaClient(proxy_url=proxy)
                    try:
                        login_res = client.login(email, password)
                        if login_res.get("code") == 200:
                            info_res = client.user_info()
                            own_invite_code = info_res.get("data", {}).get("user", {}).get("inviteCode")
                    finally:
                        client.close()
                except Exception as e:
                    logger.error(f"Failed to fetch DostWa own invite: {e}")
            else:
                email = await backend.create_account(country_code, invite_code, proxy, password)
                # Log in and fetch own invite code
                try:
                    client = backend.C88ZZClient(proxy_url=proxy)
                    try:
                        login_res = client.login(email, password)
                        if login_res.get("code") == 200:
                            invite_info_res = client.user_invite_info()
                            invite_data = invite_info_res.get("data", {})
                            own_invite_code = invite_data.get("invite_code") or invite_data.get("code")
                            if not own_invite_code:
                                info_res = client.user_info()
                                own_invite_code = info_res.get("data", {}).get("invite_code")
                    finally:
                        client.close()
                except Exception as e:
                    logger.error(f"Failed to fetch own invite: {e}")
                
            account_id = await db.add_account(user_id, country_code, email, password, invite_code)
            if own_invite_code and account_id:
                await db.update_account_own_invite_code(account_id, own_invite_code)
        except Exception as e:
            logger.error(f"Account registration failed: {e}")
            await safe_edit_message(status_msg, f"❌ Failed to register account: {str(e)}\n\n*(Hint: If you need a proxy to register, please check settings).*")
            await state.clear()
            return
            
    # Save current account email in state
    await state.update_data(current_email=email, own_invite_code=own_invite_code, account_id=account_id)
    
    # Prompt for WhatsApp number
    await safe_edit_message(
        status_msg,
        f"✅ **Account Prepared!**\n\n"
        f"👤 **Username:** `{email}`\n"
        f"🔑 **Invite Code:** `{own_invite_code or 'Pending'}`\n\n"
        f"📱 Please enter the WhatsApp number you want to link (e.g. +923XXXXXXXXX):",
        parse_mode="Markdown"
    )
    await state.set_state(BotStates.waiting_for_whatsapp_number)

@router.message(BotStates.waiting_for_whatsapp_number)
async def process_whatsapp_number(message: Message, state: FSMContext):
    wa_phone = message.text.strip()
    cleaned_phone = "".join(c for i, c in enumerate(wa_phone) if c.isdigit() or (c == '+' and i == 0))
    if not cleaned_phone:
        await message.answer("❌ Invalid phone number. Please try again:")
        return
        
    data = await state.get_data()
    email = data.get("current_email")
    country_code = data.get("country_code")
    method = data.get("method")
    own_invite_code = data.get("own_invite_code")
    account_id = data.get("account_id")
    invite_code = data.get("invite_code")
    
    await state.clear()
    
    asyncio.create_task(
        start_pairing_flow(
            message,
            user_id=message.from_user.id,
            email=email,
            country_code=country_code,
            method=method,
            own_invite_code=own_invite_code,
            invite_code=invite_code,
            wa_phone=cleaned_phone,
            account_id=account_id
        )
    )

async def start_pairing_flow(message: Message, user_id: int, email: str, country_code: str, method: str, own_invite_code: str, invite_code: str, wa_phone: str, account_id: int):
    status_msg = await message.answer(f"🔄 Requesting linking code for `{wa_phone}`... Please wait.")
    
    user_data = await db.get_user(user_id)
    proxy = user_data.get('proxy')
    password = user_data.get("custom_password") or "53561106@Roni"
    
    try:
        # Start link and get pairing code (branched by site)
        if country_code == "pakistan_2":
            client, session_id, pair_code = await backend.dostwa_start_whatsapp_link(email, password, proxy, wa_phone)
        else:
            client, session_id, pair_code = await backend.start_whatsapp_link(country_code, email, password, proxy, wa_phone)
        
        # Save session_id in database
        if account_id:
            await db.update_account_session_id(account_id, session_id)
            
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Copy Code", switch_inline_query=pair_code)]
        ])
        
        sent_msg = await message.answer(
            f"📱 **WhatsApp Link Code Ready**\n\n"
            f"👤 **Username:** `{email}`\n"
            f"🔑 **Invite Code:** `{own_invite_code or 'Pending'}`\n"
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
                email,
                country_code,
                method,
                account_id,
                invite_code
            )
        )
        
    except Exception as e:
        logger.error(f"Error starting pairing: {e}")
        await safe_edit_message(status_msg, f"❌ Error during account linking.\n\n`{str(e)}`", parse_mode="Markdown")

async def poll_for_success(message: Message, client, session_id, email, country_code, method, account_id, invite_code):
    user_id = message.chat.id
    try:
        for _ in range(60): # Poll for max length (roughly 2 minutes)
            await asyncio.sleep(2)
            if country_code == "pakistan_2":
                res = await backend.dostwa_poll_wa_status(client, session_id)
            else:
                res = await backend.poll_wa_status(client, session_id)
            
            if res.get("code") == 200:
                res_data = res.get("data", {})
                status = int(res_data.get("login_status", 0))
                
                if status == 2:
                    await db.mark_account_linked(user_id, country_code, email)
                    
                    # Always register a new account for linking the next number
                    buttons = [
                        [InlineKeyboardButton(text="Next ➡️", callback_data=f"next_mar_{country_code}_{invite_code}")]
                    ]
                    
                    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
                    
                    await message.edit_text(
                        f"✅ **Success!**\n\n"
                        f"WhatsApp has been successfully linked to `{email}`!\n\n"
                        f"Use the Next button to continue.", 
                        parse_mode="Markdown",
                        reply_markup=kb
                    )
                    return
    finally:
        try:
            client.close()
        except Exception:
            pass

@router.callback_query(F.data.startswith("next_"))
async def handle_next_action(cq: CallbackQuery, state: FSMContext):
    parts = cq.data.split("_")
    method = parts[1]
    
    if method == "mar":
        country_code = parts[2]
        invite_code = parts[3]
        
        await state.update_data(method=method, country_code=country_code, invite_code=invite_code)
        await safe_answer_callback(cq)
        
        # Trigger the process invite flow again to register a new account
        mock_msg = Message(
            message_id=cq.message.message_id,
            date=cq.message.date,
            chat=cq.message.chat,
            from_user=cq.from_user,
            text=invite_code,
        ).as_(bot)
        await process_invite(mock_msg, state)
        
    elif method == "sas":
        country_code = parts[2]
        email = parts[3]
        
        # Retrieve account details from DB to get the invite_code used
        latest_acc = await db.get_latest_account_by_site(cq.from_user.id, country_code)
        invite_code = "K7MBKZ"  # Default fallback
        if latest_acc:
            invite_code = latest_acc.get("invite_code") or "K7MBKZ"
            
        # Switch method to "mar" so it creates a new account instead of reusing the linked one
        await state.update_data(method="mar", country_code=country_code, invite_code=invite_code)
        await safe_answer_callback(cq, "🔄 Registering new account for next link...", show_alert=False)
        try:
            await cq.message.delete()
        except:
            pass
            
        # Trigger the process invite flow to register a new account
        mock_msg = Message(
            message_id=cq.message.message_id,
            date=cq.message.date,
            chat=cq.message.chat,
            from_user=cq.from_user,
            text=invite_code,
        ).as_(bot)
        await process_invite(mock_msg, state)

# Removed old withdrawal handlers

async def start_background_monitoring():
    """Background task to periodically check online status and keep sessions active."""
    logger.info("Background keep-alive monitor task initialized.")
    while True:
        try:
            accounts = await db.get_all_accounts_admin()
            logger.info(f"Monitor: Running status check & keep-alive for {len(accounts)} accounts...")
            for acc in accounts:
                username = acc.get("email") # stored in email column
                created_at_str = acc.get("created_at")
                
                # Filter accounts: only keep online / check status for accounts created in the last 36 hours
                is_recent = True
                if created_at_str:
                    try:
                        created_dt = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                        now = datetime.now(timezone.utc)
                        diff = now - created_dt
                        if diff > timedelta(hours=36):
                            is_recent = False
                    except Exception as parse_err:
                        logger.error(f"Monitor: Error parsing created_at for {username}: {parse_err}")
                
                if not is_recent:
                    continue
                
                password = acc.get("password") or "53561106@Roni"
                user_id = acc.get("user_id")
                user_data = await db.get_user(user_id)
                proxy = user_data.get("proxy") if user_data else None
                session_id = acc.get("session_id")
                acc_id = acc.get("id")
                # Check real-time WhatsApp status if session_id is available
                if session_id and acc_id:
                    try:
                        site_id = acc.get("site_id", "pakistan")
                        if site_id == "pakistan_2":
                            client = backend.DostWaClient(proxy_url=proxy)
                            try:
                                login_res = client.login(username, password)
                                if login_res.get("code") == 200:
                                    check_res = client.get_account_status(session_id)
                                    status = check_res.get("data", {}).get("status", "")
                                    is_online = status.lower() in ("bindok", "online", "connected")
                                    await db.update_account_linked_status(acc_id, is_online)
                                    logger.info(f"Monitor: Updated DostWa account {username} link status: {is_online}")
                            finally:
                                client.close()
                        else:
                            client = backend.C88ZZClient(proxy_url=proxy)
                            try:
                                login_res = client.login(username, password)
                                if login_res.get("code") == 200:
                                    check_res = client.whatsapp_check(session_id)
                                    is_online = check_res.get("data", {}).get("is_online", False)
                                    await db.update_account_linked_status(acc_id, is_online)
                                    logger.info(f"Monitor: Updated account {username} link status: {is_online}")
                            finally:
                                client.close()
                    except Exception as status_err:
                        logger.error(f"Monitor: Failed to check status for {username}: {status_err}")
                
                # Fresh login keep-alive
                if acc.get("site_id", "pakistan") == "pakistan_2":
                    await backend.dostwa_keep_alive(username, password, proxy)
                else:
                    await backend.keep_alive_account(username, password, proxy)
                await asyncio.sleep(2) # rate-limiting delay
        except Exception as e:
            logger.error(f"Monitor loop error: {e}")
        # Run every 4 hours
        await asyncio.sleep(4 * 3600)

async def main():
    await db.init_db()
    await setup_bot_commands()
    dp.include_router(router)
    
    # Start Web Server (Admin Panel) in the background
    import web_server
    asyncio.create_task(web_server.start_server())
    
    # Start background keep-alive task
    asyncio.create_task(start_background_monitoring())
    
    # Start bot polling
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
