#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════╗
║         Age-Gate Bot with Broadcast          ║
║  Features:                                   ║
║  • Age verification → channel link (30min)   ║
║  • Admin broadcast with custom delete timer  ║
║  • Admin can pin broadcast messages          ║
╚══════════════════════════════════════════════╝
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG  ← values are loaded from .env file
# ─────────────────────────────────────────────────────────────────────────────
load_dotenv()

def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Missing required env variable: {key}  (check your .env file)")
    return val

BOT_TOKEN    = _require("BOT_TOKEN")
CHANNEL_LINK = _require("CHANNEL_LINK")
ADMIN_IDS    = [int(x.strip()) for x in _require("ADMIN_IDS").split(",")]
USERS_FILE   = os.getenv("USERS_FILE", "users.json")

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CONVERSATION STATES
# ─────────────────────────────────────────────────────────────────────────────
ASK_MSG, ASK_TIMER, ASK_PIN = range(3)


# ─────────────────────────────────────────────────────────────────────────────
# USER STORAGE  (simple JSON file — survives restarts)
# ─────────────────────────────────────────────────────────────────────────────
def load_users() -> set:
    if Path(USERS_FILE).exists():
        with open(USERS_FILE) as f:
            return set(json.load(f))
    return set()


def save_users(users: set):
    with open(USERS_FILE, "w") as f:
        json.dump(list(users), f)


def add_user(user_id: int):
    users = load_users()
    if user_id not in users:
        users.add(user_id)
        save_users(users)


# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


async def _delete_job(context: ContextTypes.DEFAULT_TYPE):
    """Job callback — silently deletes a scheduled message."""
    d = context.job.data
    try:
        await context.bot.delete_message(
            chat_id=d["chat_id"], message_id=d["message_id"]
        )
        logger.info(f"Auto-deleted msg {d['message_id']} in chat {d['chat_id']}")
    except Exception as e:
        logger.warning(f"Could not delete message: {e}")


def schedule_delete(context: ContextTypes.DEFAULT_TYPE, chat_id, message_id, seconds: int):
    """Queue a message for deletion after `seconds`."""
    context.job_queue.run_once(
        _delete_job,
        when=seconds,
        data={"chat_id": chat_id, "message_id": message_id},
        name=f"del_{chat_id}_{message_id}",
    )


# ─────────────────────────────────────────────────────────────────────────────
# /start — Age verification
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    add_user(user.id)

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅  Yes, I'm 18+", callback_data="age:yes"),
            InlineKeyboardButton("🔞  Below 18",      callback_data="age:no"),
        ]
    ])

    await update.message.reply_text(
        f"👋 <b>Welcome, {user.first_name}!</b>\n\n"
        "Please confirm your age before continuing 👇",
        parse_mode="HTML",
        reply_markup=kb,
    )


