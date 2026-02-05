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
    ref_rewarded INTEGER DEFAULT 0
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")

# إعدادات افتراضية
cur.execute("""
INSERT OR IGNORE INTO settings (key, value)
VALUES ('welcome_message', '👋 مرحبًا بك\\n\\n✅ اضغط زر 🔗 ربط IP ثم أرسل IP فقط.')
""")
cur.execute("""
INSERT OR IGNORE INTO settings (key, value)
VALUES ('bot_status', 'on')
""")

# قنوات الاشتراك الإجباري (افتراضي: @eshop_2)
cur.execute("""
INSERT OR IGNORE INTO settings (key, value)
VALUES ('force_channels', ?)
""", (json.dumps(["@eshop_2"]),))

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
            "INSERT INTO users (user_id, first_name, username, joined_at, banned, referred_by, ref_rewarded) "
            "VALUES (?,?,?,?,0,NULL,0)",
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

# ✅✅✅ إصلاح الاشتراك الإجباري (نسخة نهائية)
async def is_user_subscribed(bot, uid: int) -> Tuple[bool, str]:
    """
    Return (ok, info)
    info may contain status/error for admin debugging.
    """
    if is_admin(uid):
        return True, ""

    channels = get_force_channels()
    if not channels:
        return True, ""

    for ch in channels:
        try:
            member = await bot.get_chat_member(chat_id=ch, user_id=uid)
            status = str(member.status).lower()  # safest across versions

            # statuses accepted
            if status in ("member", "administrator", "creator"):
                continue

            # left/kicked/restricted
            return False, f"{ch} | status={status}"

        except Exception as e:
            reason = str(e)
            print(f"[SUB_CHECK_ERROR] channel={ch} user={uid} error={reason}")

            # أرسل السبب للأدمن تلقائياً (حتى نعرف ليش يفشل)
            if ADMIN_ID:
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        f"⚠️ خطأ فحص الاشتراك\n"
                        f"القناة: {ch}\n"
                        f"المستخدم: {uid}\n"
                        f"السبب: {reason}\n\n"
                        f"✅ تأكد: القناة عامة + اسمها صحيح + البوت مشرف"
                    )
                except:
                    pass

            return False, f"{ch} | error={reason}"

    return True, ""

def force_join_keyboard(channels: List[str]) -> InlineKeyboardMarkup:
    btns = []
    for ch in channels[:3]:
        username = ch.lstrip("@")
        btns.append([InlineKeyboardButton(f"🔗 الدخول إلى {ch}", url=f"https://t.me/{username}")])
    btns.append([InlineKeyboardButton("✅ تحقق من الاشتراك", callback_data="checksub")])
    return InlineKeyboardMarkup(btns)

# ================== Keyboards ==================
def main_keyboard(uid: int) -> ReplyKeyboardMarkup:
    kb = [
        ["🔗 ربط IP"],
        ["📂 دوميناتي"],
        ["🎁 رابط دعوتي", "📊 المتبقي اليومي"],
        ["❓ مساعدة"]
    ]
    if is_admin(uid):
        kb.append(["🛠 لوحة الأدمن"])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["👥 إدارة المستخدمين", "📊 إحصائيات"],
            ["🚫 حظر مستخدم", "✅ رفع حظر"],
            ["📢 إذاعة"],
            ["📣 قنوات الاشتراك الإجباري"],
            ["⏸️ إيقاف البوت", "▶️ تشغيل البوت"],
            ["✏️ تعديل رسالة الترحيب"],
            ["🔙 رجوع"]
        ],
        resize_keyboard=True
    )

def domains_inline_keyboard(subdomain: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑️ حذف", callback_data=f"askdel|{subdomain}"),
            InlineKeyboardButton("📋 نسخ الاسم", callback_data=f"copy|{subdomain}"),
        ],
        [
            InlineKeyboardButton("🔁 إعادة ربط", callback_data=f"rebind|{subdomain}")
        ]
    ])

def confirm_delete_keyboard(subdomain: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔒 تأكيد الحذف", callback_data=f"confirm|{subdomain}")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
    ])

def forced_channels_admin_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["📋 عرض القنوات"],
            ["➕ إضافة قناة", "🗑️ حذف قناة"],
            ["🔙 رجوع"]
        ],
        resize_keyboard=True
    )

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

