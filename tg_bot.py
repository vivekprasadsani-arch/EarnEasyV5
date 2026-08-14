import asyncio
import logging
import re
import json
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import (Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, 
                           InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, 
                           ForceReply, InputMediaPhoto, BotCommand)
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
BD_TZ = ZoneInfo("Asia/Dhaka")

class BotStates(StatesGroup):
    waiting_for_invite = State()
    waiting_for_proxy = State()
    waiting_for_password = State()
    waiting_for_whatsapp_number = State()

COUNTRIES = {
    "pakistan": "🇵🇰 Pakistan"
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
        BotCommand(command="start", description="Open the main menu"),
        BotCommand(command="setpassword", description="Set your default account password"),
    ])


async def safe_edit_message(message: Message, text: str, parse_mode: str = None):
    try:
        await message.edit_text(text, parse_mode=parse_mode)
        return message
    except Exception:
        return await message.answer(text, parse_mode=parse_mode)

async def safe_delete_message(message: Message):
    try:
        await message.delete()
    except Exception:
        pass

async def safe_answer_callback(cq: CallbackQuery, text: str = None, show_alert: bool = False):
    try:
        await cq.answer(text=text, show_alert=show_alert)
    except Exception:
        pass

def format_bd_datetime(value: str) -> str:
    if not value:
        return "Unknown"
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(BD_TZ).strftime("%d %b %Y, %I:%M %p")
    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone(BD_TZ).strftime("%d %b %Y, %I:%M %p")
    except ValueError:
        return value

def parse_bd_datetime(value: str):
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return dt.astimezone(BD_TZ)
    try:
        dt = datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt.astimezone(BD_TZ)
    except ValueError:
        return None

def format_bd_group_label(dt):
    if not dt:
        return "Unknown Date"
    today = datetime.now(BD_TZ).date()
    target = dt.date()
    if target == today:
        return f"Today - {dt.strftime('%d %b %Y')}"
    if target == today - timedelta(days=1):
        return f"Yesterday - {dt.strftime('%d %b %Y')}"
    return dt.strftime("%d %b %Y")

async def check_user_access(user_id: int, username: str, first_name: str, message_to_reply=None) -> bool:
    if user_id == config.ADMIN_USER_ID:
        # Admin is instantly approved
        user = await db.get_user(user_id)
        if not user:
            await db.add_or_update_user(user_id, username, first_name, status="approved")
        return True

    user = await db.get_user(user_id)
    if not user:
        await db.add_or_update_user(user_id, username, first_name, status="pending")
        if config.ADMIN_USER_ID != 0:
            try:
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Approve ✅", callback_data=f"approve_{user_id}"),
                     InlineKeyboardButton(text="Reject ❌", callback_data=f"reject_{user_id}")]
                ])
                await bot.send_message(
                    config.ADMIN_USER_ID, 
                    f"New user request:\nID: {user_id}\nName: {first_name}\nUsername: @{username}",
                    reply_markup=kb
                )
            except Exception as e:
                logger.error(f"Failed to notify admin: {e}")
                
        if message_to_reply:
            if isinstance(message_to_reply, Message):
                await message_to_reply.answer("⏳ Your account is pending admin approval. Please wait.")
            elif isinstance(message_to_reply, CallbackQuery):
                await message_to_reply.answer("⏳ Account pending approval.", show_alert=True)
        return False
        
    if user['status'] == 'rejected':
        if message_to_reply:
            if isinstance(message_to_reply, Message):
                await message_to_reply.answer("❌ Your account request was rejected.")
            elif isinstance(message_to_reply, CallbackQuery):
                await message_to_reply.answer("❌ Account rejected.", show_alert=True)
        return False
        
    if user['status'] == 'pending':
        if message_to_reply:
            if isinstance(message_to_reply, Message):
                await message_to_reply.answer("⏳ Your account is still pending admin approval. Please wait.")
            elif isinstance(message_to_reply, CallbackQuery):
                await message_to_reply.answer("⏳ Account pending.", show_alert=True)
        return False
        
    return True

@router.message(CommandStart())
async def cmd_start(message: Message):
    has_access = await check_user_access(
        message.from_user.id, 
        message.from_user.username or "", 
        message.from_user.first_name or "", 
        message
    )
    if has_access:
        await message.answer("🎉 Welcome! Setup your WhatsApp connections safely and easily.", reply_markup=main_keyboard())

