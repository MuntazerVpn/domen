import os
import json
import sqlite3
import random
import string
from datetime import datetime, timezone
from typing import Optional, List, Tuple

import requests
from dotenv import load_dotenv

from telegram import (
    Update,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================== Config ==================
load_dotenv()

TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
CF_API_TOKEN = os.getenv("CF_API_TOKEN")
CF_ZONE_ID = os.getenv("CF_ZONE_ID")
CF_BASE_DOMAIN = os.getenv("CF_BASE_DOMAIN")

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "5"))
DB_PATH = os.getenv("DB_PATH", "database/bot.db")

WEBHOOK_BASE_URL = (os.getenv("WEBHOOK_BASE_URL") or "").rstrip("/")
PORT = int(os.getenv("PORT", "8080"))

CF_API = "https://api.cloudflare.com/client/v4"

missing = [k for k, v in {
    "TG_BOT_TOKEN": TG_BOT_TOKEN,
    "CF_API_TOKEN": CF_API_TOKEN,
    "CF_ZONE_ID": CF_ZONE_ID,
    "CF_BASE_DOMAIN": CF_BASE_DOMAIN,
}.items() if not v]
if missing:
    raise RuntimeError("❌ Missing env vars: " + ", ".join(missing))

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

# ================== DB ==================
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS quota (
    user_id INTEGER PRIMARY KEY,
    used INTEGER DEFAULT 0,
    bonus INTEGER DEFAULT 0,
    last_date TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS domains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    subdomain TEXT,
    ip TEXT,
    created_at TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    first_name TEXT,
    username TEXT,
    joined_at TEXT,
    banned INTEGER DEFAULT 0,
    referred_by INTEGER,
    ref_rewarded INTEGER DEFAULT 0,
    lang TEXT
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")

# defaults
cur.execute("""
INSERT OR IGNORE INTO settings (key, value)
VALUES ('welcome_message_ar', '👋 مرحبًا بك\\n\\n✅ اضغط زر 🔗 ربط IP ثم أرسل IP فقط.')
""")
cur.execute("""
INSERT OR IGNORE INTO settings (key, value)
VALUES ('welcome_message_en', '👋 Welcome\\n\\n✅ Tap 🔗 Link IP then send IP only.')
""")
cur.execute("""
INSERT OR IGNORE INTO settings (key, value)
VALUES ('help_message_ar', 'ℹ️ المساعدة\\n\\n1) اضغط 🔗 ربط IP\\n2) أرسل IP فقط\\n3) راح ينشئ دومين عشوائي + A + NS\\n\\n⏱️ الحد اليومي: 5')
""")
cur.execute("""
INSERT OR IGNORE INTO settings (key, value)
VALUES ('help_message_en', 'ℹ️ Help\\n\\n1) Tap 🔗 Link IP\\n2) Send IP only\\n3) It will create random domain + A + NS\\n\\n⏱️ Daily limit: 5')
""")
cur.execute("""
INSERT OR IGNORE INTO settings (key, value)
VALUES ('bot_status', 'on')
""")
cur.execute("""
INSERT OR IGNORE INTO settings (key, value)
VALUES ('force_channels', ?)
""", (json.dumps(["@eshop_2"]),))

conn.commit()

# ================== i18n ==================
TXT = {
    "ar": {
        "btn_link_ip": "🔗 ربط IP",
        "btn_my_domains": "📂 دوميناتي",
        "btn_invite": "🎁 رابط دعوتي",
        "btn_quota": "📊 المتبقي اليومي",
        "btn_help": "🆘 مساعدة",
        "btn_admin": "🛠 لوحة الأدمن",

        "ask_ip": "📥 أرسل IP الآن:",
        "quota_admin": "👑 أنت أدمن — بدون حدود محاولات ✅",
        "not_allowed_daily": "❌ وصلت الحد اليومي. جرّب باچر.",
        "no_domains": "📂 ما عندك دومينات مضافة لحد الآن.",
        "bot_off": "⛔ البوت متوقف مؤقتًا.",
        "banned": "⛔ تم حظرك من استخدام البوت.",
        "must_sub": "🔒 يجب الاشتراك في القناة/القنوات أولاً.\n\nبعد الاشتراك اضغط ✅ تحقق من الاشتراك",
        "sub_bad": "❌ لست مشتركًا بعد.\nاشترك ثم اضغط ✅ تحقق من الاشتراك",
        "sub_ok": "✅ تم التحقق! تفضل استخدم البوت.",
        "lang_choose": "🌐 اختر اللغة:",
        "lang_saved": "✅ تم حفظ اللغة.",
        "back_main": "✅ رجعناك للقائمة الرئيسية",

        "admin_title": "🛠 لوحة تحكم الأدمن",
        "admin_users": "👥 إدارة المستخدمين",
        "admin_stats": "📊 إحصائيات",
        "admin_ban": "🚫 حظر مستخدم",
        "admin_unban": "✅ رفع حظر",
        "admin_broadcast": "📢 إذاعة",
        "admin_channels": "📣 قنوات الاشتراك الإجباري",
        "admin_stop": "⏸️ إيقاف البوت",
        "admin_start": "▶️ تشغيل البوت",
        "admin_edit_welcome": "✏️ تعديل رسالة الترحيب",
        "admin_edit_help": "🆘 تعديل المساعدة",
        "admin_back": "🔙 رجوع",

        "ask_user_id_ban": "🆔 أرسل ID المستخدم للحظر:",
        "ask_user_id_unban": "🆔 أرسل ID المستخدم لرفع الحظر:",
        "ask_new_welcome": "✏️ أرسل رسالة الترحيب الجديدة الآن:",
        "ask_new_help": "🆘 أرسل رسالة المساعدة الجديدة الآن:",
        "ask_broadcast": "📢 أرسل رسالة الإذاعة الآن:",
        "ban_done": "🚫 تم حظر المستخدم: {id}",
        "unban_done": "✅ تم رفع الحظر عن: {id}",
        "welcome_updated": "✅ تم تحديث رسالة الترحيب.",
        "help_updated": "✅ تم تحديث رسالة المساعدة.",
        "broadcast_done": "📢 تم إكمال الإذاعة\n\n✅ نجح: {ok}\n❌ فشل: {fail}\n👥 الإجمالي: {total}",
        "stopped": "⛔ تم إيقاف البوت.",
        "started": "✅ تم تشغيل البوت.",

        "channels_menu": "📣 إدارة قنوات الاشتراك الإجباري",
        "ch_show": "📋 عرض القنوات",
        "ch_add": "➕ إضافة قناة",
        "ch_del": "🗑️ حذف قناة",
        "ch_back": "🔙 رجوع",
        "ask_ch_add": "➕ أرسل معرف القناة مثل: @channel (أو بدون @)",
        "ask_ch_del": "🗑️ أرسل معرف القناة لحذفها مثل: @channel",
        "ch_added": "✅ تم إضافة القناة.",
        "ch_deleted": "✅ تم حذف القناة (إن كانت موجودة).",
        "ch_list": "📣 القنوات الحالية:\n{list}",

        "copy": "📋 نسخ الاسم",
        "delete": "🗑️ حذف",
        "rebind": "🔁 إعادة ربط",
        "confirm_delete": "🔒 تأكيد الحذف",
        "cancel": "❌ إلغاء",
        "cancelled": "❌ تم إلغاء العملية.",
        "del_ask": "⚠️ هل أنت متأكد؟\n\n🌐 {sub}",
        "deleted": "🗑️ تم حذف:\n{sub}",
        "rebind_ask": "🔁 أرسل IP الجديد لـ:\n{sub}",

        "invite_text": "🎁 رابط دعوتك:\n{link}\n\n✅ إذا دخل شخص جديد عبر رابطك → تنضاف لك محاولة (+1).",
        "invite_reward": "🎉 تم قبول دعوة جديدة!\n✅ تم إضافة محاولة إضافية لك (+1).",
    },
    "en": {
        "btn_link_ip": "🔗 Link IP",
        "btn_my_domains": "📂 My Domains",
        "btn_invite": "🎁 My Invite Link",
        "btn_quota": "📊 Daily Remaining",
        "btn_help": "🆘 Help",
        "btn_admin": "🛠 Admin Panel",

        "ask_ip": "📥 Send IP now:",
        "quota_admin": "👑 You are Admin — Unlimited attempts ✅",
        "not_allowed_daily": "❌ Daily limit reached. Try tomorrow.",
        "no_domains": "📂 You don't have any domains yet.",
        "bot_off": "⛔ Bot is temporarily paused.",
        "banned": "⛔ You are banned from using this bot.",
        "must_sub": "🔒 You must join the required channel(s) first.\n\nAfter joining, tap ✅ Check Subscription",
        "sub_bad": "❌ Not subscribed yet.\nJoin then tap ✅ Check Subscription",
        "sub_ok": "✅ Verified! You can use the bot now.",
        "lang_choose": "🌐 Choose language:",
        "lang_saved": "✅ Language saved.",
        "back_main": "✅ Back to main menu",

        "admin_title": "🛠 Admin Panel",
        "admin_users": "👥 Users",
        "admin_stats": "📊 Stats",
        "admin_ban": "🚫 Ban User",
        "admin_unban": "✅ Unban User",
        "admin_broadcast": "📢 Broadcast",
        "admin_channels": "📣 Forced Channels",
        "admin_stop": "⏸️ Stop Bot",
        "admin_start": "▶️ Start Bot",
        "admin_edit_welcome": "✏️ Edit Welcome",
        "admin_edit_help": "🆘 Edit Help",
        "admin_back": "🔙 Back",

        "ask_user_id_ban": "🆔 Send user ID to ban:",
        "ask_user_id_unban": "🆔 Send user ID to unban:",
        "ask_new_welcome": "✏️ Send new welcome message now:",
        "ask_new_help": "🆘 Send new help message now:",
        "ask_broadcast": "📢 Send broadcast message now:",
        "ban_done": "🚫 Banned user: {id}",
        "unban_done": "✅ Unbanned user: {id}",
        "welcome_updated": "✅ Welcome message updated.",
        "help_updated": "✅ Help message updated.",
        "broadcast_done": "📢 Broadcast completed\n\n✅ Sent: {ok}\n❌ Failed: {fail}\n👥 Total: {total}",
        "stopped": "⛔ Bot stopped.",
        "started": "✅ Bot started.",

        "channels_menu": "📣 Forced Channels",
        "ch_show": "📋 Show channels",
        "ch_add": "➕ Add channel",
        "ch_del": "🗑️ Remove channel",
        "ch_back": "🔙 Back",
        "ask_ch_add": "➕ Send channel username like: @channel (or without @)",
        "ask_ch_del": "🗑️ Send channel username to remove like: @channel",
        "ch_added": "✅ Channel added.",
        "ch_deleted": "✅ Channel removed (if existed).",
        "ch_list": "📣 Current channels:\n{list}",

        "copy": "📋 Copy",
        "delete": "🗑️ Delete",
        "rebind": "🔁 Re-link",
        "confirm_delete": "🔒 Confirm delete",
        "cancel": "❌ Cancel",
        "cancelled": "❌ Cancelled.",
        "del_ask": "⚠️ Are you sure?\n\n🌐 {sub}",
        "deleted": "🗑️ Deleted:\n{sub}",
        "rebind_ask": "🔁 Send new IP for:\n{sub}",

        "invite_text": "🎁 Your invite link:\n{link}\n\n✅ If a new user joins via your link → you get +1 attempt.",
        "invite_reward": "🎉 New referral accepted!\n✅ You received +1 attempt.",
    }
}


def t(lang: str, key: str) -> str:
    lang = lang if lang in TXT else "ar"
    return TXT[lang].get(key, TXT["ar"].get(key, key))


def get_user_lang(uid: int) -> str:
    cur.execute("SELECT lang FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    if row and row[0] in ("ar", "en"):
        return row[0]
    return "ar"


def set_user_lang(uid: int, lang: str) -> None:
    cur.execute("UPDATE users SET lang=? WHERE user_id=?", (lang, uid))
    conn.commit()


# ================== Helpers ==================
def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def get_setting(key: str, default: str = "") -> str:
    cur.execute("SELECT value FROM settings WHERE key=?", (key,))
    row = cur.fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    cur.execute("""
    INSERT INTO settings(key,value) VALUES(?,?)
    ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (key, value))
    conn.commit()


def bot_is_on() -> bool:
    return get_setting("bot_status", "on") == "on"


def random_label(length: int = 6) -> str:
    chars = string.ascii_lowercase + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def cf_headers():
    return {"Authorization": f"Bearer {CF_API_TOKEN}", "Content-Type": "application/json"}


def cf_find_record(name: str, rtype: str) -> Optional[dict]:
    params = {"type": rtype, "name": name}
    r = requests.get(f"{CF_API}/zones/{CF_ZONE_ID}/dns_records", headers=cf_headers(), params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(str(data))
    results = data.get("result", [])
    return results[0] if results else None


def cf_upsert_record(rtype: str, name: str, content: str, proxied: bool = False, ttl: int = 1) -> dict:
    existing = cf_find_record(name, rtype)
    payload = {"type": rtype, "name": name, "content": content, "ttl": ttl}
    if rtype in ("A", "AAAA", "CNAME"):
        payload["proxied"] = proxied

    if existing:
        rid = existing["id"]
        r = requests.put(
            f"{CF_API}/zones/{CF_ZONE_ID}/dns_records/{rid}",
            headers=cf_headers(),
            json=payload,
            timeout=20
        )
    else:
        r = requests.post(
            f"{CF_API}/zones/{CF_ZONE_ID}/dns_records",
            headers=cf_headers(),
            json=payload,
            timeout=20
        )
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(str(data))
    return data["result"]


def cf_delete_records(name: str, rtype: str) -> int:
    params = {"type": rtype, "name": name}
    r = requests.get(f"{CF_API}/zones/{CF_ZONE_ID}/dns_records", headers=cf_headers(), params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(str(data))
    results = data.get("result", [])
    deleted = 0
    for rec in results:
        rid = rec["id"]
        rr = requests.delete(f"{CF_API}/zones/{CF_ZONE_ID}/dns_records/{rid}", headers=cf_headers(), timeout=20)
        rr.raise_for_status()
        d2 = rr.json()
        if d2.get("success"):
            deleted += 1
    return deleted


def register_user(update: Update) -> bool:
    u = update.effective_user
    uid = u.id
    first_name = u.first_name or ""
    username = u.username or ""

    cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
    exists = cur.fetchone() is not None
    if not exists:
        cur.execute(
            "INSERT INTO users (user_id, first_name, username, joined_at, banned, referred_by, ref_rewarded, lang) "
            "VALUES (?,?,?,?,0,NULL,0,NULL)",
            (uid, first_name, username, now_iso())
        )
        conn.commit()
        return True
    else:
        cur.execute("UPDATE users SET first_name=?, username=? WHERE user_id=?", (first_name, username, uid))
        conn.commit()
        return False


def user_is_banned(uid: int) -> bool:
    cur.execute("SELECT banned FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    return bool(row and row[0] == 1)


def ensure_quota_row(uid: int) -> None:
    today = today_iso()
    cur.execute("SELECT user_id FROM quota WHERE user_id=?", (uid,))
    row = cur.fetchone()
    if not row:
        cur.execute("INSERT INTO quota (user_id, used, bonus, last_date) VALUES (?,?,?,?)", (uid, 0, 0, today))
        conn.commit()


def reset_quota_if_new_day(uid: int) -> Tuple[int, int]:
    today = today_iso()
    ensure_quota_row(uid)
    cur.execute("SELECT used, bonus, last_date FROM quota WHERE user_id=?", (uid,))
    used, bonus, last_date = cur.fetchone()

    if last_date != today:
        used = 0
        cur.execute("UPDATE quota SET used=0, last_date=? WHERE user_id=?", (today, uid))
        conn.commit()

    return used, bonus


def add_bonus_attempt(uid: int, amount: int = 1) -> None:
    ensure_quota_row(uid)
    cur.execute("UPDATE quota SET bonus=bonus+? WHERE user_id=?", (amount, uid))
    conn.commit()


def consume_attempt(uid: int) -> Tuple[bool, int]:
    if is_admin(uid):
        return True, 999999

    used, bonus = reset_quota_if_new_day(uid)
    limit = DAILY_LIMIT + bonus

    if used >= limit:
        return False, 0

    cur.execute("UPDATE quota SET used=used+1 WHERE user_id=?", (uid,))
    conn.commit()

    remaining = (limit - (used + 1))
    return True, remaining


def get_today_stats(uid: int) -> Tuple[int, int, int]:
    if is_admin(uid):
        return 0, 0, 999999
    used, bonus = reset_quota_if_new_day(uid)
    return used, bonus, DAILY_LIMIT + bonus


def get_force_channels() -> List[str]:
    raw = get_setting("force_channels", "[]")
    try:
        arr = json.loads(raw)
        if isinstance(arr, list):
            out = []
            for c in arr:
                if not isinstance(c, str):
                    continue
                c = c.strip()
                if not c:
                    continue
                if not c.startswith("@"):
                    c = "@" + c
                out.append(c)
            return out
    except:
        pass
    return ["@eshop_2"]


async def is_user_subscribed(bot, uid: int) -> Tuple[bool, str]:
    if is_admin(uid):
        return True, ""

    channels = get_force_channels()
    if not channels:
        return True, ""

    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch, user_id=uid)
            status = str(member.status).lower()
            if status in ("member", "administrator", "creator"):
                continue
            return False, f"{ch} | status={status}"
        except Exception as e:
            reason = str(e)
            if ADMIN_ID:
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        f"⚠️ SUB CHECK ERROR\nChannel: {ch}\nUser: {uid}\nReason: {reason}"
                    )
                except:
                    pass
            return False, f"{ch} | error={reason}"

    return True, ""


def force_join_keyboard(lang: str, channels: List[str]) -> InlineKeyboardMarkup:
    btns = []
    for ch in channels[:3]:
        username = ch.lstrip("@")
        btns.append([InlineKeyboardButton(f"🔗 {ch}", url=f"https://t.me/{username}")])
    btns.append([InlineKeyboardButton("✅ Check Subscription", callback_data="checksub")])
    btns.append([InlineKeyboardButton("🌐 Language / اللغة", callback_data="lang")])
    return InlineKeyboardMarkup(btns)


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🇮🇶 العربية", callback_data="setlang|ar"),
         InlineKeyboardButton("🇬🇧 English", callback_data="setlang|en")]
    ])


# ================== Keyboards ==================
def main_keyboard(lang: str, uid: int) -> ReplyKeyboardMarkup:
    kb = [
        [t(lang, "btn_link_ip")],
        [t(lang, "btn_my_domains")],
        [t(lang, "btn_invite"), t(lang, "btn_quota")],
        [t(lang, "btn_help")],
        ["🌐 اللغة / Language"]
    ]
    if is_admin(uid):
        kb.append([t(lang, "btn_admin")])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)


def admin_keyboard(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [t(lang, "admin_users"), t(lang, "admin_stats")],
            [t(lang, "admin_ban"), t(lang, "admin_unban")],
            [t(lang, "admin_broadcast")],
            [t(lang, "admin_channels")],
            [t(lang, "admin_stop"), t(lang, "admin_start")],
            [t(lang, "admin_edit_welcome")],
            [t(lang, "admin_edit_help")],
            [t(lang, "admin_back")]
        ],
        resize_keyboard=True
    )


def forced_channels_admin_keyboard(lang: str) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [t(lang, "ch_show")],
            [t(lang, "ch_add"), t(lang, "ch_del")],
            [t(lang, "ch_back")]
        ],
        resize_keyboard=True
    )


def domains_inline_keyboard(lang: str, subdomain: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(t(lang, "delete"), callback_data=f"askdel|{subdomain}"),
            InlineKeyboardButton(t(lang, "copy"), callback_data=f"copy|{subdomain}"),
        ],
        [
            InlineKeyboardButton(t(lang, "rebind"), callback_data=f"rebind|{subdomain}")
        ]
    ])


def confirm_delete_keyboard(lang: str, subdomain: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(t(lang, "confirm_delete"), callback_data=f"confirm|{subdomain}")],
        [InlineKeyboardButton(t(lang, "cancel"), callback_data="cancel")]
    ])


# ================== Referral ==================
def parse_ref_from_start(arg: str) -> Optional[int]:
    if not arg:
        return None
    if arg.startswith("ref_"):
        try:
            return int(arg.split("_", 1)[1])
        except:
            return None
    return None


def reward_referral_if_needed(new_uid: int, ref_uid: int) -> bool:
    if ref_uid == new_uid:
        return False

    cur.execute("SELECT referred_by, ref_rewarded FROM users WHERE user_id=?", (new_uid,))
    row = cur.fetchone()
    if not row:
        return False

    referred_by, ref_rewarded = row
    if ref_rewarded == 1:
        return False
    if referred_by is not None:
        return False

    cur.execute("UPDATE users SET referred_by=?, ref_rewarded=1 WHERE user_id=?", (ref_uid, new_uid))
    conn.commit()
    add_bonus_attempt(ref_uid, 1)
    return True


def get_invite_link(bot_username: str, uid: int) -> str:
    return f"https://t.me/{bot_username}?start=ref_{uid}"


# ================== Report ==================
def connection_report(ip: str, fqdn: str, ns_name: str, bot_username: str) -> str:
    return (
        "✅ Connection Status Report\n"
        "Overall Status: Successfully Linked 🎉\n"
        "DNS Configuration Details:\n"
        "📍 A Record (IPv4):\n"
        f"{ip}\n"
        "🌐 Domain URL:\n"
        f"{fqdn}\n"
        "⚙️ Nameserver (NS):\n"
        f"{ns_name}\n"
        f"Created by @{bot_username}"
    )


# ================== Admin notify ==================
async def notify_admin_new_user(context: ContextTypes.DEFAULT_TYPE, update: Update):
    if not ADMIN_ID:
        return
    uid = update.effective_user.id
    uname = update.effective_user.username
    uname = f"@{uname}" if uname else "-"
    cur.execute("SELECT COUNT(*) FROM users")
    total_users = cur.fetchone()[0]
    await context.bot.send_message(
        ADMIN_ID,
        f"👤 New user joined\n\nID: {uid}\nName: {update.effective_user.first_name or '-'}\nUser: {uname}\nTotal: {total_users}"
    )


# ================== Start ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    is_new = register_user(update)
    if is_new:
        await notify_admin_new_user(context, update)

    # referral
    ref_uid = None
    if context.args:
        ref_uid = parse_ref_from_start(context.args[0])
    if ref_uid and is_new:
        rewarded = reward_referral_if_needed(uid, ref_uid)
        if rewarded:
            try:
                lang_ref = get_user_lang(ref_uid)
                await context.bot.send_message(ref_uid, t(lang_ref, "invite_reward"))
            except:
                pass

    lang = get_user_lang(uid)

    # language selection if not set yet
    cur.execute("SELECT lang FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    if row and (row[0] is None or row[0] not in ("ar", "en")):
        await update.message.reply_text(t(lang, "lang_choose"), reply_markup=language_keyboard())
        return

    if not bot_is_on() and not is_admin(uid):
        await update.message.reply_text(t(lang, "bot_off"))
        return
    if user_is_banned(uid) and not is_admin(uid):
        await update.message.reply_text(t(lang, "banned"))
        return

    ok, info = await is_user_subscribed(context.bot, uid)
    if not ok:
        channels = get_force_channels()
        if is_admin(uid) and info:
            await update.message.reply_text(f"⚠️ {info}")
        await update.message.reply_text(t(lang, "must_sub"), reply_markup=force_join_keyboard(lang, channels))
        return

    welcome = get_setting("welcome_message_ar" if lang == "ar" else "welcome_message_en", "")
    if not welcome:
        welcome = t(lang, "btn_help")
    await update.message.reply_text(welcome, reply_markup=main_keyboard(lang, uid))


# ================== Admin Text Handlers ==================
async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, lang: str) -> bool:
    uid = update.effective_user.id
    if not is_admin(uid):
        return False

    if text == t(lang, "admin_back"):
        await update.message.reply_text(t(lang, "back_main"), reply_markup=main_keyboard(lang, uid))
        return True

    if text == t(lang, "btn_admin"):
        await update.message.reply_text(t(lang, "admin_title"), reply_markup=admin_keyboard(lang))
        return True

    if text == t(lang, "admin_stats"):
        cur.execute("SELECT COUNT(*) FROM users")
        users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM domains")
        domains = cur.fetchone()[0]
        bot_status = "✅ ON" if bot_is_on() else "⛔ OFF"
        channels = get_force_channels()
        await update.message.reply_text(
            f"📊 Stats\n\nUsers: {users}\nDomains: {domains}\nBot: {bot_status}\nChannels: {', '.join(channels) if channels else '-'}",
            reply_markup=admin_keyboard(lang)
        )
        return True

    if text == t(lang, "admin_users"):
        cur.execute("SELECT COUNT(*) FROM users")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users WHERE banned=1")
        banned = cur.fetchone()[0]
        cur.execute("SELECT user_id, first_name, username, joined_at FROM users ORDER BY joined_at DESC LIMIT 15")
        rows = cur.fetchall()
        msg = f"👥 Users\n\nTotal: {total}\nBanned: {banned}\n\nLast 15:\n"
        for r in rows:
            u_id, fn, un, j = r
            un = f"@{un}" if un else "-"
            msg += f"• {u_id} | {fn or '-'} | {un} | {j[:19]}\n"
        await update.message.reply_text(msg, reply_markup=admin_keyboard(lang))
        return True

    if text == t(lang, "admin_ban"):
        context.user_data["admin_wait_ban"] = True
        await update.message.reply_text(t(lang, "ask_user_id_ban"), reply_markup=admin_keyboard(lang))
        return True

    if text == t(lang, "admin_unban"):
        context.user_data["admin_wait_unban"] = True
        await update.message.reply_text(t(lang, "ask_user_id_unban"), reply_markup=admin_keyboard(lang))
        return True

    if text == t(lang, "admin_stop"):
        set_setting("bot_status", "off")
        await update.message.reply_text(t(lang, "stopped"), reply_markup=admin_keyboard(lang))
        return True

    if text == t(lang, "admin_start"):
        set_setting("bot_status", "on")
        await update.message.reply_text(t(lang, "started"), reply_markup=admin_keyboard(lang))
        return True

    if text == t(lang, "admin_edit_welcome"):
        context.user_data["admin_wait_welcome"] = True
        await update.message.reply_text(t(lang, "ask_new_welcome"), reply_markup=admin_keyboard(lang))
        return True

    if text == t(lang, "admin_edit_help"):
        context.user_data["admin_wait_help"] = True
        await update.message.reply_text(t(lang, "ask_new_help"), reply_markup=admin_keyboard(lang))
        return True

    if text == t(lang, "admin_broadcast"):
        context.user_data["admin_wait_broadcast"] = True
        await update.message.reply_text(t(lang, "ask_broadcast"), reply_markup=admin_keyboard(lang))
        return True

    if text == t(lang, "admin_channels"):
        context.user_data["admin_channels_menu"] = True
        await update.message.reply_text(t(lang, "channels_menu"), reply_markup=forced_channels_admin_keyboard(lang))
        return True

    return False


async def handle_admin_waiting_inputs(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, lang: str) -> bool:
    uid = update.effective_user.id
    if not is_admin(uid):
        return False

    # forced channels menu
    if context.user_data.get("admin_channels_menu"):
        if text == t(lang, "ch_back"):
            context.user_data["admin_channels_menu"] = False
            await update.message.reply_text(t(lang, "admin_title"), reply_markup=admin_keyboard(lang))
            return True

        if text == t(lang, "ch_show"):
            channels = get_force_channels()
            listing = "\n".join([f"• {c}" for c in channels]) if channels else "-"
            await update.message.reply_text(t(lang, "ch_list").format(list=listing),
                                           reply_markup=forced_channels_admin_keyboard(lang))
            return True

        if text == t(lang, "ch_add"):
            context.user_data["admin_wait_add_channel"] = True
            await update.message.reply_text(t(lang, "ask_ch_add"), reply_markup=forced_channels_admin_keyboard(lang))
            return True

        if text == t(lang, "ch_del"):
            context.user_data["admin_wait_del_channel"] = True
            await update.message.reply_text(t(lang, "ask_ch_del"), reply_markup=forced_channels_admin_keyboard(lang))
            return True

    if context.user_data.get("admin_wait_add_channel"):
        context.user_data["admin_wait_add_channel"] = False
        ch = text.strip()
        if not ch:
            await update.message.reply_text("❌", reply_markup=forced_channels_admin_keyboard(lang))
            return True
        if not ch.startswith("@"):
            ch = "@" + ch
        channels = get_force_channels()
        if ch not in channels:
            channels.append(ch)
            set_setting("force_channels", json.dumps(channels))
        await update.message.reply_text(t(lang, "ch_added"), reply_markup=forced_channels_admin_keyboard(lang))
        return True

    if context.user_data.get("admin_wait_del_channel"):
        context.user_data["admin_wait_del_channel"] = False
        ch = text.strip()
        if not ch:
            await update.message.reply_text("❌", reply_markup=forced_channels_admin_keyboard(lang))
            return True
        if not ch.startswith("@"):
            ch = "@" + ch
        channels = [c for c in get_force_channels() if c != ch]
        set_setting("force_channels", json.dumps(channels))
        await update.message.reply_text(t(lang, "ch_deleted"), reply_markup=forced_channels_admin_keyboard(lang))
        return True

    if context.user_data.get("admin_wait_ban"):
        context.user_data["admin_wait_ban"] = False
        try:
            target = int(text.strip())
        except:
            await update.message.reply_text("❌", reply_markup=admin_keyboard(lang))
            return True
        cur.execute("UPDATE users SET banned=1 WHERE user_id=?", (target,))
        conn.commit()
        await update.message.reply_text(t(lang, "ban_done").format(id=target), reply_markup=admin_keyboard(lang))
        return True

    if context.user_data.get("admin_wait_unban"):
        context.user_data["admin_wait_unban"] = False
        try:
            target = int(text.strip())
        except:
            await update.message.reply_text("❌", reply_markup=admin_keyboard(lang))
            return True
        cur.execute("UPDATE users SET banned=0 WHERE user_id=?", (target,))
        conn.commit()
        await update.message.reply_text(t(lang, "unban_done").format(id=target), reply_markup=admin_keyboard(lang))
        return True

    if context.user_data.get("admin_wait_welcome"):
        context.user_data["admin_wait_welcome"] = False
        if lang == "ar":
            set_setting("welcome_message_ar", text)
        else:
            set_setting("welcome_message_en", text)
        await update.message.reply_text(t(lang, "welcome_updated"), reply_markup=admin_keyboard(lang))
        return True

    if context.user_data.get("admin_wait_help"):
        context.user_data["admin_wait_help"] = False
        if lang == "ar":
            set_setting("help_message_ar", text)
        else:
            set_setting("help_message_en", text)
        await update.message.reply_text(t(lang, "help_updated"), reply_markup=admin_keyboard(lang))
        return True

    if context.user_data.get("admin_wait_broadcast"):
        context.user_data["admin_wait_broadcast"] = False
        msg = text
        cur.execute("SELECT user_id FROM users WHERE banned=0")
        users = [r[0] for r in cur.fetchall()]

        ok = 0
        fail = 0
        for u in users:
            try:
                await context.bot.send_message(u, msg)
                ok += 1
            except:
                fail += 1

        await update.message.reply_text(
            t(lang, "broadcast_done").format(ok=ok, fail=fail, total=len(users)),
            reply_markup=admin_keyboard(lang)
        )
        return True

    return False


# ================== Guard ==================
async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE, lang: str) -> bool:
    uid = update.effective_user.id

    if not bot_is_on() and not is_admin(uid):
        await update.message.reply_text(t(lang, "bot_off"))
        return False

    if user_is_banned(uid) and not is_admin(uid):
        await update.message.reply_text(t(lang, "banned"))
        return False

    ok, info = await is_user_subscribed(context.bot, uid)
    if not ok:
        channels = get_force_channels()
        if is_admin(uid) and info:
            await update.message.reply_text(f"⚠️ {info}")
        await update.message.reply_text(t(lang, "must_sub"), reply_markup=force_join_keyboard(lang, channels))
        return False

    return True


# ================== User handler ==================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    register_user(update)
    lang = get_user_lang(uid)

    if text == "🌐 اللغة / Language":
        await update.message.reply_text(t(lang, "lang_choose"), reply_markup=language_keyboard())
        return

    if await handle_admin_waiting_inputs(update, context, text, lang):
        return
    if await handle_admin_text(update, context, text, lang):
        return

    if not await guard(update, context, lang):
        return

    if text == t(lang, "btn_link_ip"):
        context.user_data["await_ip"] = True
        await update.message.reply_text(t(lang, "ask_ip"))
        return

    if text == t(lang, "btn_quota"):
        used, bonus, total = get_today_stats(uid)
        if is_admin(uid):
            await update.message.reply_text(t(lang, "quota_admin"), reply_markup=main_keyboard(lang, uid))
        else:
            msg = f"📊 {used}/{total}  (+{bonus})"
            await update.message.reply_text(msg, reply_markup=main_keyboard(lang, uid))
        return

    if text == t(lang, "btn_help"):
        help_msg = get_setting("help_message_ar" if lang == "ar" else "help_message_en", "")
        if not help_msg:
            help_msg = TXT[lang]["help_message_ar"] if lang == "ar" else TXT[lang]["help_message_en"]
        await update.message.reply_text(help_msg, reply_markup=main_keyboard(lang, uid))
        return

    if text == t(lang, "btn_invite"):
        me = await context.bot.get_me()
        link = get_invite_link(me.username, uid)
        await update.message.reply_text(t(lang, "invite_text").format(link=link), reply_markup=main_keyboard(lang, uid))
        return

    if text == t(lang, "btn_my_domains"):
        cur.execute("SELECT subdomain, ip, created_at FROM domains WHERE user_id=? ORDER BY id DESC LIMIT 30", (uid,))
        rows = cur.fetchall()
        if not rows:
            await update.message.reply_text(t(lang, "no_domains"), reply_markup=main_keyboard(lang, uid))
            return

        for sub, ip, created_at in rows:
            label = sub.split(".", 1)[0]
            ns_name = f"ns.{label}.{CF_BASE_DOMAIN}"
            await update.message.reply_text(
                f"🌐 {sub}\n➡️ {ip}\n⚙️ NS: {ns_name} → {sub}\n⏰ {created_at[:19]}",
                reply_markup=domains_inline_keyboard(lang, sub)
            )
        return

    # create domain
    if context.user_data.get("await_ip"):
        context.user_data["await_ip"] = False
        ip = text

        allowed, remaining = consume_attempt(uid)
        if not allowed:
            await update.message.reply_text(t(lang, "not_allowed_daily"), reply_markup=main_keyboard(lang, uid))
            return

        label = random_label(6)
        fqdn = f"{label}.{CF_BASE_DOMAIN}"
        ns_name = f"ns.{label}.{CF_BASE_DOMAIN}"
        ns_value = fqdn

        try:
            cf_upsert_record("A", fqdn, ip, proxied=False, ttl=1)
            cf_upsert_record("NS", ns_name, ns_value, ttl=1)
        except Exception as e:
            await update.message.reply_text(f"⚠️ Cloudflare Error: {e}", reply_markup=main_keyboard(lang, uid))
            return

        cur.execute(
            "INSERT INTO domains (user_id, subdomain, ip, created_at) VALUES (?,?,?,?)",
            (uid, fqdn, ip, now_iso())
        )
        conn.commit()

        me = await context.bot.get_me()
        report = connection_report(ip=ip, fqdn=fqdn, ns_name=ns_name, bot_username=me.username)
        await update.message.reply_text(report, reply_markup=main_keyboard(lang, uid))
        return

    # rebind flow
    if context.user_data.get("rebind_domain"):
        sub = context.user_data.pop("rebind_domain")
        ip = text.strip()

        label = sub.split(".", 1)[0]
        ns_name = f"ns.{label}.{CF_BASE_DOMAIN}"
        ns_value = sub

        try:
            cf_upsert_record("A", sub, ip, proxied=False, ttl=1)
            cf_upsert_record("NS", ns_name, ns_value, ttl=1)
            cur.execute("UPDATE domains SET ip=? WHERE user_id=? AND subdomain=?", (ip, uid, sub))
            conn.commit()
        except Exception as e:
            await update.message.reply_text(f"⚠️ Error: {e}", reply_markup=main_keyboard(lang, uid))
            return

        me = await context.bot.get_me()
        report = connection_report(ip=ip, fqdn=sub, ns_name=ns_name, bot_username=me.username)
        await update.message.reply_text(report, reply_markup=main_keyboard(lang, uid))
        return


# ================== Callbacks ==================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    register_user(update)

    lang = get_user_lang(uid)
    data = q.data

    if data == "lang":
        await q.message.reply_text(t(lang, "lang_choose"), reply_markup=language_keyboard())
        return

    if data.startswith("setlang|"):
        new_lang = data.split("|", 1)[1].strip()
        if new_lang not in ("ar", "en"):
            new_lang = "ar"
        set_user_lang(uid, new_lang)
        lang = new_lang
        await q.message.reply_text(t(lang, "lang_saved"), reply_markup=main_keyboard(lang, uid))
        return

    if data == "checksub":
        ok, info = await is_user_subscribed(context.bot, uid)
        if ok:
            await q.message.reply_text(t(lang, "sub_ok"), reply_markup=main_keyboard(lang, uid))
        else:
            channels = get_force_channels()
            if is_admin(uid) and info:
                await q.message.reply_text(f"⚠️ {info}")
            await q.message.reply_text(t(lang, "sub_bad"), reply_markup=force_join_keyboard(lang, channels))
        return

    # other callbacks guarded
    if not bot_is_on() and not is_admin(uid):
        await q.message.reply_text(t(lang, "bot_off"))
        return
    if user_is_banned(uid) and not is_admin(uid):
        await q.message.reply_text(t(lang, "banned"))
        return
    ok, info = await is_user_subscribed(context.bot, uid)
    if not ok:
        channels = get_force_channels()
        if is_admin(uid) and info:
            await q.message.reply_text(f"⚠️ {info}")
        await q.message.reply_text(t(lang, "must_sub"), reply_markup=force_join_keyboard(lang, channels))
        return

    if data.startswith("copy|"):
        sub = data.split("|", 1)[1]
        await q.answer(sub, show_alert=True)
        return

    if data.startswith("askdel|"):
        sub = data.split("|", 1)[1]
        await q.edit_message_text(t(lang, "del_ask").format(sub=sub), reply_markup=confirm_delete_keyboard(lang, sub))
        return

    if data == "cancel":
        await q.edit_message_text(t(lang, "cancelled"))
        return

    if data.startswith("confirm|"):
        sub = data.split("|", 1)[1]
        label = sub.split(".", 1)[0]
        ns_name = f"ns.{label}.{CF_BASE_DOMAIN}"

        try:
            cf_delete_records(sub, "A")
            cf_delete_records(ns_name, "NS")
        except Exception as e:
            await q.edit_message_text(f"⚠️ {e}")
            return

        cur.execute("DELETE FROM domains WHERE user_id=? AND subdomain=?", (uid, sub))
        conn.commit()
        await q.edit_message_text(t(lang, "deleted").format(sub=sub))
        return

    if data.startswith("rebind|"):
        sub = data.split("|", 1)[1]
        context.user_data["rebind_domain"] = sub
        await q.message.reply_text(t(lang, "rebind_ask").format(sub=sub))
        return


# ================== Main ==================
def main():
    app = Application.builder().token(TG_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    if WEBHOOK_BASE_URL:
        webhook_path = f"/{TG_BOT_TOKEN}"
        webhook_url = f"{WEBHOOK_BASE_URL}{webhook_path}"
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=webhook_path.lstrip("/"),
            webhook_url=webhook_url,
            allowed_updates=Update.ALL_TYPES,
        )
    else:
        app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