# ================== Admin utilities ==================
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
        f"👤 مستخدم جديد دخل البوت\n\n"
        f"🆔 ID: {uid}\n"
        f"👤 الاسم: {update.effective_user.first_name or '-'}\n"
        f"📛 اليوزر: {uname}\n"
        f"📊 عدد المستخدمين: {total_users}"
    )

# ================== Start ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    is_new = register_user(update)
    if is_new:
        await notify_admin_new_user(context, update)

    ref_uid = None
    if context.args:
        ref_uid = parse_ref_from_start(context.args[0])

    if ref_uid and is_new:
        rewarded = reward_referral_if_needed(uid, ref_uid)
        if rewarded:
            try:
                await context.bot.send_message(ref_uid, f"🎉 تم قبول دعوة جديدة!\n✅ تم إضافة محاولة إضافية لك (+1).")
            except:
                pass

    if not bot_is_on() and not is_admin(uid):
        await update.message.reply_text("⛔ البوت متوقف مؤقتًا.\nيرجى المحاولة لاحقًا.")
        return
    if user_is_banned(uid) and not is_admin(uid):
        await update.message.reply_text("⛔ تم حظرك من استخدام البوت.")
        return

    ok, info = await is_user_subscribed(context.bot, uid)
    if not ok:
        channels = get_force_channels()
        # للأدمن فقط: اعرض السبب بالمحادثة (يساعدك تعرف المشكلة)
        if is_admin(uid) and info:
            await update.message.reply_text(f"⚠️ سبب فشل التحقق:\n{info}")

        await update.message.reply_text(
            "🔒 يجب الاشتراك في القناة/القنوات أولاً لاستخدام البوت.\n\n"
            "بعد الاشتراك اضغط: ✅ تحقق من الاشتراك",
            reply_markup=force_join_keyboard(channels)
        )
        return

    welcome = get_setting("welcome_message", "👋 مرحبًا بك")
    await update.message.reply_text(welcome, reply_markup=main_keyboard(uid))

# ================== Admin handlers ==================
async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    uid = update.effective_user.id
    if not is_admin(uid):
        return False

    if text == "🔙 رجوع":
        await update.message.reply_text("✅ رجعناك للقائمة الرئيسية", reply_markup=main_keyboard(uid))
        return True

    if text == "🛠 لوحة الأدمن":
        await update.message.reply_text("🛠 لوحة تحكم الأدمن", reply_markup=admin_keyboard())
        return True

    if text == "📊 إحصائيات":
        cur.execute("SELECT COUNT(*) FROM users")
        users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM domains")
        domains = cur.fetchone()[0]
        bot_status = "✅ شغال" if bot_is_on() else "⛔ متوقف"
        channels = get_force_channels()
        await update.message.reply_text(
            f"📊 إحصائيات\n\n"
            f"👥 المستخدمين: {users}\n"
            f"🌐 الدومينات: {domains}\n"
            f"⚙️ حالة البوت: {bot_status}\n"
            f"📣 قنوات الاشتراك: {', '.join(channels) if channels else 'لا يوجد'}",
            reply_markup=admin_keyboard()
        )
        return True

    if text == "👥 إدارة المستخدمين":
        cur.execute("SELECT COUNT(*) FROM users")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users WHERE banned=1")
        banned = cur.fetchone()[0]
        cur.execute("SELECT user_id, first_name, username, joined_at FROM users ORDER BY joined_at DESC LIMIT 15")
        rows = cur.fetchall()

        msg = f"👥 إدارة المستخدمين\n\n📊 الكل: {total}\n🚫 المحظورين: {banned}\n\nآخر 15:\n"
        for r in rows:
            u_id, fn, un, j = r
            un = f"@{un}" if un else "-"
            msg += f"• {u_id} | {fn or '-'} | {un} | {j[:19]}\n"
        await update.message.reply_text(msg, reply_markup=admin_keyboard())
        return True

    if text == "🚫 حظر مستخدم":
        context.user_data["admin_wait_ban"] = True
        await update.message.reply_text("🆔 أرسل ID المستخدم للحظر:", reply_markup=admin_keyboard())
        return True

    if text == "✅ رفع حظر":
        context.user_data["admin_wait_unban"] = True
        await update.message.reply_text("🆔 أرسل ID المستخدم لرفع الحظر:", reply_markup=admin_keyboard())
        return True

    if text == "⏸️ إيقاف البوت":
        set_setting("bot_status", "off")
        await update.message.reply_text("⛔ تم إيقاف البوت.", reply_markup=admin_keyboard())
        return True

    if text == "▶️ تشغيل البوت":
        set_setting("bot_status", "on")
        await update.message.reply_text("✅ تم تشغيل البوت.", reply_markup=admin_keyboard())
        return True

    if text == "✏️ تعديل رسالة الترحيب":
        context.user_data["admin_wait_welcome"] = True
        await update.message.reply_text("✏️ أرسل رسالة الترحيب الجديدة الآن:", reply_markup=admin_keyboard())
        return True

    if text == "📢 إذاعة":
        context.user_data["admin_wait_broadcast"] = True
        await update.message.reply_text("📢 أرسل رسالة الإذاعة الآن:", reply_markup=admin_keyboard())
        return True

    if text == "📣 قنوات الاشتراك الإجباري":
        context.user_data["admin_channels_menu"] = True
        await update.message.reply_text("📣 إدارة قنوات الاشتراك الإجباري", reply_markup=forced_channels_admin_keyboard())
        return True

    return False