@router.callback_query(F.data.startswith("approve_"))
async def approve_user(cq: CallbackQuery):
    if cq.from_user.id != config.ADMIN_USER_ID:
        return
    uid = int(cq.data.split("_")[1])
    await db.update_user_status(uid, "approved")
    await cq.message.edit_text(cq.message.text + "\n\n✅ Approved.")
    try:
        await bot.send_message(uid, "🎉 Your account has been approved! Use the menu below.", reply_markup=main_keyboard())
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
    
    # Filter only linked accounts
    linked_accounts = [a for a in accounts if a['is_linked']]
    
    if not linked_accounts:
        await cq.message.edit_text(f"📉 You have no successfully linked accounts for {COUNTRIES[country_code]}.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="back_my_account")]]))
        await safe_answer_callback(cq)
        return
        
    # Aggregate counts per email and keep the latest linked timestamp
    email_stats = {}
    for a in linked_accounts:
        email = a['email']
        stats = email_stats.setdefault(email, {"count": 0, "latest_at": ""})
        stats["count"] += 1
        created_at = str(a["created_at"] or "")
        if created_at > stats["latest_at"]:
            stats["latest_at"] = created_at
        
    text = f"👤 **Linked Accounts for {COUNTRIES[country_code]}**\n"
    text += f"📊 Total Links: {len(linked_accounts)} | Unique Emails: {len(email_stats)}\n"
    text += "🕒 Times shown in Bangladesh time (UTC+6)\n\n"
    
    # Sort by latest activity first, then by highest link count
    sorted_emails = sorted(
        email_stats.items(),
        key=lambda x: (x[1]["latest_at"], x[1]["count"]),
        reverse=True,
    )

    grouped_emails = {}
    for email, stats in sorted_emails[:20]:
        latest_dt = parse_bd_datetime(stats["latest_at"])
        group_label = format_bd_group_label(latest_dt)
        grouped_emails.setdefault(group_label, []).append((email, stats, latest_dt))

    serial = 1
    for group_label, items in grouped_emails.items():
        text += f"**{group_label}**\n"
        for email, stats, latest_dt in items:
            latest_text = latest_dt.strftime("%I:%M %p") if latest_dt else format_bd_datetime(stats["latest_at"])
            text += f"{serial}. ✅ `{email}` 🔗 **({stats['count']} links)**\n"
            text += f"   🕒 `{latest_text}`\n"
            serial += 1
        text += "\n"
        
    if len(sorted_emails) > 20:
        text += f"\n_...and {len(sorted_emails) - 20} more emails_"
        
    await cq.message.edit_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="back_my_account")]]))
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
        [InlineKeyboardButton(text="SAS Method (Single Account)", callback_data="method_sas")],
        [InlineKeyboardButton(text="MAR Method (Rotation)", callback_data="method_mar")]
    ])
    await cq.message.edit_text(f"Region selected: {COUNTRIES[country_code]}\n\nPlease select the registration method:", reply_markup=kb)
    await safe_answer_callback(cq)

@router.callback_query(F.data.startswith("method_"))
async def ask_invite_code(cq: CallbackQuery, state: FSMContext):
    method = cq.data.split("_")[1] # sas or mar
    await state.update_data(method=method)
    
    await cq.message.answer("📝 Please enter your Invite Code:", reply_markup=ForceReply())
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
        await message.answer("❌ Error: Region or Method lost. Please start over using 'Add WhatsApp'.")
        await state.clear()
        return

    invite_codes = re.findall(r'[a-zA-Z0-9]{5,12}', text)
    if not invite_codes:
        invite_codes = [text]

    code = invite_codes[-1]
    await state.update_data(invite_code=code)
    
    await message.answer("📱 Please enter the WhatsApp number you want to link (e.g. +923XXXXXXXXX):", reply_markup=ForceReply())
    await state.set_state(BotStates.waiting_for_whatsapp_number)

@router.message(BotStates.waiting_for_whatsapp_number)
async def process_whatsapp_number(message: Message, state: FSMContext):
    wa_phone = message.text.strip()
    cleaned_phone = "".join(c for c in wa_phone if c.isdigit() or c == '+')
    if not cleaned_phone:
        await message.answer("❌ Invalid phone number. Please try again:")
        return
        
    data = await state.get_data()
    country_code = data.get("country_code")
    method = data.get("method")
    invite_code = data.get("invite_code")
    
    await state.set_state(None) # Clear state to allow other operations
    
    asyncio.create_task(
        start_pairing_flow(
            message,
            state=state,
            country_code=country_code,
            method=method,
            invite_code=invite_code,
            user_id=message.from_user.id,
            wa_phone=cleaned_phone
        )
    )

