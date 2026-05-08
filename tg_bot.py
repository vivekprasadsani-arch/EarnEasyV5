import asyncio
import logging
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
from bot_requests import normalize_proxy_url

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

COUNTRIES = {
    "india": "🇮🇳 India",
    "pakistan": "🇵🇰 Pakistan",
    "south_africa": "🇿🇦 South Africa",
    "nigeria": "🇳🇬 Nigeria"
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
        try:
            res = requests.get(
                "https://httpbin.org/ip",
                proxies={"http": proxy, "https": proxy},
                timeout=15,
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
    data = await state.get_data()
    
    if method == "mar":
        # MAR method uses the SAME invite code if the user already provided one recently or we can ask for a new one.
        # But we prompt.
        pass
        
    msg = await cq.message.answer("📝 Please enter your Invite Code:", reply_markup=ForceReply())
    await state.update_data(prompt_msg_id=msg.message_id) # Save for cleanup later
    await state.set_state(BotStates.waiting_for_invite)
    await cq.message.delete()
    await safe_answer_callback(cq)

@router.message(BotStates.waiting_for_invite)
async def process_invite(message: Message, state: FSMContext):
    invite_code = message.text.strip()
    data = await state.get_data()
    country_code = data.get("country_code")
    method = data.get("method")
    
    await state.update_data(invite_code=invite_code)
    await generate_and_send_qr(message, state, message.from_user.id)


async def generate_and_send_qr(message: Message, state: FSMContext, user_id: int, message_to_edit: Message = None):
    data = await state.get_data()
    country_code = data.get("country_code")
    method = data.get("method")
    invite_code = data.get("invite_code")
    
    if message_to_edit:
        try:
            # We can't easily edit a photo with just text without changing the media type, 
            # so we just delete it and send a new "Preparing..." text message.
            await message_to_edit.delete()
            status_msg = await message.answer(f"🔄 Preparing next account for {COUNTRIES[country_code]}...")
            is_photo = False
        except:
            status_msg = await message.answer(f"🔄 Preparing next account for {COUNTRIES[country_code]}...")
            is_photo = False
    else:
        status_msg = await message.answer(f"🔄 Preparing account for {COUNTRIES[country_code]}...")
        is_photo = False
    
    user_data = await db.get_user(user_id)
    proxy = user_data['proxy']
    password = user_data['custom_password'] or config.DEFAULT_PASSWORD
    
    try:
        # Create DeepEarn / Emailnator Account
        email = await backend.create_account(country_code, invite_code, proxy, password)
        await db.add_account(user_id, country_code, email, password, invite_code)
        
        # Save email in state for SAS method reuse
        await state.update_data(current_email=email)
        
        if is_photo:
            await status_msg.edit_caption(caption=f"🔄 Account created `({email})`! Generating QR...", parse_mode="Markdown")
        else:
            status_msg = await safe_edit_message(
                status_msg,
                f"🔄 Account created `({email})`! Generating QR...",
                parse_mode="Markdown",
            )
        
        # Generator QR
        walink_client, device_id, returned_invite_code, qr_bytes = await backend.generate_wa_qr(country_code, email, password, proxy)
        
        # Send QR code
        qr_file = BufferedInputFile(qr_bytes, filename="qr.png")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Copy Email", switch_inline_query=email),
             InlineKeyboardButton(text="📋 Copy Invite", switch_inline_query=returned_invite_code)],
            [InlineKeyboardButton(text="🔄 Regenerate QR", callback_data=f"regen_{country_code}_{email}_{returned_invite_code}")]
        ])
        
        sent_qr = await message.answer_photo(
            photo=qr_file,
            caption=f"📱 **QR Code Ready**\n\n**Email**: `{email}`\n**Invite Code**: `{returned_invite_code}`\n\nScan this QR with WhatsApp natively.",
            parse_mode="Markdown",
            reply_markup=kb
        )
        await safe_delete_message(status_msg)
        
        # Start Polling
        asyncio.create_task(poll_for_success(sent_qr, state, walink_client, device_id, returned_invite_code, email, method, country_code))
        await state.set_state(None) # Done with input FSM
        
    except Exception as e:
        logger.error(f"Error generating QR: {e}")
        if is_photo:
            await status_msg.edit_caption(caption=f"❌ Error during account creation.\n\n`{str(e)}`", parse_mode="Markdown")
        else:
            await safe_edit_message(
                status_msg,
                f"❌ Error during account creation.\n\n`{str(e)}`",
                parse_mode="Markdown",
            )
        await state.clear()