async def handle_admin_waiting_inputs(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    uid = update.effective_user.id
    if not is_admin(uid):
        return False

    if context.user_data.get("admin_channels_menu"):
        if text == "🔙 رجوع":
            context.user_data["admin_channels_menu"] = False
            await update.message.reply_text("🛠 لوحة تحكم الأدمن", reply_markup=admin_keyboard())
            return True

        if text == "📋 عرض القنوات":
            channels = get_force_channels()
            await update.message.reply_text(
                "📣 القنوات الحالية:\n" + "\n".join([f"• {c}" for c in channels]) if channels else "لا يوجد قنوات.",
                reply_markup=forced_channels_admin_keyboard()
            )
            return True

        if text == "➕ إضافة قناة":
            context.user_data["admin_wait_add_channel"] = True
            await update.message.reply_text("➕ أرسل معرف القناة مثل: @channel (أو بدون @)", reply_markup=forced_channels_admin_keyboard())
            return True

        if text == "🗑️ حذف قناة":
            context.user_data["admin_wait_del_channel"] = True
            await update.message.reply_text("🗑️ أرسل معرف القناة لحذفها مثل: @channel", reply_markup=forced_channels_admin_keyboard())
            return True

    if context.user_data.get("admin_wait_add_channel"):
        context.user_data["admin_wait_add_channel"] = False
        ch = text.strip()
        if not ch:
            await update.message.reply_text("❌ ارسل معرف صحيح.", reply_markup=forced_channels_admin_keyboard())
            return True
        if not ch.startswith("@"):
            ch = "@" + ch

        channels = get_force_channels()
        if ch not in channels:
            channels.append(ch)
            set_setting("force_channels", json.dumps(channels))
        await update.message.reply_text("✅ تم إضافة القناة.", reply_markup=forced_channels_admin_keyboard())
        return True

    if context.user_data.get("admin_wait_del_channel"):
        context.user_data["admin_wait_del_channel"] = False
        ch = text.strip()
        if not ch:
            await update.message.reply_text("❌ ارسل معرف صحيح.", reply_markup=forced_channels_admin_keyboard())
            return True
        if not ch.startswith("@"):
            ch = "@" + ch

        channels = get_force_channels()
        channels = [c for c in channels if c != ch]
        set_setting("force_channels", json.dumps(channels))
        await update.message.reply_text("✅ تم حذف القناة (إن كانت موجودة).", reply_markup=forced_channels_admin_keyboard())
        return True

    if context.user_data.get("admin_wait_ban"):
        context.user_data["admin_wait_ban"] = False
        try:
            target = int(text.strip())
        except:
            await update.message.reply_text("❌ ID غير صحيح.", reply_markup=admin_keyboard())
            return True
        cur.execute("UPDATE users SET banned=1 WHERE user_id=?", (target,))
        conn.commit()
        await update.message.reply_text(f"🚫 تم حظر المستخدم: {target}", reply_markup=admin_keyboard())
        return True

    if context.user_data.get("admin_wait_unban"):
        context.user_data["admin_wait_unban"] = False
        try:
            target = int(text.strip())
        except:
            await update.message.reply_text("❌ ID غير صحيح.", reply_markup=admin_keyboard())
            return True
        cur.execute("UPDATE users SET banned=0 WHERE user_id=?", (target,))
        conn.commit()
        await update.message.reply_text(f"✅ تم رفع الحظر عن: {target}", reply_markup=admin_keyboard())
        return True

    if context.user_data.get("admin_wait_welcome"):
        context.user_data["admin_wait_welcome"] = False
        set_setting("welcome_message", text)
        await update.message.reply_text("✅ تم تحديث رسالة الترحيب.", reply_markup=admin_keyboard())
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
            f"📢 تم إكمال الإذاعة\n\n✅ نجح: {ok}\n❌ فشل: {fail}\n👥 الإجمالي: {len(users)}",
            reply_markup=admin_keyboard()
        )
        return True

    return False

