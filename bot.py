import os
import sqlite3
import random
import string
from datetime import datetime, timezone
from typing import Optional, Tuple

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

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))  # 6964811817
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "5"))

# Nameservers (ثابتة/تضعها بالـ .env)
NS1 = os.getenv("NS1", "ns1.yourdns.com")
NS2 = os.getenv("NS2", "ns2.yourdns.com")

# SQLite path (على Railway لازم تضيف Volume حتى يبقى ثابت)
DB_PATH = os.getenv("DB_PATH", "database/bot.db")

CF_API = "https://api.cloudflare.com/client/v4"

if not all([TG_BOT_TOKEN, CF_API_TOKEN, CF_ZONE_ID, CF_BASE_DOMAIN]):
    raise RuntimeError("❌ أكمل متغيرات .env: TG_BOT_TOKEN / CF_API_TOKEN / CF_ZONE_ID / CF_BASE_DOMAIN")

os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)

# ================== DB ==================
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS quota (
    user_id INTEGER PRIMARY KEY,
    used INTEGER DEFAULT 0,
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
    banned INTEGER DEFAULT 0
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
)
""")

cur.execute("""
INSERT OR IGNORE INTO settings (key, value)
VALUES ('welcome_message', '👋 مرحبًا بك في البوت\\n\\nاضغط زر 🔗 ربط IP ثم أرسل IP فقط.')
""")
cur.execute("""
INSERT OR IGNORE INTO settings (key, value)
VALUES ('bot_status', 'on')
""")
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

def cf_find_record(name: str, rtype: str, content: Optional[str] = None) -> Optional[dict]:
    params = {"type": rtype, "name": name}
    r = requests.get(f"{CF_API}/zones/{CF_ZONE_ID}/dns_records", headers=cf_headers(), params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(str(data))
    results = data.get("result", [])
    if content is None:
        return results[0] if results else None
    for rec in results:
        if rec.get("content") == content:
            return rec
    return None

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

def cf_delete_records(name: str, rtype: str, content: Optional[str] = None) -> int:
    """
    Delete all matching records by name+type, optionally filter by content.
    Returns number deleted.
    """
    params = {"type": rtype, "name": name}
    r = requests.get(f"{CF_API}/zones/{CF_ZONE_ID}/dns_records", headers=cf_headers(), params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        raise RuntimeError(str(data))
    results = data.get("result", [])
    deleted = 0
    for rec in results:
        if content is not None and rec.get("content") != content:
            continue
        rid = rec["id"]
        rr = requests.delete(f"{CF_API}/zones/{CF_ZONE_ID}/dns_records/{rid}", headers=cf_headers(), timeout=20)
        rr.raise_for_status()
        d2 = rr.json()
        if d2.get("success"):
            deleted += 1
    return deleted

def check_quota(uid: int) -> Tuple[bool, int]:
    today = today_iso()
    cur.execute("SELECT used,last_date FROM quota WHERE user_id=?", (uid,))
    row = cur.fetchone()

    if not row:
        cur.execute("INSERT INTO quota VALUES (?,?,?)", (uid, 0, today))
        conn.commit()
        used, last = 0, today
    else:
        used, last = row[0], row[1]

    if last != today:
        used = 0
        cur.execute("UPDATE quota SET used=0,last_date=? WHERE user_id=?", (today, uid))
        conn.commit()

    if used >= DAILY_LIMIT:
        return False, 0

    cur.execute("UPDATE quota SET used=used+1 WHERE user_id=?", (uid,))
    conn.commit()

    return True, DAILY_LIMIT - (used + 1)

def get_used_today(uid: int) -> int:
    today = today_iso()
    cur.execute("SELECT used,last_date FROM quota WHERE user_id=?", (uid,))
    row = cur.fetchone()
    if not row:
        return 0
    used, last = row
    return used if last == today else 0

def user_is_banned(uid: int) -> bool:
    cur.execute("SELECT banned FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    return bool(row and row[0] == 1)

def register_user(update: Update) -> bool:
    """
    Returns True if user inserted first time (new user).
    """
    u = update.effective_user
    uid = u.id
    first_name = u.first_name or ""
    username = u.username or ""

    cur.execute("SELECT user_id FROM users WHERE user_id=?", (uid,))
    exists = cur.fetchone() is not None
    if not exists:
        cur.execute(
            "INSERT INTO users (user_id, first_name, username, joined_at, banned) VALUES (?,?,?,?,0)",
            (uid, first_name, username, now_iso())
        )
        conn.commit()
        return True
    else:
        # تحديث الاسم/اليوزر إذا تغير
        cur.execute(
            "UPDATE users SET first_name=?, username=? WHERE user_id=?",
            (first_name, username, uid)
        )
        conn.commit()
        return False

# ================== Keyboards ==================
def main_keyboard(uid: int) -> ReplyKeyboardMarkup:
    kb = [
        ["🔗 ربط IP"],
        ["📂 دوميناتي"],
        ["📊 المتبقي اليومي", "❓ مساعدة"]
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
            InlineKeyboardButton("📋 نسخ", callback_data=f"copy|{subdomain}"),
        ],
        [
            InlineKeyboardButton("🔁 إعادة ربط", callback_data=f"rebind|{subdomain}")
        ]
    ])

def confirm_delete_keyboard(subdomain: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ نعم احذف", callback_data=f"confirm|{subdomain}")],
        [InlineKeyboardButton("❌ إلغاء", callback_data="cancel")]
    ])

# ================== Start / Welcome ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    is_new = register_user(update)

    # اشعار للأدمن عند دخول مستخدم جديد
    if is_new and ADMIN_ID:
        cur.execute("SELECT COUNT(*) FROM users")
        total_users = cur.fetchone()[0]
        uname = update.effective_user.username
        uname = f"@{uname}" if uname else "-"
        await context.bot.send_message(
            ADMIN_ID,
            f"👤 مستخدم جديد دخل البوت\n\n"
            f"🆔 ID: {uid}\n"
            f"👤 الاسم: {update.effective_user.first_name or '-'}\n"
            f"📛 اليوزر: {uname}\n"
            f"📊 عدد المستخدمين: {total_users}"
        )

    # لو البوت مطفي: نسمح للأدمن فقط
    if not bot_is_on() and not is_admin(uid):
        await update.message.reply_text("⛔ البوت متوقف مؤقتًا.\nيرجى المحاولة لاحقًا.")
        return

    # لو المستخدم محظور
    if user_is_banned(uid) and not is_admin(uid):
        await update.message.reply_text("⛔ تم حظرك من استخدام البوت.")
        return

    welcome = get_setting("welcome_message", "👋 مرحبًا بك")
    await update.message.reply_text(welcome, reply_markup=main_keyboard(uid))

# ================== Admin Actions (text) ==================
async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    uid = update.effective_user.id
    if not is_admin(uid):
        return False  # not handled

    # رجوع
    if text == "🔙 رجوع":
        await update.message.reply_text("رجعناك للقائمة الرئيسية ✅", reply_markup=main_keyboard(uid))
        return True

    # لوحة الأدمن
    if text == "🛠 لوحة الأدمن":
        await update.message.reply_text("🛠 لوحة تحكم الأدمن", reply_markup=admin_keyboard())
        return True

    # إحصائيات
    if text == "📊 إحصائيات":
        cur.execute("SELECT COUNT(*) FROM users")
        users = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM domains")
        domains = cur.fetchone()[0]
        bot_status = "✅ شغال" if bot_is_on() else "⛔ متوقف"
        await update.message.reply_text(
            f"📊 إحصائيات البوت\n\n"
            f"👥 المستخدمين: {users}\n"
            f"🌐 الدومينات: {domains}\n"
            f"⚙️ الحالة: {bot_status}",
            reply_markup=admin_keyboard()
        )
        return True

    # إدارة المستخدمين (ملخص + آخر 15)
    if text == "👥 إدارة المستخدمين":
        cur.execute("SELECT COUNT(*) FROM users")
        total = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM users WHERE banned=1")
        banned = cur.fetchone()[0]
        cur.execute("SELECT user_id, first_name, username, joined_at FROM users ORDER BY joined_at DESC LIMIT 15")
        rows = cur.fetchall()

        msg = f"👥 إدارة المستخدمين\n\n📊 الكل: {total}\n🚫 المحظورين: {banned}\n\nآخر 15 مستخدم:\n"
        for r in rows:
            u_id, fn, un, j = r
            un = f"@{un}" if un else "-"
            msg += f"• {u_id} | {fn or '-'} | {un} | {j[:19]}\n"
        await update.message.reply_text(msg, reply_markup=admin_keyboard())
        return True

    # حظر مستخدم
    if text == "🚫 حظر مستخدم":
        context.user_data["admin_wait_ban"] = True
        await update.message.reply_text("🆔 أرسل ID المستخدم للحظر:", reply_markup=admin_keyboard())
        return True

    # رفع حظر
    if text == "✅ رفع حظر":
        context.user_data["admin_wait_unban"] = True
        await update.message.reply_text("🆔 أرسل ID المستخدم لرفع الحظر:", reply_markup=admin_keyboard())
        return True

    # إيقاف / تشغيل
    if text == "⏸️ إيقاف البوت":
        set_setting("bot_status", "off")
        await update.message.reply_text("⛔ تم إيقاف البوت (المستخدمين لن يستطيعوا الاستخدام).", reply_markup=admin_keyboard())
        return True

    if text == "▶️ تشغيل البوت":
        set_setting("bot_status", "on")
        await update.message.reply_text("✅ تم تشغيل البوت.", reply_markup=admin_keyboard())
        return True

    # تعديل رسالة الترحيب
    if text == "✏️ تعديل رسالة الترحيب":
        context.user_data["admin_wait_welcome"] = True
        await update.message.reply_text("✏️ أرسل رسالة الترحيب الجديدة الآن:", reply_markup=admin_keyboard())
        return True

    # إذاعة
    if text == "📢 إذاعة":
        context.user_data["admin_wait_broadcast"] = True
        await update.message.reply_text("📢 أرسل رسالة الإذاعة الآن (نص/ملصق/صورة تُرسل كنص فقط هنا):", reply_markup=admin_keyboard())
        return True

    return False  # not handled

async def handle_admin_waiting_inputs(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    uid = update.effective_user.id
    if not is_admin(uid):
        return False

    # Ban
    if context.user_data.get("admin_wait_ban"):
        context.user_data["admin_wait_ban"] = False
        try:
            target = int(text.strip())
        except:
            await update.message.reply_text("❌ ID غير صحيح. أرسل رقم فقط.", reply_markup=admin_keyboard())
            return True

        cur.execute("UPDATE users SET banned=1 WHERE user_id=?", (target,))
        conn.commit()
        await update.message.reply_text(f"🚫 تم حظر المستخدم: {target}", reply_markup=admin_keyboard())
        return True

    # Unban
    if context.user_data.get("admin_wait_unban"):
        context.user_data["admin_wait_unban"] = False
        try:
            target = int(text.strip())
        except:
            await update.message.reply_text("❌ ID غير صحيح. أرسل رقم فقط.", reply_markup=admin_keyboard())
            return True

        cur.execute("UPDATE users SET banned=0 WHERE user_id=?", (target,))
        conn.commit()
        await update.message.reply_text(f"✅ تم رفع الحظر عن: {target}", reply_markup=admin_keyboard())
        return True

    # Welcome edit
    if context.user_data.get("admin_wait_welcome"):
        context.user_data["admin_wait_welcome"] = False
        set_setting("welcome_message", text)
        await update.message.reply_text("✅ تم تحديث رسالة الترحيب.", reply_markup=admin_keyboard())
        return True

    # Broadcast
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

# ================== User flow (buttons) ==================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid = update.effective_user.id

    # تسجيل/تحديث المستخدم
    register_user(update)

    # لو البوت مطفي: نسمح للأدمن فقط
    if not bot_is_on() and not is_admin(uid):
        await update.message.reply_text("⛔ البوت متوقف مؤقتًا.\nيرجى المحاولة لاحقًا.")
        return

    # لو محظور
    if user_is_banned(uid) and not is_admin(uid):
        await update.message.reply_text("⛔ تم حظرك من استخدام البوت.")
        return

    # مدخلات الأدمن (المرحلة الثانية: انتظار ID/رسالة..)
    if await handle_admin_waiting_inputs(update, context, text):
        return

    # أزرار الأدمن
    if await handle_admin_text(update, context, text):
        return

    # ===== أزرار المستخدمين =====
    if text == "🔗 ربط IP":
        context.user_data["await_ip"] = True
        await update.message.reply_text("📥 أرسل IP الآن:")
        return

    if text == "📊 المتبقي اليومي":
        used = get_used_today(uid)
        await update.message.reply_text(
            f"📊 استخدمت اليوم: {used}/{DAILY_LIMIT}",
            reply_markup=main_keyboard(uid)
        )
        return

    if text == "❓ مساعدة":
        await update.message.reply_text(
            "طريقة الاستخدام:\n"
            "1) اضغط زر 🔗 ربط IP\n"
            "2) أرسل IP فقط\n"
            "3) يعطيك اسم عشوائي ويضيف A + NS\n\n"
            f"⏱️ الحد: {DAILY_LIMIT} مرات يوميًا",
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
            await update.message.reply_text(
                f"🌐 {sub}\n➡️ {ip}\n⏰ {created_at[:19]}",
                reply_markup=domains_inline_keyboard(sub)
            )
        return

    # استقبال IP لإنشاء جديد
    if context.user_data.get("await_ip"):
        context.user_data["await_ip"] = False
        ip = text

        allowed, remaining = check_quota(uid)
        if not allowed:
            await update.message.reply_text("❌ وصلت الحد اليومي. جرّب باچر.", reply_markup=main_keyboard(uid))
            return

        label = random_label(6)
        fqdn = f"{label}.{CF_BASE_DOMAIN}"

        try:
            # A
            cf_upsert_record("A", fqdn, ip, proxied=False, ttl=1)
            # NS (سجلين)
            cf_upsert_record("NS", fqdn, NS1, ttl=1)
            # قد يكون NS1 موجود بالفعل بنفس المحتوى، نضيف NS2 أيضاً
            # Upsert سيعدل نفس سجل NS إذا أول سجل موجود؛ لذلك نضمن سجلين:
            # نتحقق إن كان NS2 موجود، إذا لا، ننشئه كـ POST مباشرة
            # (Cloudflare يسمح بأكثر من NS لنفس الاسم)
            existing_ns2 = cf_find_record(fqdn, "NS", content=NS2)
            if not existing_ns2:
                # Create NS2 explicitly (حتى ما يستبدل NS1)
                r = requests.post(
                    f"{CF_API}/zones/{CF_ZONE_ID}/dns_records",
                    headers=cf_headers(),
                    json={"type": "NS", "name": fqdn, "content": NS2, "ttl": 1},
                    timeout=20
                )
                r.raise_for_status()
                if not r.json().get("success"):
                    raise RuntimeError(str(r.json()))
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
            f"NS → {NS1}\n"
            f"NS → {NS2}\n\n"
            f"⏳ المتبقي اليوم: {remaining}",
            reply_markup=main_keyboard(uid)
        )
        return

    # إعادة ربط IP لنفس الدومين (بعد زر إعادة ربط)
    if context.user_data.get("rebind_domain"):
        sub = context.user_data.pop("rebind_domain")
        ip = text.strip()

        try:
            cf_upsert_record("A", sub, ip, proxied=False, ttl=1)
            cur.execute("UPDATE domains SET ip=? WHERE user_id=? AND subdomain=?", (ip, uid, sub))
            conn.commit()
        except Exception as e:
            await update.message.reply_text(f"⚠️ خطأ: {e}", reply_markup=main_keyboard(uid))
            return

        await update.message.reply_text(
            f"✅ تم إعادة الربط:\n{sub}\n➡️ {ip}",
            reply_markup=main_keyboard(uid)
        )
        return

# ================== Inline Callbacks ==================
async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    # لو البوت مطفي: نسمح للأدمن فقط
    if not bot_is_on() and not is_admin(uid):
        await q.message.reply_text("⛔ البوت متوقف مؤقتًا.")
        return

    # Copy
    if data.startswith("copy|"):
        sub = data.split("|", 1)[1]
        await q.answer(sub, show_alert=True)
        return

    # Ask delete (confirm)
    if data.startswith("askdel|"):
        sub = data.split("|", 1)[1]
        await q.edit_message_text(
            f"⚠️ هل أنت متأكد من حذف هذا الدومين؟\n\n🌐 {sub}",
            reply_markup=confirm_delete_keyboard(sub)
        )
        return

    # Cancel delete
    if data == "cancel":
        await q.edit_message_text("❌ تم إلغاء عملية الحذف.")
        return

    # Confirm delete
    if data.startswith("confirm|"):
        sub = data.split("|", 1)[1]

        # احذف من Cloudflare (A + NS) ثم من DB
        try:
            # A
            cf_delete_records(sub, "A")
            # NS (كلها)
            cf_delete_records(sub, "NS")
        except Exception as e:
            await q.edit_message_text(f"⚠️ تعذر الحذف من Cloudflare:\n{e}")
            return

        cur.execute("DELETE FROM domains WHERE user_id=? AND subdomain=?", (uid, sub))
        conn.commit()
        await q.edit_message_text(f"🗑️ تم حذف:\n{sub}")
        return

    # Rebind (ask for new IP)
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
