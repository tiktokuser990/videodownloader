# ==============================================================================
# ===               TELEGRAM ADVANCED DOWNLOADER BOT SCRIPT                  ===
# ===           FEATURE RICH, USER-FRIENDLY & ADMIN CONTROLS               ===
# ===      VERSION 5.2.0 - ENHANCED ADMIN NOTIFICATIONS & CLEAN UI         ===
# ==============================================================================

import os
import re
import sys
import logging
import asyncio
import time
import math
import glob
import subprocess
import yt_dlp
import aiohttp
import aiosqlite
import shutil
from datetime import date, datetime

from telethon import TelegramClient, events, Button
from telethon.errors.rpcerrorlist import (
    SessionPasswordNeededError, UserNotParticipantError, FloodWaitError,
    PhoneNumberInvalidError, PhoneCodeInvalidError, MessageNotModifiedError,
    UserIsBlockedError, PeerIdInvalidError
)
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.types import DocumentAttributeVideo, DocumentAttributeAudio


# ==============================================================================
# --- DATABASE SETUP (ASYNC) ---
# ==============================================================================

class Database:
    """Async database operations ကို ကိုင်တွယ်ရန် class"""
    def __init__(self, db_name='bot_settings.db'):
        self.db_name = db_name

    async def init_db(self):
        """Database ကို စတင်တည်ဆောက်ပြီး tables များကိုစစ်ဆေးသည်"""
        async with aiosqlite.connect(self.db_name) as db:
            # Settings table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            # Banned users table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS banned_users (
                    user_id INTEGER PRIMARY KEY
                )
            ''')
            # Users table for persistent user list
            await db.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY
                )
            ''')
            # Bot stats table
            await db.execute('''
                CREATE TABLE IF NOT EXISTS bot_stats (
                    key TEXT PRIMARY KEY,
                    value INTEGER
                )
            ''')
            
            defaults = {
                'PRIVATE_CHANNEL_ID': '-1000000000000', # Force-Join Channel ID
                'UPLOAD_CHANNEL_ID': '-1000000000000',  # Upload Channel ID
                'PRIVATE_CHANNEL_INVITE_LINK': 'https://t.me/your_invite_link',
                'UPLOAD_CHANNEL_INVITE_LINK': 'https://t.me/your_upload_channel_link', # New Setting
                'MAX_CONCURRENT_DOWNLOADS': '10'
            }
            for key, value in defaults.items():
                await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
            
            await db.execute("INSERT OR IGNORE INTO bot_stats (key, value) VALUES ('total_downloads', 0)")

            await db.commit()
        logger.info("Async Database initialized successfully with all tables.")

    async def get_setting(self, key, default=None):
        async with aiosqlite.connect(self.db_name) as db:
            async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
                result = await cursor.fetchone()
                return result[0] if result else default

    async def set_setting(self, key, value):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute("REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
            await db.commit()

    async def add_user_to_ban_list(self, user_id):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute("INSERT OR IGNORE INTO banned_users (user_id) VALUES (?)", (user_id,))
            await db.commit()

    async def remove_user_from_ban_list(self, user_id):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
            await db.commit()

    async def load_banned_users_from_db(self):
        async with aiosqlite.connect(self.db_name) as db:
            async with db.execute("SELECT user_id FROM banned_users") as cursor:
                return {row[0] for row in await cursor.fetchall()}

    async def add_user(self, user_id):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
            await db.commit()

    async def load_users_from_db(self):
        async with aiosqlite.connect(self.db_name) as db:
            async with db.execute("SELECT user_id FROM users") as cursor:
                return {row[0] for row in await cursor.fetchall()}

    async def get_bot_stat(self, key):
        async with aiosqlite.connect(self.db_name) as db:
            async with db.execute("SELECT value FROM bot_stats WHERE key = ?", (key,)) as cursor:
                result = await cursor.fetchone()
                return result[0] if result else 0

    async def increment_bot_stat(self, key, amount=1):
        async with aiosqlite.connect(self.db_name) as db:
            await db.execute("UPDATE bot_stats SET value = value + ? WHERE key = ?", (amount, key))
            await db.commit()

    async def get_total_users(self):
        async with aiosqlite.connect(self.db_name) as db:
            async with db.execute("SELECT COUNT(user_id) FROM users") as cursor:
                result = await cursor.fetchone()
                return result[0] if result else 0

# ==============================================================================
# --- CONFIGURATION ---
# ==============================================================================
API_ID = os.environ.get("API_ID", "23877053")
API_HASH = os.environ.get("API_HASH", "989c360358b981dae46a910693ab2f4c")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "6646404639"))
COOKIE_FILE_PATH = 'cookies.txt'
IS_ARIA2C_AVAILABLE = shutil.which('aria2c') is not None

# ==============================================================================
# --- အခြေခံ Setup များ ---
# ==============================================================================
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

DOWNLOAD_DIR = 'downloads/'
TELEGRAM_UPLOAD_LIMIT_BYTES = 2 * 1024 * 1024 * 1024

db = Database()

USER_CONTEXT = {}
BROADCAST_USERS = set()
BANNED_USERS = set()
LOGIN_STATE = {}
BOT_START_TIME = time.time()
DOWNLOAD_SEMAPHORE = None
MAX_CONCURRENT_DOWNLOADS = 10
DOWNLOAD_STATS = {}

bot = TelegramClient('bot_session', API_ID, API_HASH)
uploader = TelegramClient('uploader_session', API_ID, API_HASH)

# ==============================================================================
# --- Helper Functions (အထောက်အကူပြု Functions များ) ---
# ==============================================================================
def human_readable_size(size_bytes):
    if size_bytes is None or size_bytes == 0: return "0B"
    size_name = ("B", "KB", "MB", "GB", "TB")
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {size_name[i]}"

def format_duration(seconds):
    if seconds is None: return "N/A"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    duration_str = ""
    if d > 0: duration_str += f"{d}d "
    if h > 0: duration_str += f"{h}h "
    if m > 0: duration_str += f"{m}m "
    if s > 0 or not duration_str: duration_str += f"{s}s"
    return duration_str.strip()

async def generate_thumbnail(video_path, task_id, duration=None):
    thumb_path = os.path.join(DOWNLOAD_DIR, f"thumb_{task_id}.jpg")
    capture_time_seconds = int(duration * 0.25) if duration and duration > 1 else 0
    capture_time_str = time.strftime('%H:%M:%S', time.gmtime(capture_time_seconds))
    try:
        command = ['ffmpeg', '-i', video_path, '-ss', capture_time_str, '-vframes', '1', '-y', thumb_path]
        process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        _, stderr = await process.communicate()
        if process.returncode != 0:
            logger.error(f"FFmpeg thumbnail failed for {video_path}: {stderr.decode()}")
            return None
        return thumb_path if os.path.exists(thumb_path) else None
    except Exception as e:
        logger.error(f"Error generating thumbnail for {video_path}: {e}")
        return None