# ================== User handlers ==================
async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id

    if not bot_is_on() and not is_admin(uid):
        await update.message.reply_text("⛔ البوت متوقف مؤقتًا.")
        return False

    if user_is_banned(uid) and not is_admin(uid):
        await update.message.reply_text("⛔ تم حظرك من استخدام البوت.")
        return False

    ok, _ = await is_user_subscribed(context.bot, uid)
    if not ok:
        channels = get_force_channels()
        await update.message.reply_text(
            "🔒 يجب الاشتراك في القناة/القنوات أولاً.\n\nبعد الاشتراك اضغط ✅ تحقق من الاشتراك",
            reply_markup=force_join_keyboard(channels)
        )
        return False

    return True

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    register_user(update)

    if await handle_admin_waiting_inputs(update, context, text):
        return
    if await handle_admin_text(update, context, text):
        return

    if not await guard(update, context):
        return

    if text == "🔗 ربط IP":
        context.user_data["await_ip"] = True
        await update.message.reply_text("📥 أرسل IP الآن:")
        return

    if text == "📊 المتبقي اليومي":
        used, bonus, total = get_today_stats(uid)
        if is_admin(uid):
            await update.message.reply_text("👑 أنت أدمن — بدون حدود محاولات ✅", reply_markup=main_keyboard(uid))
        else:
            await update.message.reply_text(
                f"📊 اليوم\n"
                f"المستخدم: {used}\n"
                f"المكافأة: +{bonus}\n"
                f"الحد الكلي: {total}",
                reply_markup=main_keyboard(uid)
            )
        return

    if text == "🎁 رابط دعوتي":
        me = await context.bot.get_me()
        link = get_invite_link(me.username, uid)
        await update.message.reply_text(
            "🎁 رابط دعوتك:\n"
            f"{link}\n\n"
            "✅ إذا دخل شخص جديد عبر رابطك → تنضاف لك محاولة (+1).",
            reply_markup=main_keyboard(uid)
        )
        return

    if text == "❓ مساعدة":
        channels = get_force_channels()
        await update.message.reply_text(
            "طريقة الاستخدام:\n"
            "1) اضغط 🔗 ربط IP\n"
            "2) أرسل IP فقط\n"
            "3) ينشئ اسم عشوائي + A + NS مثل الصورة\n\n"
            f"📣 قنوات الاشتراك: {', '.join(channels) if channels else 'لا يوجد'}",
            reply_markup=main_keyboard(uid)
        )
        return

    if text == "📂 دوميناتي":
        cur.execute("SELECT subdomain, ip, created_at FROM domains WHERE user_id=? ORDER BY id DESC LIMIT 30", (uid,))
        rows = cur.fetchall()
        if not rows:
            await update.message.reply_text("📂 ما عندك دومينات مضافة لحد الآن.", reply_markup=main_keyboard(uid))
            return

        for sub, ip, created_at in rows:
            label = sub.split(".", 1)[0]
            ns_name = f"ns.{label}.{CF_BASE_DOMAIN}"
            await update.message.reply_text(
                f"🌐 {sub}\n➡️ {ip}\n🧷 NS: {ns_name} → {sub}\n⏰ {created_at[:19]}",
                reply_markup=domains_inline_keyboard(sub)
            )
        return

    if context.user_data.get("await_ip"):
        context.user_data["await_ip"] = False
        ip = text

        allowed, remaining = consume_attempt(uid)
        if not allowed:
            await update.message.reply_text("❌ وصلت الحد اليومي. جرّب باچر.", reply_markup=main_keyboard(uid))
            return

        label = random_label(6)
        fqdn = f"{label}.{CF_BASE_DOMAIN}"
        ns_name = f"ns.{label}.{CF_BASE_DOMAIN}"
        ns_value = fqdn

        try:
            cf_upsert_record("A", fqdn, ip, proxied=False, ttl=1)
            cf_upsert_record("NS", ns_name, ns_value, ttl=1)
        except Exception as e:
            await update.message.reply_text(f"⚠️ خطأ Cloudflare: {e}", reply_markup=main_keyboard(uid))
            return

        cur.execute(
            "INSERT INTO domains (user_id, subdomain, ip, created_at) VALUES (?,?,?,?)",
            (uid, fqdn, ip, now_iso())
        )
        conn.commit()

        await update.message.reply_text(
            "✅ تم الربط بنجاح 🎉\n\n"
            f"🌐 {fqdn}\n"
            f"A → {ip}\n"
            f"NS → {ns_name} managed by {ns_value}\n\n"
            f"⏳ المتبقي: {remaining if not is_admin(uid) else '∞'}",
            reply_markup=main_keyboard(uid)
        )
        return

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
            await update.message.reply_text(f"⚠️ خطأ: {e}", reply_markup=main_keyboard(uid))
            return

        await update.message.reply_text(
            f"✅ تم إعادة الربط:\n"
            f"🌐 {sub}\n"
            f"A → {ip}\n"
            f"NS → {ns_name} managed by {ns_value}",
            reply_markup=main_keyboard(uid)
        )
        return