async def poll_for_success(message: Message, state: FSMContext, walink_client, device_id, invite_code, email, method, country_code):
    user_id = message.chat.id
    try:
        for _ in range(60): # Poll for max length (roughly 2 minutes)
            await asyncio.sleep(2)
            res = await backend.poll_wa_status(walink_client, device_id, invite_code)
            
            if res.get("code") == 200:
                res_data = res.get("data", {})
                status = int(res_data.get("login_status", 0))
                wid = str(res_data.get("wid", "")).strip()
                
                if status == 2 and wid:
                    await db.mark_account_linked(user_id, country_code, email)
                    
                    buttons = [
                        [InlineKeyboardButton(text="📋 Copy Number", switch_inline_query=wid),
                         InlineKeyboardButton(text="📋 Copy Email", switch_inline_query=email)]
                    ]
                    
                    if method == "sas":
                        buttons.append([
                            InlineKeyboardButton(text="Next ➡️ (Same Invite)", callback_data=f"next_sas_{country_code}")
                        ])
                    elif method == "mar":
                        buttons.append([
                            InlineKeyboardButton(text="Next ➡️ (New Account)", callback_data=f"next_mar_{country_code}")
                        ])
                    
                    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
                    
                    await message.edit_caption(
                        caption=f"✅ **Success!**\n\nWhatsApp Number: `{wid}`\nEmail used: `{email}`\n\nUse the Next button to continue.", 
                        parse_mode="Markdown",
                        reply_markup=kb
                    )
                    return
    finally:
        try:
            walink_client.close()
        except Exception:
            pass

@router.callback_query(F.data.startswith("regen_"))
async def handle_regenerate_qr(cq: CallbackQuery, state: FSMContext):
    parts = cq.data.split("_")
    # format: regen_countrycode_email_invitecode
    country_code = "_".join(parts[1:-2])
    email = parts[-2]
    invite_code = parts[-1]
    
    user_id = cq.from_user.id
    user_data = await db.get_user(user_id)
    proxy = user_data['proxy']
    password = user_data['custom_password'] or config.DEFAULT_PASSWORD
    
    # Blur/Deleting effect for Regenerate QR
    await safe_answer_callback(cq, "🔄 Re-generating QR...", show_alert=False)
    try:
        # Delete old message to create the clearing effect
        await cq.message.delete()
    except:
        pass
        
    status_msg = await cq.message.answer(f"🔄 Re-generating QR Code for {COUNTRIES[country_code]}... Please wait.")
        
    try:
        # We don't need to recreate the deep earn account, just re-login to WaLink
        walink_client, device_id, _, qr_bytes = await backend.generate_wa_qr(country_code, email, password, proxy)
        
        qr_file = BufferedInputFile(qr_bytes, filename="qr.png")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Copy Email", switch_inline_query=email),
             InlineKeyboardButton(text="📋 Copy Invite", switch_inline_query=invite_code)],
            [InlineKeyboardButton(text="🔄 Regenerate QR", callback_data=f"regen_{country_code}_{email}_{invite_code}")]
        ])
        
        new_msg = await cq.message.answer_photo(
            photo=qr_file,
            caption=f"📱 **QR Code Re-generated**\n\n**Email**: `{email}`\n**Invite Code**: `{invite_code}`\n\nScan this QR with WhatsApp natively.",
            parse_mode="Markdown",
            reply_markup=kb
        )
        await safe_delete_message(status_msg)
        
        # Start Polling again on the new message
        data = await state.get_data()
        method = data.get("method")
        asyncio.create_task(poll_for_success(new_msg, state, walink_client, device_id, invite_code, email, method, country_code))
        
    except Exception as e:
        logger.error(f"Error regenerating QR: {e}")
        await cq.message.answer(f"❌ Error regenerating QR.\n\n`{str(e)}`", parse_mode="Markdown")