def cleanup_downloads():
    logger.info("Bot စတင်နေပါပြီ... မလိုအပ်သောဖိုင်များကို ရှင်းလင်းနေသည်...")
    if not os.path.isdir(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    for f in glob.glob(os.path.join(DOWNLOAD_DIR, '*')):
        try:
            if os.path.isfile(f): os.remove(f)
        except OSError as e:
            logger.error(f"ရှင်းလင်းနေစဉ် အမှားအယွင်းဖြစ်ပေါ်: {f}: {e}")

async def is_user_subscribed(user_id):
    if user_id == ADMIN_ID: return True
    try:
        # This function specifically checks the FORCE-JOIN channel
        channel_id_str = await db.get_setting('PRIVATE_CHANNEL_ID')
        if not channel_id_str or channel_id_str == '-1000000000000': 
            logger.warning("Force-Join Channel ID is not set. Subscription check skipped.")
            return True # If not set, allow access by default
        channel_id = int(channel_id_str)
        await bot(GetParticipantRequest(channel=channel_id, participant=user_id))
        return True
    except UserNotParticipantError:
        return False
    except Exception as e:
        logger.error(f"Subscription စစ်ဆေးမရပါ (User: {user_id}): {e}")
        return False

# ==============================================================================
# --- User Commands ---
# ==============================================================================
@bot.on(events.NewMessage(pattern='/start', func=lambda e: e.is_private))
async def start_handler(event):
    user_id = event.sender_id
    if user_id not in BROADCAST_USERS:
        BROADCAST_USERS.add(user_id)
        await db.add_user(user_id)
    
    await event.reply(
        '👋 **မင်္ဂလာပါ! Hub Downloader Bot မှ ကြိုဆိုပါတယ်။**\n\n'
        '**Down ခြင်တဲ့ Video link ကိုပေးပို့ပါ**'
    )

@bot.on(events.NewMessage(pattern='/help', func=lambda e: e.is_private))
async def help_handler(event):
    await event.reply(
        '╔══════════════════╗\n╠⍟**အသုံးပြုရန်နည်းလမ်းများ** ╠════⍟  \n╚══════════════════╝\n\n'
        '1. ဒေါင်းလုဒ်ဆွဲလိုသော ဗီဒီယို (သို့) သီချင်း၏ Link ကို Bot ထဲသို့ တိုက်ရိုက်ထည့်ပါ။\n\n'
        '2. Bot မှ Quality ရွေးချယ်ရန် Button များ ပြပေးပါလိမ့်မည်။\n\n'
        '3. မိမိနှစ်သက်ရာ Quality ကို ရွေးချယ်ပြီး ဒေါင်းလုဒ်ဆွဲနိုင်ပါသည်။\n\n'
        '4.**ဒေါင်းနိုင်သော အရာများ**\n╔══⍟\n╠═⍟Tiktok \n╠═⍟ Facebook \n╠═⍟ Instgram \n╠═⍟ YouTube \n╠═⍟ VK \n╠═⍟ PronHub \n╠═⍟ Pinterest\n╠═⍟ Twitter (X)\n╚═════⍟\n'
        '👉 Bot ကို အသုံးပြုရန် Channel ကို Join ရန်လိုအပ်ပါသည်။'
    )

@bot.on(events.NewMessage(pattern='/stats', func=lambda e: e.is_private))
async def public_stats_handler(event):
    total_users = await db.get_total_users()
    total_downloads = await db.get_bot_stat('total_downloads')
    
    stats_msg = (
        "📊 **Bot Statistics**\n\n"
        f"👥 **စုစုပေါင်း အသုံးပြုသူ:** `{total_users}` ဦး\n"
        f"📥 **စုစုပေါင်း ဒေါင်းလုဒ်:** `{total_downloads}` ကြိမ်"
    )
    await event.reply(stats_msg)
    
# ==============================================================================
# --- Admin Commands & Functions ---
# ==============================================================================

# --- Login & Logout ---
@bot.on(events.NewMessage(pattern='/login', from_users=ADMIN_ID))
async def login_handler(event):
    await uploader.connect()
    if await uploader.is_user_authorized():
        return await event.reply("✅ Uploader Account က Login ဝင်ပြီးသားပါ။")
    LOGIN_STATE[ADMIN_ID] = {'step': 'awaiting_phone'}
    await event.reply("🔐 **Uploader Login Process**\n\nသင်၏ ဖုန်းနံပါတ်ကို နိုင်ငံတကာကုဒ်ဖြင့် ထည့်ပါ (ဥပမာ: `+959...`)\n\n`/cancel` ဖြင့် ပယ်ဖျက်နိုင်ပါသည်။")

@bot.on(events.NewMessage(pattern='/logout', from_users=ADMIN_ID))
async def logout_handler(event):
    if await uploader.is_user_authorized():
        await uploader.log_out()
        if os.path.exists('uploader_session.session'):
            os.remove('uploader_session.session')
        await event.reply("🔒 Uploader Account မှ ထွက်ပြီးပါပြီ။ Session file ကိုလည်း ဖျက်လိုက်ပါပြီ။")
    else:
        await event.reply("ℹ️ Uploader Account က Login ဝင်ထားခြင်းမရှိပါ။")

# --- Restart & Update ---
@bot.on(events.NewMessage(pattern='/restart', from_users=ADMIN_ID))
async def restart_handler(event):
    await event.reply("🔄 Bot ကို Restart လုပ်နေပါပြီ...")
    os.execl(sys.executable, sys.executable, *sys.argv)

async def update_yt_dlp(event):
    msg = await event.edit("🔄 **Updating `yt-dlp`...**\n\nနောက်ကွယ်မှ command ကို run နေပါသည်၊ ခေတ္တစောင့်ဆိုင်းပါ...")
    process = await asyncio.create_subprocess_exec(
        sys.executable, '-m', 'pip', 'install', '--upgrade', 'yt-dlp',
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    output = stdout.decode() + "\n" + stderr.decode()
    
    if process.returncode == 0:
        response_text = f"✅ **`yt-dlp` Update အောင်မြင်ပါသည်!**\n\n**Output:**\n`{output}`\n\n⚠️ Update အသစ် အသက်ဝင်ရန် `/restart` command ဖြင့် bot ကို ပြန်လည်စတင်ပေးပါ။"
    else:
        response_text = f"❌ **`yt-dlp` Update မအောင်မြင်ပါ!**\n\n**Error:**\n`{output}`"
    await msg.edit(response_text)

# --- Broadcast Function ---
async def broadcast_message_task(event, message_to_forward):
    """Broadcasts a message by forwarding it to all users."""
    admin_status_msg = await event.edit("📢 **Broadcast in progress...**\n\nPreparing to forward the message.")
    success_count = 0
    fail_count = 0
    total_users = len(BROADCAST_USERS)
    
    for i, user_id in enumerate(list(BROADCAST_USERS)):
        try:
            await bot.forward_messages(
                entity=user_id,
                messages=message_to_forward.id,
                from_peer=message_to_forward.peer_id
            )
            success_count += 1
        except (UserIsBlockedError, PeerIdInvalidError):
            fail_count += 1
        except FloodWaitError as fwe:
            await admin_status_msg.edit(f"Flood wait for {fwe.seconds} seconds... Pausing broadcast.")
            await asyncio.sleep(fwe.seconds)
            try: # Retry after wait
                await bot.forward_messages(
                    entity=user_id,
                    messages=message_to_forward.id,
                    from_peer=message_to_forward.peer_id
                )
                success_count += 1
            except Exception:
                fail_count += 1
        except Exception as e:
            fail_count += 1
            logger.error(f"Broadcast error to user {user_id}: {e}")

        if (i + 1) % 20 == 0: # Update status every 20 users
            try:
                await admin_status_msg.edit(
                    f"📢 **Broadcasting...**\n\n"
                    f"╔══Sent: `{i + 1}/{total_users}`\n"
                    f"╠══Success: `{success_count}`\n"
                    f"╚══Failed: `{fail_count}`"
                )
            except MessageNotModifiedError:
                pass
            await asyncio.sleep(1)

    summary_text = (
        f"🎉 **Broadcast Complete!**\n\n"
        f"╔══📊Total Users: `{total_users}`\n"
        f"╠═✅ Forwarded Successfully: `{success_count}`\n"
        f"╚══❌ Failed to Forward: `{fail_count}`"
    )
    await admin_status_msg.edit(summary_text)

# --- Admin Panel & Input Handlers ---
@bot.on(events.NewMessage(pattern='/admin', from_users=ADMIN_ID))
async def admin_panel_handler(event, edit=False):
    uptime = format_duration(time.time() - BOT_START_TIME)
    total_users_count = await db.get_total_users()
    panel_text = (
        f"👑 **Admin Control Panel**\n\n"
        f"╔═══⍟**Uptime:** `{uptime}`\n"
        f"╠═══⍟**Total Users (Persistent):** `{total_users_count}`\n"
        f"╠═══⍟**Banned Users:** `{len(BANNED_USERS)}`\n"
        f"╚═══⍟**Active Downloads:** `{MAX_CONCURRENT_DOWNLOADS - DOWNLOAD_SEMAPHORE._value}`/`{MAX_CONCURRENT_DOWNLOADS}`"
    )
    panel_buttons = [
        [Button.inline("⚙️ Bot Settings", b"admin:settings"), Button.inline("📊 Bot Stats", b"admin:stats")],
        [Button.inline("👤 User Management", b"admin:user_manage")],
        [Button.inline("🚀 Maintenance", b"admin:maintenance")],
        [Button.inline("❌ Close Panel", b"admin:close")]
    ]
    if edit:
        try: await event.edit(panel_text, buttons=panel_buttons)
        except MessageNotModifiedError: pass
    else:
        await event.reply(panel_text, buttons=panel_buttons)

async def show_settings_menu(event):
    settings_text = (
        "⚙️ **Bot Settings**\n\n"
        f"╔══⍟**📢 Force-Join Channel ID:** `{await db.get_setting('PRIVATE_CHANNEL_ID')}`\n"
        f"╚══⍟**🔗 Force-Join Invite Link:** `{await db.get_setting('PRIVATE_CHANNEL_INVITE_LINK')}`\n\n"
        f"╔══⍟**📤 Upload Channel ID:** `{await db.get_setting('UPLOAD_CHANNEL_ID')}`\n"
        f"╚══⍟**🔗 Upload Channel Invite Link:** `{await db.get_setting('UPLOAD_CHANNEL_INVITE_LINK')}`\n\n"
        f"**⚡️ Max Downloads:** `{await db.get_setting('MAX_CONCURRENT_DOWNLOADS')}`"
    )
    settings_buttons = [
        [Button.inline("🆔 Force-Join ID", b"admin:set_force_join_id"), Button.inline("🔗 Set Force-Join Link", b"admin:set_invite_link")],
        [Button.inline("📤 Set Upload Channel ID", b"admin:set_upload_channel_id"), Button.inli("🔗 Upload Invite Link", b"admin:set_upload_invite_link")],
        [Button.inline("⚡️ Max Downloads", b"admin:set_max_dl")],
        [Button.inline("⬅️ Back", b"admin:back_main")]
    ]
    await event.edit(settings_text, buttons=settings_buttons)

async def show_user_management_menu(event):
    menu_text = "👤 **User Management**\n\nSelect an action:"
    menu_buttons = [
        [Button.inline("📢 Broadcast (Forward)", b"admin:broadcast")],
        [Button.inline("🚫 Ban User", b"admin:ban"), Button.inline("✅ Unban User", b"admin:unban")],
        [Button.inline("⬅️ Back", b"admin:back_main")]
    ]
    await event.edit(menu_text, buttons=menu_buttons)

async def show_maintenance_menu(event):
    menu_text = "🚀 **Maintenance**\n\nSelect an action:"
    menu_buttons = [
        [Button.inline("🔄 Update yt-dlp", b"admin:ytdlp_update")],
        [Button.inline("🧹 Clean Downloads", b"admin:cleandl")],
        [Button.inline("💽 Check Storage", b"admin:storage")],
        [Button.inline("🔄 Restart Bot", b"admin:restart")],
        [Button.inline("⬅️ Back", b"admin:back_main")]
    ]
    await event.edit(menu_text, buttons=menu_buttons)

async def handle_admin_input(event):
    user_id = event.sender_id
    text = event.text.strip()
    context = USER_CONTEXT.get(user_id)

    if not context or 'admin_action' not in context: return

    action = context['admin_action']
    
    if text == '/cancel':
        del USER_CONTEXT[user_id]
        return await event.reply("✅ လုပ်ဆောင်ချက်ကို ပယ်ဖျက်လိုက်ပါသည်။")

    if action == 'awaiting_force_join_id':
        if re.match(r'^-100\d+$', text):
            await db.set_setting('PRIVATE_CHANNEL_ID', text)
            await event.reply(f"✅ Force-Join Channel ID ကို `{text}` သို့ ပြောင်းလဲလိုက်ပါသည်။")
            del USER_CONTEXT[user_id]
        else:
            await event.reply("❌ **မှားယွင်းနေသည်!** Channel ID သည် `-100` ဖြင့်စရပါမည်။ ထပ်ကြိုးစားပါ။\n`/cancel` ဖြင့် ပယ်ဖျက်နိုင်ပါသည်။")
            
    elif action == 'awaiting_upload_channel_id':
        if re.match(r'^-100\d+$', text):
            await db.set_setting('UPLOAD_CHANNEL_ID', text)
            await event.reply(f"✅ Upload Channel ID ကို `{text}` သို့ ပြောင်းလဲလိုက်ပါသည်။")
            del USER_CONTEXT[user_id]
        else:
            await event.reply("❌ **မှားယွင်းနေသည်!** Channel ID သည် `-100` ဖြင့်စရပါမည်။ ထပ်ကြိုးစားပါ။\n`/cancel` ဖြင့် ပယ်ဖျက်နိုင်ပါသည်။")
    
    elif action == 'awaiting_invite_link':
        if re.match(r'https?://t\.me/\S+', text):
            await db.set_setting('PRIVATE_CHANNEL_INVITE_LINK', text)
            await event.reply(f"✅ Force-Join Invite Link ကို `{text}` သို့ ပြောင်းလဲလိုက်ပါသည်။")
            del USER_CONTEXT[user_id]
        else:
            await event.reply("❌ **မှားယွင်းနေသည်!** မှန်ကန်သော Telegram Invite Link (`https://t.me/...`) ကိုထည့်ပါ။\n`/cancel` ဖြင့် ပယ်ဖျက်နိုင်ပါသည်။")

    elif action == 'awaiting_upload_invite_link':
        if re.match(r'https?://t\.me/\S+', text):
            await db.set_setting('UPLOAD_CHANNEL_INVITE_LINK', text)
            await event.reply(f"✅ Upload Channel Invite Link ကို `{text}` သို့ ပြောင်းလဲလိုက်ပါသည်။")
            del USER_CONTEXT[user_id]
        else:
            await event.reply("❌ **မှားယွင်းနေသည်!** မှန်ကန်သော Telegram Invite Link (`https://t.me/...`) ကိုထည့်ပါ။\n`/cancel` ဖြင့် ပယ်ဖျက်နိုင်ပါသည်။")
            
    elif action == 'awaiting_max_dl':
        if text.isdigit() and int(text) > 0:
            await db.set_setting('MAX_CONCURRENT_DOWNLOADS', text)
            global MAX_CONCURRENT_DOWNLOADS, DOWNLOAD_SEMAPHORE
            MAX_CONCURRENT_DOWNLOADS = int(text)
            DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
            await event.reply(f"✅ Max Concurrent Downloads ကို `{text}` သို့ ပြောင်းလဲလိုက်ပါသည်။")
            del USER_CONTEXT[user_id]
        else:
            await event.reply("❌ **မှားယွင်းနေသည်!** 0 ထက်ကြီးသော ကိန်းဂဏန်းတစ်ခုကို ထည့်ပါ။\n`/cancel` ဖြင့် ပယ်ဖျက်နိုင်ပါသည်။")
    
    elif action == 'awaiting_ban_id':
        if text.isdigit():
            target_id = int(text)
            if target_id == ADMIN_ID:
                return await event.reply("❌ သင်ကိုယ်တိုင်ကို Ban လုပ်၍မရပါ။")
            BANNED_USERS.add(target_id)
            await db.add_user_to_ban_list(target_id)
            await event.reply(f"✅ User ID `{target_id}` ကို အောင်မြင်စွာ Ban လုပ်ပြီးပါပြီ။")
            del USER_CONTEXT[user_id]
        else:
            await event.reply("❌ **မှားယွင်းနေသည်!** ကျေးဇူးပြု၍ မှန်ကန်သော User ID (နံပါတ်များသာ) ကို ထည့်ပါ။\n`/cancel` ဖြင့် ပယ်ဖျက်နိုင်ပါသည်။")

    elif action == 'awaiting_unban_id':
        if text.isdigit():
            target_id = int(text)
            if target_id in BANNED_USERS:
                BANNED_USERS.remove(target_id)
                await db.remove_user_from_ban_list(target_id)
                await event.reply(f"✅ User ID `{target_id}` ကို အောင်မြင်စွာ Unban လုပ်ပြီးပါပြီ။")
            else:
                await event.reply(f"ℹ️ User ID `{target_id}` is not in the ban list.")
            del USER_CONTEXT[user_id]
        else:
            await event.reply("❌ **မှားယွင်းနေသည်!** ကျေးဇူးပြု၍ မှန်ကန်သော User ID (နံပါတ်များသာ) ကို ထည့်ပါ။\n`/cancel` ဖြင့် ပယ်ဖျက်နိုင်ပါသည်။")

# ==============================================================================
# --- Message Handlers (Admin & User) ---
# ==============================================================================

# Admin state handler (login, settings input)
@bot.on(events.NewMessage(from_users=ADMIN_ID, func=lambda e: e.is_private and not e.text.startswith('/')))
async def admin_state_handler(event):
    user_id = event.sender_id
    if LOGIN_STATE.get(user_id):
        text = event.text.strip()
        state = LOGIN_STATE.get(user_id, {})
        step = state.get('step')

        if text == '/cancel':
            if user_id in LOGIN_STATE: del LOGIN_STATE[user_id]
            return await event.reply("✅ Login Process ကို ပယ်ဖျက်လိုက်ပါသည်။")

        try:
            if step == 'awaiting_phone':
                LOGIN_STATE[user_id].update({'phone': text, 'step': 'awaiting_code'})
                sent_code = await uploader.send_code_request(text)
                LOGIN_STATE[user_id]['phone_code_hash'] = sent_code.phone_code_hash
                await event.reply("📲 Telegram မှ ပေးပို့လိုက်သော Code ကို ထည့်ပါ။")
            elif step == 'awaiting_code':
                try:
                    await uploader.sign_in(state['phone'], code=text, phone_code_hash=state[phone_code_hash'])
                    me = await uploader.get_me()
                    await event.reply(f"✅ Login အောင်မြင်ပါသည်။ Welcome, **{me.first_name}**!")
                    del LOGIN_STATE[user_id]
                except SessionPasswordNeededError:
                    LOGIN_STATE[user_id]['step'] = 'awaiting_password'
                    await event.reply("🔑 သင်၏ 2FA Password ကို ထည့်ပါ။")
                except PhoneCodeInvalidError:
                    await event.reply("❌ Code မှားနေပါသည်။ `/login` ဖြင့် ပြန်လည်ကြိုးစားပါ။")
                    del LOGIN_STATE[user_id]
            elif step == 'awaiting_password':
                try:
                    await uploader.sign_in(password=text)
                    me = await uploader.get_me()
                    await event.reply(f"✅ 2FA မှန်ကန်၍ Login အောင်မြင်ပါသည်။ Welcome, **{me.first_name}**!")
                    del LOGIN_STATE[user_id]
                except Exception as e:
                    await event.reply(f"❌ Password မှားနေပါသည်။\nError: `{e}`")
                    del LOGIN_STATE[user_id]
        except PhoneNumberInvalidError:
            await event.reply("❌ ဖုန်းနံပါတ် မှားယွင်းနေပါသည်။ နိုင်ငံတကာကုဒ်ဖြင့် မှန်ကန်စွာ ထည့်ပါ။ (ဥပမာ: `+95...`)")
            del LOGIN_STATE[user_id]
        except Exception as e:
            await event.reply(f"Login လုပ်ရာတွင် အမှားအယွင်းဖြစ်ပေါ်: `{e}`")
            logger.error(f"Login flow error: {e}", exc_info=True)
            if user_id in LOGIN_STATE: del LOGIN_STATE[user_id]
    
    elif USER_CONTEXT.get(user_id, {}).get('admin_action'):
        await handle_admin_input(event)

# Broadcast message handler
@bot.on(events.NewMessage(from_users=ADMIN_ID, func=lambda e: USER_CONTEXT.get(e.sender_id, {}).get('admin_action') == 'awaiting_broadcast_message'))
async def broadcast_message_handler(event):
    user_id = event.sender_id
    message_to_broadcast = event.message
    
    USER_CONTEXT[user_id]['broadcast_message'] = message_to_broadcast
    USER_CONTEXT[user_id]['admin_action'] = 'awaiting_broadcast_confirm'

    total_users_count = await db.get_total_users()
    confirmation_text = f"📢 သင်သည် ဤ message ကို user `{total_users_count}` ဦးထံသို့ **Forward** ပို့တော့မှာ သေချာပါသလား?"
    await event.reply(confirmation_text, buttons=[
        [Button.inline("✅ Yes, Forward Now", b"admin:broadcast_confirm")],
        [Button.inline("❌ No, Cancel", b"admin:broadcast_cancel")]
    ])

# Main user message handler
@bot.on(events.NewMessage(func=lambda e: e.is_private and not e.forward and (e.text and not e.text.startswith('/'))))
async def main_message_handler(event):
    user_id = event.sender_id
    
    if user_id in BANNED_USERS: return
    if user_id == ADMIN_ID and (USER_CONTEXT.get(user_id) or LOGIN_STATE.get(user_id)): return

    if user_id not in BROADCAST_USERS:
        BROADCAST_USERS.add(user_id)
        await db.add_user(user_id)
        
    text = event.text.strip()
    if not re.match(r'(?i)https?://\S+', text): return

    invite_link = await db.get_setting('PRIVATE_CHANNEL_INVITE_LINK')
    if not await is_user_subscribed(user_id):
        join_msg = (f"**ACCESS DENIED** 😕\n\n"
                    f"Bot ကိုအသုံးပြုရန် အောက်ပါ Channel ကို Join ပေးပါ။\n\n"
                    f"➡️ [Click to Join Channel]({invite_link})")
        return await event.reply(join_msg, buttons=[Button.url("👉 Channel ကို Join ရန် 👈", invite_link)])

    if not uploader.is_connected() or not await uploader.is_user_authorized():
        return await evply("⚠️ Bot ကို ပြုပြင်ထိန်းသိမ်းနေပါသည် Admin ကိုဆက်သွယ်ပါ @Hub_Offical")
    
    task_id = f"{user_id}_{int(time.time() * 1000)}"
    msg = await event.reply("🔎 **သင်၏ Link ကို စစ်ဆေးနေပါသည်...**")
    
    temp_thumbnail_path = None
    try:
        async with bot.action(user_id, 'typing'):
            ydl_opts = {'noplaylist': True, 'quiet': True, 'cookiefile': COOKIE_FILE_PATH if os.path.exists(COOKIE_FILE_PATH) else None}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, text, download=False)
        
        title = info.get('title', 'Untitled Content')
        duration = info.get('duration')
        USER_CONTEXT[task_id] = {'status_message': msg, 'user_id': user_id, 'url': text, 'title': title, 'duration': duration}
        
        buttons = [[Button.inline("🎧 Audio (MP3)", f"quality:audio:{task_id}")]]
        available_formats = sorted({f['height'] for f in info.get('formats', []) if f.get('height') and f.get('height') >= 360})
        
        quality_buttons = [Button.inline(f"🎬 {h}p", f"quality:{h}:{task_id}") for h in available_formats]
        if quality_buttons:
            rows = [quality_buttons[i:i + 2] for i in range(0, len(quality_buttons), 2)]
            buttons.extend(rows)

        buttons.append([Button.inline("❌ Cancel", f"cancel:op:{task_id}")])
        caption = f"**{title}**\n\nကျေးဇူးပြု၍ ဒေါင်းလိုသော Quality ကို ရွေးချယ်ပါ:"
        
        thumbnail_url = info.get('thumbnail')
        if thumbnail_url:
            async with aiohttp.ClientSession() as session:
                async with session.get(thumbnail_url) as resp:
                    if resp.status == 200:
                        temp_thumbnail_path = os.path.join(DOWNLOAD_DIR, f"temp_thumb_{task_id}.jpg")
                        with open(temp_thumbnail_path, 'wb') as f: f.write(await resp.read())
                        new_msg = await bot.send_file(user_id, file=temp_thumbnail_path, caption=caption, buttons=buttons)
                        await msg.delete()
                        USER_CONTEXT[task_id]['status_message'] = new_msg
                    else: await msg.edit(caption, buttons=buttons)
        else: await msg.edit(caption, buttons=buttons)

    except Exception as e:
        logger.error(f"Link Processing Error: {e}", exc_info=True)
        user_friendly_error = "❌ **Error!** ဤ Link ကို အသုံးပြု၍မရပါ။"
        error_text = str(e).lower()
        if "unsupported url" in error_text: user_friendly_error += "\n`Reason: ဤ Website ကို မထောက်ပံ့ပါ။`"
        elif "private video" in error_text: user_friendly_error += "\n`Reason: ဤ Video သည် Private ဖြစ်နေပါသည်။`"
        elif "not a valid url" in error_text: user_friendly_error += "\n`Reason: Link ပုံစံ မှားယွင်းနေပါသည်။`"
        await msg.edit(user_friendly_error)
        if task_id in USER_CONTEXT: del USER_CONTEXT[task_id]
    finally:
        if temp_thumbnail_path and os.path.exists(temp_thumbnail_path): os.remove(temp_thumbnail_path)


# ==============================================================================
# --- Callback & Download Logic ---
# ==============================================================================
@bot.on(events.CallbackQuery)
async def callback_handler(event):
    user_id = event.sender_id
    query_data_str = event.data.decode()

    # --- Admin Panel Callbacks ---
    if query_data_str.startswith('admin:'):
        await event.answer()
        action = query_data_str.split(':', 1)[1]

        if action == 'close': await event.delete()
        elif action == 'back_main': await admin_panel_handler(event, edit=True)
        elif action == 'settings': await show_settings_menu(event)
        elif action == 'user_manage': await show_user_management_menu(event)
        elif action == 'maintenance': await show_maintenance_menu(event)
        
        elif action == 'stats':
             today_key = date.today().isoformat()
             today_usage = human_readable_size(DOWNLOAD_STATS.get(today_key, 0))
             total_downloads = await db.get_bot_stat('total_downloads')
             stats_text = (f"📊 **Bot Statistics**\n\n"
                           f"╔═══⍟**Bot Uptime:** `{format_duration(time.time() - BOT_START_TIME)}`\n"
                           f"╠⍟**Today's Download Usage:** `{today_usage}`\n"
                           f"╚═══⍟**Total Successful Downloads:** `{total_downloads}`")
             await event.edit(stats_text, buttons=[[Button.inline("⬅️ Back", b"admin:back_main")]])
        
        elif action == 'storage':
            try:
                path_to_check = os.path.abspath(DOWNLOAD_DIR)
                total, used, free = shutil.disk_usage(path_to_check)
                storage_text = (f"💽 **Server Storage Usage**\n\n"
                                f"**Total:** `{human_readable_size(total)}`\n"
                                f"**Used:** `{human_readable_size(used)}`\n"
                                f"**Free:** `{human_readable_size(free)}`")
                await event.edit(storage_text, buttons=[[Button.inline("⬅️ Back", b"admin:maintenance")]])
            except Exception as e:
                await event.edit(f"❌ Storage ကိုစစ်ဆေးမရပါ: `{e}`", buttons=[[Button.inline("⬅️ Back", b"admin:maintenance")]])

        elif action == 'cleandl':
            msg = await event.edit("🧹 `downloads` folder ကို ရှင်းလင်းနေပါသည်...")
            count, total_size = 0, 0
            for f in glob.glob(os.path.join(DOWNLOAD_DIR, '*')):
                try:
                    if os.path.isfile(f):
                        file_size = os.path.getsize(f)
                        os.remove(f); count += 1; total_size += file_size
                except OSError as e: logger.error(f"File ရှင်းလင်းရာတွင် အမှားဖြစ်ပေါ်: {f}: {e}")
            await msg.edit(f"✅ **Clean Up Complete!**\n`{count}` ဖိုင် (စုစုပေါင်း `{human_readable_size(total_size)}`) ကို ရှင်းလင်းပြီးပါပြီ။", buttons=[[Button.inline("⬅️ Back", b"admin:maintenance")]])
        
        elif action == 'set_force_join_id':
            USER_CONTEXT[user_id] = {'admin_action': 'awaiting_force_join_id'}
            await event.edit("🆔 **Set Force-Join Channel ID**\n\nUser တွေ မဖြစ်မနေ Join ရမယ့် Channel ID ကို ပေးပို့ပါ (ဥပမာ: `-100123...`)။\n\n`/cancel` ဖြင့် ပယ်ဖျက်ပါ။", buttons=None)
        
        elif action == 'set_upload_channel_id':
            USER_CONTEXT[user_id] = {'admin_action': 'awaiting_upload_channel_id'}
            await event.edit("📤 **Set Upload Channel ID**\n\nVideo တွေ upload တင်မယ့် Channel ID ကို ပေးပို့ပါ (ဥပမာ: `-100123...`)။\n\n`/cancel` ဖြင့် ပယ်ဖျက်ပါ။", buttons=None)

        elif action == 'set_invite_link':
            USER_CONTEXT[user_id] = {'admin_action': 'awaiting_invite_link'}
            await event.edit("🔗 **Set Force-Join Invite Link**\n\nForce-Join Channel အတွက် Invite Link အသစ်ကို ပေးပို့ပါ။\n\n`/cancel` ဖြင့် ပယ်ဖျက်ပါ။", buttons=None)
        
        elif action == 'set_upload_invite_link':
            USER_CONTEXT[user_id] = {'admin_action': 'awaiting_upload_invite_link'}
            await event.edit("🔗 **Set Upload Invite Link**\n\nUpload Channel အတွက် Invite Link အသစ်ကို ပေးပို့ပါ။\n\n`/cancel` ဖြင့် ပယ်ဖျက်ပါ။", buttons=None)

        elif action == 'set_max_dl':
            USER_CONTEXT[user_id] = {'admin_action': 'awaiting_max_dl'}
            await event.edit("⚡️ **Set Max Downloads**\n\nတစ်ပြိုင်နက်တည်း download အရေအတွက်ကို ပေးပို့ပါ။\n\n`/cancel` ဖြင့် ပယ်ဖျက်ပါ။", buttons=None)
        elif action == 'ban':
            USER_CONTEXT[user_id] = {'admin_action': 'awaiting_ban_id'}
            await event.edit("🚫 **Ban User**\n\nBan လုပ်လိုသော User ၏ ID ကို ထည့်ပါ။\n\n`/cancel` ဖြင့် ပယ်ဖျက်ပါ။", buttons=None)
        elif action == 'unban':
            USER_CONTEXT[user_id] = {'admin_action': 'awaiting_unban_id'}
            await event.edit("✅ **Unban User**\n\nUnban လုပ်လိုသော User ၏ ID ကို ထည့်ပါ။\n\n`/cancel` ဖြင့် ပယ်ဖျက်ပါ။", buttons=None)
        elif action == 'broadcast':
            USER_CONTEXT[user_id] = {'admin_action': 'awaiting_broadcast_message'}
            await event.edit("📢 **Broadcast Message**\n\nUser များထံ Forward ပို့လိုသော message ကို ရေးသားပေးပို့ပါ။ (Text, Photo, etc.)\n\n`/cancel` ဖြင့် ပယ်ဖျက်ပါ။", buttons=None)
        elif action == 'broadcast_confirm':
            context = USER_CONTEXT.get(user_id, {})
            message_to_broadcast = context.get('broadcast_message')
            if message_to_broadcast:
                await event.edit("✅ Confirmation ရပါပြီ။ Broadcasting စတင်ပါပြီ...", buttons=None)
                asyncio.create_task(broadcast_message_task(await event.get_message(), message_to_broadcast))
            else:
                await event.edit("❌ Error! Broadcast လုပ်ရန် message မတွေ့ပါ။", buttons=None)
            if user_id in USER_CONTEXT: del USER_CONTEXT[user_id]
        elif action == 'broadcast_cancel':
            if user_id in USER_CONTEXT: del USER_CONTEXT[user_id]
            await event.edit("✅ Broadcast ကို ပယ်ဖျက်လိုက်ပါသည်။", buttons=[[Button.inline("⬅️ Back to Admin Panel", b"admin:back_main")]])
        elif action == 'restart':
            await event.edit("🔄 Bot is restarting...")
            os.execl(sys.executable, sys.executable, *sys.argv)
        elif action == 'ytdlp_update':
            await update_yt_dlp(event)
        return

    # --- Other Callbacks ---
    if any(query_data_str.startswith(prefix) for prefix in ['quality:', 'cancel:', 'dest:']):
        try:
            action, value, task_id = query_data_str.split(':', 2)
        except (IndexError, ValueError):
            return await event.answer("❌ Error! Invalid button data.", alert=True)

        context = USER_CONTEXT.get(task_id)
        if not context:
            return await event.edit("❌ **Error!** ဤတောင်းဆိုမှုသည် အချိန်ကုန်သွားပါပြီ။ Link ကိုပြန်ပို့ပါ။", buttons=None)
        
        if action == 'cancel':
            sub_action = value
            if sub_action == 'op': # Cancel operation selection
                if task_id in USER_CONTEXT: del USER_CONTEXT[task_id]
                await event.edit("✅ လုပ်ဆောင်ချက်ကို ပယ်ဖျက်လိုက်ပါသည်။", buttons=None)
            elif sub_action == 'dl': # Cancel download
                USER_CONTEXT[task_id]['cancelled'] = True
                await event.answer("🚫 ဒေါင်းလုဒ်ကို ရပ်တန့်ရန် တောင်းဆိုလိုက်ပါပြီ...")
        
        elif action == 'quality':
            await event.answer()
            quality_text = value if value == 'audio' else value + 'p'
            cancel_button = [[Button.inline("❌ Cancel Download", f"cancel:dl:{task_id}")]]
                           await event.edit(
                    f"✅ **Quality: `{quality_text}`**\n\n📥 ဒေါင်းလုဒ်ရန်ပြင်ဆင်နေပါသည်ခဏစောင့်ပါ..",
                    buttons=cancel_button, file=None)
            except MessageNotModifiedError: pass
            asyncio.create_task(handle_video_download(task_id, value))
            
        elif action == 'dest':
            if 'admin_choice_future' in context and not context['admin_choice_future'].done():
                context['admin_choice_future'].set_result(value)

async def handle_video_download(task_id, quality):
    async with DOWNLOAD_SEMAPHORE:
        context = USER_CONTEXT.get(task_id)
        if not context: return
        
        status_message, url, title = context['status_message'], context['url'], context['title']
        main_loop = asyncio.get_running_loop()
        video_path, thumb_path = None, None

        try:
            format_code = 'bestaudio/best' if quality == 'audio' else f'bestvideo[height<={quality}][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4][height<={quality}]/best'
            ext = 'mp3' if quality == 'audio' else 'mp4'

            last_update_time = 0
            def progress_hook(d):
                nonlocal last_update_time
                if USER_CONTEXT.get(task_id, {}).get('cancelled'): raise yt_dlp.utils.DownloadError("Cancelled by user.")
                if d['status'] == 'downloading' and time.time() - last_update_time > 2.5:
                    downloaded_bytes = d.get('downloaded_bytes', 0)
                    total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                    
                    if total_bytes > 0:
                        p = (downloaded_bytes / total_bytes) * 100
                        bar = "■" * int(p / 10) + "□" * (10 - int(p / 10))
                        
                        # -- REVISED CLEANER PROGRESS DISPLAY --
                        progress_text = (
                            f"**📥 Downloading...**\n`{title}`\n\n"
                            f"╔═⍟`{bar} {p:.1f}%`\n"
                            f"╠════◆\n╠`{human_readable_size(downloaded_bytes)} / {human_readable_size(total_bytes)}`\n╚═══◆\n"
                            f"╔══◆**Speed:** `{d.get('_speed_str', 'N/A').strip()}` \n╚══◆**ETA:** `{d.get('_eta_str', 'N/A').strip()}`"
                        )
                        # ------------------------------------

                        asyncio.run_coroutine_threadsafe(
                            status_message.edit(
                                progress_text,
                                buttons=[[Button.inline("❌ Cancel Download", f"cancel:dl:{task_id}")]]
                            ),
                            main_loop
                        )
                        last_update_time = time.time()
            
            output_template = os.path.join(DOWNLOAD_DIR, f'file_{task_id}.%(ext)s')
            ydl_opts = {'outtmpl': output_template, 'noplaylist': True, 'quiet': True, 'progress_hooks': [progress_hook],
                        'cookiefile': COOKIE_FILE_PATH if os.path.exists(COOKIE_FILE_PATH) else None, 'format': format_code,
                        'retries': 10, 'fragment_retries': 10}
            
            if IS_ARIA2C_AVAILABLE:
                logger.info("Aria2c is available. Using it for faster downloads.")
                ydl_opts.update({
                    'external_downloader': 'aria2c',
                    'external_downloader_args': [
                        '--min-split-size=1M',
                        '--max-connection-per-server=16',
                        '--max-concurrent-downloads=16',
                        '--split=16'
                    ]
                })

            if quality == 'audio': ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}]

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                await asyncio.to_thread(ydl.download, [url])
            
            possible_files = glob.glob(os.path.join(DOWNLOAD_DIR, f'file_{task_id}.*'))
            video_path = next((f for f in possible_files if not f.endswith('.part')), None)
            if video_path and not video_path.endswith(f'.{ext}'):
                final_path = os.path.splitext(video_path)[0] + f'.{ext}'
                os.rename(video_path, final_path)
                video_path = final_path

            if USER_CONTEXT.get(task_id, {}).get('cancelled'):
                return await status_message.edit("🚫 **Download Cancelled**", buttons=None)
            if not video_path or not os.path.exists(video_path): raise FileNotFoundError("Downloaded file not found.")

            file_size = os.path.getsize(video_path)
            DOWNLOAD_STATS[date.today().isoformat()] = DOWNLOAD_STATS.get(date.today().isoformat(), 0) + file_size

            thumb_path = glob.glob(os.path.join(DOWNLOAD_DIR, f'file_{task_id}*.webp'))
            thumb_path = thumb_path[0] if thumb_path else await generate_thumbnail(video_path, task_id, context.get('duration'))
            
            if USER_CONTEXT.get(task_id, {}).get('cancelled'): return await status_message.edit("🚫 **Download Cancelled**", buttons=None)
            if file_size > TELEGRAM_UPLOAD_LIMIT_BYTES: return await status_message.edit(f"❌ **Error:** File is `{human_readable_size(file_size)}`, which is larger than Telegram's 2GB limit.")

            await status_message.edit("✅ **Download ပြီးပါပြီ**\n\n⬆️ ယခု Telegram သို့ တင်နေပါသည်...", buttons=None)
            
            file_size_str = human_readable_size(file_size)
            final_caption = f"**{title}**\n\n💾 **Size:** `{file_size_str}`\n\n**Bot အများကြီးထဲမှဒီBot လေးကိုရွေးပြီးအသုံးပြုလို့ များစွာပိတီဖြစ်မိပါတယ် ဒီထက်ပိုကောင်းမွန်အောင် အများကြီးကြိုးစားပါအုံးမယ်ဗျာ \n\n**╔═◆အကူအညီရယူရန်\n╚═════◆Bot Owner @Hub_Offical "
            sender_client, destination = bot, context['user_id']
            
            if context['user_id'] == ADMIN_ID:
                future = main_loop.create_future()
                USER_CONTEXT[task_id]['admin_choice_future'] = future
                admin_buttons = [[Button.inline("➡️ Channel သို့ ပို့ရန်", f"dest:channel:{task_id}")], [Button.inline("👤 ကျွန်ုပ်ထံ ပို့ရန်", f"dest:me:{task_id}")]]
                await status_message.edit("**Admin Action:** ဖိုင်ကို ဘယ်နေရာသို့ ပို့လိုပါသလဲ?", buttons=admin_buttons)
                try:
                    admin_choice = await asyncio.wait_for(future, timeout=60.0)
                    if admin_choice == 'channel':
                        logger.info("Admin chose to send to channel. Preparing uploader client.")
                        if not await uploader.is_user_authorized():
                            await status_message.edit("❌ **Error:** Uploader account is not logged in. Please /login first.", buttons=None)
                            raise Exception("Uploader not authorized")
                        
                        destination_id_str = await db.get_setting('UPLOAD_CHANNEL_ID')
                        if not destination_id_str or destination_id_str == '-1000000000000':
                            await status_message.edit("❌ **Error:** Upload Channel ID is not set in Admin Panel.", buttons=None)
                            raise Exception("Upload Channel ID not configured")

                        destination, sender_client = int(destination_id_str), uploader
                        await status_message.edit(f"✅ Upload Channel ({destination}) သို့ `Uploader Account` ဖြင့် ပို့ပါမည်...", buttons=None)

                except asyncio.TimeoutError: 
                    await status_message.edit("⏰ အချိန်စေ့သွားပါပြီ။ သင့်ထံသို့ `Bot` မှတဆင့် တိုက်ရိုက်ပို့ပါမည်။", buttons=None)

            last_upload_update_time = 0
            async def upload_progress_callback(current, total):
                nonlocal last_upload_update_time
                if time.time() - last_upload_update_time > 2.5:
                    p = (current / total) * 100
                    bar = "■" * int(p / 10) + "□" * (10 - int(p / 10))
                    try: 
                        await status_message.edit(
                            f"**➲ Sending...**\n`{title}`\n\n╔═══⍟\n╠`{bar} {p:.1f}%`\n╚═════⍟",
                            buttons=[[Button.inline("❌ Cancel Upload", f"cancel:dl:{task_id}")]]
                        )
                    except FloodWaitError as fwe: await asyncio.sleep(fwe.seconds)
                    except (MessageNotModifiedError, Exception): pass
                    last_upload_update_time = time.time()
            
                        # Attributes သတ်မှတ်ခြင်း (Video နဲ့ Audio ခွဲခြားသတ်မှတ်သည်)
            duration = int(context.get('duration', 0))
            if ext == 'mp4':
                attrs = [DocumentAttributeVideo(
                    duration=duration, 
                    w=0, h=0, 
                    supports_streaming=True
                )]
            elif ext == 'mp3':
                attrs = [DocumentAttributeAudio(
                    duration=duration,
                    title=title,  # သီချင်းခေါင်းစဉ်
                    performer="Hub Downloader" # Artist နေရာမှာ ပြမည့်အမည် (ကြိုက်တာပြောင်းလို့ရပါတယ်)
                )]
            else:
                attrs = []

            
            logger.info(f"Uploading file '{video_path}' to destination '{destination}' using {'Uploader' if sender_client == uploader else 'Bot'} client.")
            sent_message = await sender_client.send_file(destination, video_path, thumb=thumb_path, caption=final_caption, progress_callback=upload_progress_callback, attributes=attrs)
            
            if USER_CONTEXT.get(task_id, {}).get('cancelled'):
                logger.info(f"Task {task_id} was cancelled during upload. Deleting uploaded file.")
                await sender_client.delete_messages(destination, sent_message)
                return await status_message.edit("🚫 **Upload Cancelled**\nUploaded file has been deleted.", buttons=None)
            
            await status_message.edit("🎉 **ပြီးပါပြီ!** သင်၏ဖိုင်ကို ပေးပို့ပြီးပါပြီ။"); await asyncio.sleep(5); await status_message.delete()
            
            await db.increment_bot_stat('total_downloads')

            # --- REVISED ADMIN NOTIFICATION LOGIC ---
            if context['user_id'] == ADMIN_ID and destination != ADMIN_ID:
                try:
                    dest_id = int(str(destination).replace('-100', ''))
                    post_link = f"https://t.me/c/{dest_id}/{sent_message.id}"
                    
                    # Fetch the upload channel invite link
                    upload_invite_link = await db.get_setting('UPLOAD_CHANNEL_INVITE_LINK')

                    # Prepare buttons
                    notification_buttons = [Button.url("🔗 View Post", post_link)]
                    if upload_invite_link and upload_invite_link != 'https://t.me/your_upload_channel_link':
                        notification_buttons.append(Button.url("📤 Channel Link", upload_invite_link))
                    
                    # Prepare caption for the notification
                    notification_caption = f"✅ **Post Created Successfully!**\n\n{final_caption}"
            
                    # Send notification with thumbnail
                    if thumb_path and os.path.exists(thumb_path):
                        await bot.send_file(
                            ADMIN_ID,
                            file=thumb_path,
                            caption=notification_caption,
                            buttons=[notification_buttons]
                        )
                    else: # Fallback to text if no thumbnail
                        await bot.send_message(
                            ADMIN_ID,
                            notification_caption,
                            buttons=[notification_buttons]
                        )

                except Exception as e:
                    logger.error(f"Failed to send enhanced admin notification: {e}")
                    await bot.send_message(ADMIN_ID, f"✅ Post Created Successfully, but notification failed.\n\n(Could not generate post link for destination: {destination})")
            # ----------------------------------------

        except yt_dlp.utils.DownloadError as de:
            error_msg = str(de)
            if "cancelled by user" in error_msg: await status_message.edit("🚫 **Download Cancelled**", buttons=None)
            else: await status_message.edit(f"❌ **Download Error!**\n`{error_msg.split(': ERROR: ')[-1]}`")
        except Exception as e:
            logger.error(f"VIDEO HANDLING ERROR (Task ID: {task_id}): {e}", exc_info=True)
            error_message = f"❌ **An unexpected error occurred!**\nAdmin has been notified.\n`{e}`"
            if "bot is not a participant" in str(e).lower() or "chat_admin_required" in str(e).lower():
                error_message += "\n\n**Hint:** Uploader account ကို Channel ထဲမှာ Admin အဖြစ် ထည့်သွင်းထားရဲ့လား စစ်ဆေးပေးပါ။"
            await status_message.edit(error_message)
        finally:
            for f in glob.glob(os.path.join(DOWNLOAD_DIR, f'*{task_id}*')):
                if os.path.exists(f): os.remove(f)
            if task_id in USER_CONTEXT: del USER_CONTEXT[task_id]

# ==============================================================================
# --- MAIN EXECUTION ---
# ==============================================================================
async def main():
    global MAX_CONCURRENT_DOWNLOADS, DOWNLOAD_SEMAPHORE, BROADCAST_USERS, BANNED_USERS
    cleanup_downloads()
    await db.init_db()

    # Load initial settings from async DB
    MAX_CONCURRENT_DOWNLOADS = int(await db.get_setting('MAX_CONCURRENT_DOWNLOADS', 10))
    DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)
    BROADCAST_USERS = await db.load_users_from_db()
    BANNED_USERS = await db.load_banned_users_from_db()
    logger.info(f"Loaded {len(BROADCAST_USERS)} users and {len(BANNED_USERS)} banned users from DB.")
    
    if IS_ARIA2C_AVAILABLE:
        logger.info("✅ aria2c is installed and will be used for downloads.")
    else:
        logger.warning("⚠️ aria2c is NOT installed. Downloads may be slower. Install it with 'sudo apt install aria2'.")

    await bot.start(bot_token=BOT_TOKEN)
    logger.info("Bot client စတင်လည်ပတ်နေပါပြီ။")
    
    await uploader.connect()
    if not await uploader.is_user_authorized():
        logger.warning("Uploader client Login မဝင်ရသေးပါ။")
        try: await bot.send_message(ADMIN_ID, "⚠️ **Bot Started**\nUploader Account Login မဝင်ရသေးပါ။ /login command ဖြင့် login ဝင်ပေးပါ။")
        except Exception as e: logger.error(f"Admin ဆီသို့ startup message ပို့မရပါ: {e}")
    else:
        me = await uploader.get_me()
        logger.info(f"Uploader client အဖြစ် {me.first_name} ဖြင့် Login ဝင်ထားပါသည်။")
    
    logger.info(f"Bot အပြည့်အဝ အလုပ်လုပ်နေပါပြီ။")
    await bot.run_until_disconnected()

if __name__ == '__main__':
    try: asyncio.run(main())
    except (KeyboardInterrupt, SystemExit): logger.info("Bot ကို အောင်မြင်စွာ ရပ်တန့်လိုက်ပါသည်။")
    except Exception as e: logger.critical("Bot စတင်ရာတွင် သို့မဟုတ် အလုပ်လုပ်ရာတွင် အမှားအယွင်းကြီးတစ်ခု ဖြစ်ပေါ်နေပါသည်။", exc_info=True)