# ================== Inline Callbacks ==================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    # ✅ تحقق من الاشتراك
    if data == "checksub":
        ok, info = await is_user_subscribed(context.bot, uid)
        if ok:
            await q.message.reply_text("✅ تم التحقق! تفضل استخدم البوت.", reply_markup=main_keyboard(uid))
        else:
            channels = get_force_channels()

            # للأدمن فقط: اعرض السبب
            if is_admin(uid) and info:
                await q.message.reply_text(f"⚠️ سبب فشل التحقق:\n{info}")

            await q.message.reply_text(
                "❌ لست مشتركًا بعد.\nاشترك ثم اضغط ✅ تحقق من الاشتراك",
                reply_markup=force_join_keyboard(channels)
            )
        return

    # Guard for other callbacks
    if not bot_is_on() and not is_admin(uid):
        await q.message.reply_text("⛔ البوت متوقف مؤقتًا.")
        return
    if user_is_banned(uid) and not is_admin(uid):
        await q.message.reply_text("⛔ تم حظرك من استخدام البوت.")
        return
    ok, _ = await is_user_subscribed(context.bot, uid)
    if not ok:
        channels = get_force_channels()
        await q.message.reply_text("🔒 يجب الاشتراك أولاً.", reply_markup=force_join_keyboard(channels))
        return

    if data.startswith("copy|"):
        sub = data.split("|", 1)[1]
        await q.answer(sub, show_alert=True)
        return

    if data.startswith("askdel|"):
        sub = data.split("|", 1)[1]
        await q.edit_message_text(
            f"⚠️ هل أنت متأكد؟\n\n🌐 {sub}",
            reply_markup=confirm_delete_keyboard(sub)
        )
        return

    if data == "cancel":
        await q.edit_message_text("❌ تم إلغاء العملية.")
        return

    if data.startswith("confirm|"):
        sub = data.split("|", 1)[1]
        label = sub.split(".", 1)[0]
        ns_name = f"ns.{label}.{CF_BASE_DOMAIN}"
        try:
            cf_delete_records(sub, "A")
            cf_delete_records(ns_name, "NS")
        except Exception as e:
            await q.edit_message_text(f"⚠️ تعذر الحذف:\n{e}")
            return

        cur.execute("DELETE FROM domains WHERE user_id=? AND subdomain=?", (uid, sub))
        conn.commit()
        await q.edit_message_text(f"🗑️ تم حذف:\n{sub}")
        return

    if data.startswith("rebind|"):
        sub = data.split("|", 1)[1]
        context.user_data["rebind_domain"] = sub
        await q.message.reply_text(f"🔁 أرسل IP الجديد لـ:\n{sub}")
        return

# ================== Main ==================
def main():
    app = Application.builder().token(TG_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