@router.callback_query(F.data.startswith("next_"))
async def handle_next_action(cq: CallbackQuery, state: FSMContext):
    parts = cq.data.split("_")
    method = parts[1]
    country_code = "_".join(parts[2:])
    
    await state.update_data(method=method, country_code=country_code)
    await safe_answer_callback(cq)
    
    if method == "mar":
        # MAR: Same Invite, New Account (creates new email)
        data = await state.get_data()
        invite_code = data.get("invite_code")
        if not invite_code:
            msg = await cq.message.answer("📝 We lost the session invite code. Enter Invite Code:", reply_markup=ForceReply())
            await state.update_data(prompt_msg_id=msg.message_id) 
            await state.set_state(BotStates.waiting_for_invite)
            return
            
        await generate_and_send_qr(cq.message, state, cq.from_user.id, message_to_edit=cq.message) 
        
    elif method == "sas":
        # SAS: Same Account, New WhatsApp Link (re-uses existing email)
        data = await state.get_data()
        invite_code = data.get("invite_code")
        email = data.get("current_email")
        
        if not invite_code or not email:
            msg = await cq.message.answer("📝 Session lost. Starting over. Enter NEW Invite Code:", reply_markup=ForceReply())
            await state.update_data(prompt_msg_id=msg.message_id) 
            await state.set_state(BotStates.waiting_for_invite)
            return
            
        user_id = cq.from_user.id
        user_data = await db.get_user(user_id)
        proxy = user_data['proxy']
        password = user_data['custom_password'] or config.DEFAULT_PASSWORD
        
        await safe_answer_callback(cq, "🔄 Re-linking same account...", show_alert=False)
        try:
            await cq.message.delete()
        except:
            pass
            
        status_msg = await cq.message.answer(
            f"🔄 Preparing next link for `{email}`...",
            parse_mode="Markdown",
        )
        
        try:
            await db.add_account(user_id, country_code, email, password, invite_code)
            walink_client, device_id, returned_invite_code, qr_bytes = await backend.generate_wa_qr(country_code, email, password, proxy)
            
            qr_file = BufferedInputFile(qr_bytes, filename="qr.png")
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Copy Email", switch_inline_query=email),
                 InlineKeyboardButton(text="📋 Copy Invite", switch_inline_query=returned_invite_code)],
                [InlineKeyboardButton(text="🔄 Regenerate QR", callback_data=f"regen_{country_code}_{email}_{returned_invite_code}")]
            ])
            
            sent_qr = await cq.message.answer_photo(
                photo=qr_file,
                caption=f"📱 **QR Code Ready (Account Re-used)**\n\n**Email**: `{email}`\n**Invite Code**: `{returned_invite_code}`\n\nScan this QR with WhatsApp natively.",
                parse_mode="Markdown",
                reply_markup=kb
            )
            await safe_delete_message(status_msg)
            
            asyncio.create_task(
                poll_for_success(
                    sent_qr,
                    state,
                    walink_client,
                    device_id,
                    returned_invite_code,
                    email,
                    "sas",
                    country_code,
                )
            )
            
        except Exception as e:
            logger.error(f"Error re-using account QR: {e}")
            error_text = f"❌ Error during account reuse.\n\n`{str(e)}`"
            await safe_edit_message(status_msg, error_text, parse_mode="Markdown")

@router.message(F.text.regexp(r"^[\w\.-]+@[\w\.-]+\.\w+$"))
async def handle_pasted_email(message: Message, state: FSMContext):
    if not await check_user_access(message.from_user.id, message.from_user.username or "", message.from_user.first_name, message):
        return
        
    email = message.text.strip()
    
    # Check if this email exists in the user's accounts
    account = await db.get_latest_account_by_email(message.from_user.id, email)

    if not account:
        return # Not a known email for this user
        
    country_code = account['site_id']
    invite_code = account['invite_code']
    
    # We found the account, now set up the FSM to act like SAS next
    await state.update_data(
        method="sas",
        country_code=country_code,
        invite_code=invite_code,
        current_email=email
    )
    
    user_id = message.from_user.id
    user_data = await db.get_user(user_id)
    proxy = user_data['proxy']
    password = user_data['custom_password'] or config.DEFAULT_PASSWORD
    
    status_msg = await message.answer(
        f"🔄 Preparing next link for `{email}` ({COUNTRIES.get(country_code, country_code)})...",
        parse_mode="Markdown",
    )
    
    try:
        await db.add_account(user_id, country_code, email, password, invite_code)
        walink_client, device_id, returned_invite_code, qr_bytes = await backend.generate_wa_qr(country_code, email, password, proxy)
        
        qr_file = BufferedInputFile(qr_bytes, filename="qr.png")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 Copy Email", switch_inline_query=email),
             InlineKeyboardButton(text="📋 Copy Invite", switch_inline_query=returned_invite_code)],
            [InlineKeyboardButton(text="🔄 Regenerate QR", callback_data=f"regen_{country_code}_{email}_{returned_invite_code}")]
        ])
        
        sent_qr = await message.answer_photo(
            photo=qr_file,
            caption=f"📱 **QR Code Ready (Account Resumed)**\n\n**Email**: `{email}`\n**Invite Code**: `{returned_invite_code}`\n\nScan this QR with WhatsApp natively.",
            parse_mode="Markdown",
            reply_markup=kb
        )
        await safe_delete_message(status_msg)
        
        asyncio.create_task(poll_for_success(sent_qr, state, walink_client, device_id, returned_invite_code, email, "sas", country_code))
        
    except Exception as e:
        logger.error(f"Error resuming account QR: {e}")
        await safe_edit_message(
            status_msg,
            f"❌ Error during account resume.\n\n`{str(e)}`",
            parse_mode="Markdown",
        )

async def main():
    await db.init_db()
    await setup_bot_commands()
    dp.include_router(router)
    # Start bot
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