async def cb_age(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Different response per age choice — buttons disappear, link auto-deletes in 30 min."""
    query = update.callback_query
    await query.answer()
    add_user(query.from_user.id)

    name = query.from_user.first_name

    if query.data == "age:yes":
        # ── 18+ confirmed ──
        confirm_text = (
            f"🔥 <b>Welcome to the inner circle, {name}!</b>\n\n"
            "You're verified as an adult — no gates, no filters, no limits.\n"
            "The good stuff is all yours. 😈"
        )
    else:
        # ── Below 18 ──
        confirm_text = (
            f"🫣 <b>Aww, still cooking, {name}?</b>\n\n"
            "Not quite an adult yet — but hey, curiosity is healthy! 👀\n"
            "Since you're already here… might as well take a peek. 🤫"
        )

    # Edit the original message — removes the buttons cleanly
    await query.message.edit_text(confirm_text, parse_mode="HTML")

    # Send the channel link as a follow-up (auto-deletes in 30 min)
    link_msg = await query.message.reply_text(
        f"🔗 <b>Here's your link:</b>\n\n"
        f"👉 {CHANNEL_LINK}\n\n"
        "⏳ <i>This message disappears in <b>30 minutes</b>. Don't miss it!</i>",
        parse_mode="HTML",
        disable_web_page_preview=False,
    )

    schedule_delete(context, link_msg.chat_id, link_msg.message_id, seconds=1800)


# ─────────────────────────────────────────────────────────────────────────────
# /broadcast — Admin-only conversation
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ You're not authorised to use this command.")
        return ConversationHandler.END

    await update.message.reply_text(
        "📢 <b>Broadcast Setup — Step 1/3</b>\n\n"
        "Send your broadcast message.\n"
        "<i>Supports text, photo, video, document, audio — anything.</i>\n\n"
        "/cancel to abort.",
        parse_mode="HTML",
    )
    return ASK_MSG


async def got_broadcast_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["bc_msg"] = update.message

    await update.message.reply_text(
        "⏱ <b>Broadcast Setup — Step 2/3</b>\n\n"
        "How many <b>minutes</b> until the broadcast auto-deletes?\n"
        "Send <code>0</code> for <b>no auto-delete</b>.\n\n"
        "<i>Examples: 60 = 1 hour, 1440 = 1 day</i>",
        parse_mode="HTML",
    )
    return ASK_TIMER


async def got_timer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        mins = int(update.message.text.strip())
        if mins < 0:
            raise ValueError("Negative value")
    except ValueError:
        await update.message.reply_text("❌ Please send a valid number (e.g. <code>60</code>):", parse_mode="HTML")
        return ASK_TIMER

    context.user_data["bc_timer"] = mins
    timer_label = f"<b>{mins} minute(s)</b>" if mins > 0 else "<b>No auto-delete</b>"

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📌  Pin Message", callback_data="bc_pin:yes"),
        InlineKeyboardButton("➡️  Skip",        callback_data="bc_pin:no"),
    ]])

    await update.message.reply_text(
        f"📌 <b>Broadcast Setup — Step 3/3</b>\n\n"
        f"⏱ Timer: {timer_label}\n\n"
        "Pin the broadcast message in each chat?",
        parse_mode="HTML",
        reply_markup=kb,
    )
    return ASK_PIN


async def cb_pin_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    should_pin = query.data == "bc_pin:yes"
    bc_msg     = context.user_data.get("bc_msg")
    bc_timer   = context.user_data.get("bc_timer", 0)
    users      = load_users()

    # Remove admin from broadcast targets (optional — comment out to include self)
    targets = [uid for uid in users if uid not in ADMIN_IDS]

    progress_msg = await query.message.edit_text(
        f"📤 Broadcasting to <b>{len(targets)}</b> users...\n"
        "Please wait ⏳",
        parse_mode="HTML",
    )

    sent, failed, pinned = 0, 0, 0

    for uid in targets:
        try:
            copied = await bc_msg.copy(chat_id=uid)
            sent += 1

            # Pin the message
            if should_pin:
                try:
                    await context.bot.pin_chat_message(
                        chat_id=uid,
                        message_id=copied.message_id,
                        disable_notification=True,
                    )
                    pinned += 1
                except Exception as pin_err:
                    logger.debug(f"Pin failed for {uid}: {pin_err}")

            # Schedule auto-delete
            if bc_timer > 0:
                schedule_delete(context, uid, copied.message_id, bc_timer * 60)

            await asyncio.sleep(0.05)   # Respect Telegram rate limits

        except Exception as e:
            failed += 1
            logger.warning(f"Failed to broadcast to {uid}: {e}")

    # ── Summary ──
    timer_info = (
        f"🗑 Auto-deletes in <b>{bc_timer} min</b>"
        if bc_timer > 0 else "🔄 <b>No auto-delete</b>"
    )
    pin_info = (
        f"📌 Pinned in <b>{pinned}</b> chat(s)"
        if should_pin else "📌 Not pinned"
    )

    await progress_msg.edit_text(
        "✅ <b>Broadcast Complete!</b>\n\n"
        f"📤 Sent:    <b>{sent}</b>\n"
        f"❌ Failed:  <b>{failed}</b>\n"
        f"{pin_info}\n"
        f"{timer_info}",
        parse_mode="HTML",
    )

    context.user_data.clear()
    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("❌ Broadcast cancelled.")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# /stats — Admin user count
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    users = load_users()
    await update.message.reply_text(
        f"📊 <b>Bot Statistics</b>\n\n"
        f"👥 Total users: <b>{len(users)}</b>",
        parse_mode="HTML",
    )


# ─────────────────────────────────────────────────────────────────────────────
# PASSIVE USER TRACKER  (group -1 runs before all other handlers)
# ─────────────────────────────────────────────────────────────────────────────
async def _track_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user:
        add_user(update.effective_user.id)


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Passive user tracker — runs first for every update
    app.add_handler(MessageHandler(filters.ALL, _track_user), group=-1)
    app.add_handler(CallbackQueryHandler(_track_user),         group=-1)

    # /start
    app.add_handler(CommandHandler("start", cmd_start))

    # Age gate callback
    app.add_handler(CallbackQueryHandler(cb_age, pattern=r"^age:"))

    # /stats (admin)
    app.add_handler(CommandHandler("stats", cmd_stats))

    # Broadcast conversation (admin)
    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler("broadcast", cmd_broadcast)],
        states={
            ASK_MSG: [
                MessageHandler(
                    filters.ALL & ~filters.COMMAND,
                    got_broadcast_msg,
                )
            ],
            ASK_TIMER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, got_timer)
            ],
            ASK_PIN: [
                CallbackQueryHandler(cb_pin_choice, pattern=r"^bc_pin:")
            ],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        name="broadcast",
        persistent=False,
    )
    app.add_handler(broadcast_conv)

    logger.info("✅ Bot is running — polling for updates …")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