async def start_pairing_flow(message: Message, state: FSMContext, country_code: str, method: str, invite_code: str, user_id: int, wa_phone: str, message_to_edit: Message = None):
    if message_to_edit:
        try:
            await message_to_edit.delete()
        except:
            pass
            
    status_msg = await message.answer(f"🔄 Preparing account for {COUNTRIES.get(country_code, country_code)}... Please wait.")
    
    user_data = await db.get_user(user_id)
    proxy = user_data['proxy']
    password = user_data['custom_password'] or config.DEFAULT_PASSWORD
    
    try:
        username = None
        if method == "sas" and state:
            data = await state.get_data()
            username = data.get("current_email")
            
        if not username:
            # Create a new C88ZZ account using generated mobile
            username = await backend.create_account(country_code, invite_code, proxy, password)
            await db.add_account(user_id, country_code, username, password, invite_code)
            if state:
                await state.update_data(current_email=username)
                
        status_msg = await safe_edit_message(
            status_msg,
            f"🔄 Account prepared `({username})`. Requesting linking code for `{wa_phone}`...",
            parse_mode="Markdown"
        )
        
        # Start link and get pairing code
        client, session_id, pair_code = await backend.start_whatsapp_link(username, password, proxy, wa_phone)
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Copy Code", switch_inline_query=pair_code)]
        ])
        
        sent_msg = await message.answer(
            f"📱 **WhatsApp Link Code Ready**\n\n"
            f"Account: `{username}`\n"
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
                state,
                client,
                session_id,
                invite_code,
                username,
                method,
                country_code
            )
        )
        
    except Exception as e:
        logger.error(f"Error starting pairing: {e}")
        await safe_edit_message(status_msg, f"❌ Error during account linking.\n\n`{str(e)}`", parse_mode="Markdown")

async def poll_for_success(message: Message, state: FSMContext, client, session_id, invite_code, email, method, country_code):
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
                    
                    buttons = []
                    if method == "sas":
                        buttons.append([
                            InlineKeyboardButton(text="Next ➡️ (Same Account)", callback_data=f"next_sas_{country_code}")
                        ])
                    elif method == "mar":
                        buttons.append([
                            InlineKeyboardButton(text="Next ➡️ (New Account)", callback_data=f"next_mar_{country_code}")
                        ])
                    
                    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
                    
                    await message.edit_text(
                        f"✅ **Success!**\n\nWhatsApp has been successfully linked!\nAccount used: `{email}`\n\nUse the Next button to continue.", 
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
    country_code = "_join" if parts[2] == "join" else "_".join(parts[2:]) # handle next_sas_pakistan correctly
    if country_code == "_join":
        country_code = "_".join(parts[2:])
        
    await state.update_data(method=method, country_code=country_code)
    await safe_answer_callback(cq)
    
    data = await state.get_data()
    invite_code = data.get("invite_code")
    email = data.get("current_email")
    
    if method == "mar" and not invite_code:
        msg = await cq.message.answer("📝 We lost the session invite code. Enter Invite Code:", reply_markup=ForceReply())
        await state.update_data(prompt_msg_id=msg.message_id) 
        await state.set_state(BotStates.waiting_for_invite)
        return
        
    if method == "sas" and (not invite_code or not email):
        msg = await cq.message.answer("📝 Session lost. Starting over. Enter NEW Invite Code:", reply_markup=ForceReply())
        await state.update_data(prompt_msg_id=msg.message_id) 
        await state.set_state(BotStates.waiting_for_invite)
        return
        
    await cq.message.answer("📱 Please enter the WhatsApp number you want to link (e.g. +923XXXXXXXXX):", reply_markup=ForceReply())
    await state.set_state(BotStates.waiting_for_whatsapp_number)
    try:
        await cq.message.delete()
    except:
        pass

@router.message(F.text.regexp(r"^\+?[0-9]{10,13}$"))
async def handle_pasted_email(message: Message, state: FSMContext):
    if not await check_user_access(message.from_user.id, message.from_user.username or "", message.from_user.first_name, message):
        return
        
    email = message.text.strip()
    if email.startswith("+"):
        email = email[1:]
        
    # Check if this mobile exists in the user's accounts
    account = await db.get_latest_account_by_email(message.from_user.id, email)

    if not account:
        return # Not a known account for this user
        
    country_code = account['site_id']
    invite_code = account['invite_code']
    
    # We found the account, now set up the FSM to act like SAS next
    await state.update_data(
        method="sas",
        country_code=country_code,
        invite_code=invite_code,
        current_email=email
    )
    
    await message.answer("📱 Please enter the WhatsApp number you want to link (e.g. +923XXXXXXXXX):", reply_markup=ForceReply())
    await state.set_state(BotStates.waiting_for_whatsapp_number)

async def main():
    await db.init_db()
    await setup_bot_commands()
    dp.include_router(router)
    # Start bot
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
