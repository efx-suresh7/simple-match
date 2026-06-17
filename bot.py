"""
╔══════════════════════════════════════════════════════════════╗
║         ANONYMOUS CHAT BOT  — Upgraded Version              ║
║  Firebase · Inline Buttons · Games · Webhook (Vercel)       ║
╚══════════════════════════════════════════════════════════════╝
"""
import os
from flask import Flask
from threading import Thread
import logging
import os
import random
import asyncio
from collections import deque
from datetime import datetime, timezone

import firebase_admin
from firebase_admin import credentials, firestore
from telegram.ext import Application

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# ══════════════════════════════════════════════
#  CONFIGURATION  (set via environment variables)
# ══════════════════════════════════════════════
from dotenv import load_dotenv
load_dotenv()

TOKEN            = os.environ.get("TOKEN", os.environ.get("BOT_TOKEN", ""))
ADMIN_USER_ID    = int(os.environ.get("ADMIN_USER_ID", "7961164240"))
WEBHOOK_URL      = os.environ.get("WEBHOOK_URL", "")          # e.g. https://your-app.vercel.app
WEBHOOK_PATH     = "/webhook"
PORT             = int(os.environ.get("PORT", "8443"))

# ══════════════════════════════════════════════
#  FIREBASE SETUP
# ══════════════════════════════════════════════
_firebase_key_json = os.environ.get("FIREBASE_KEY_JSON", "")   # full JSON string of service-account key
if _firebase_key_json:
    import json
    try:
        cert_dict = json.loads(_firebase_key_json)
        cred = credentials.Certificate(cert_dict)
    except Exception as e:
        logger.error(f"Failed to parse FIREBASE_KEY_JSON: {e}")
        raise e
else:
    # Fallback: place serviceAccountKey.json in the project root for local dev
    cred = credentials.Certificate("serviceAccountKey.json")

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)

db = firestore.client()

# ══════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════
#  IN-MEMORY WAITING QUEUE
# ══════════════════════════════════════════════
waiting_users: deque = deque()

# ══════════════════════════════════════════════
#  FIREBASE HELPERS
# ══════════════════════════════════════════════
def get_user(user_id: str) -> dict | None:
    doc = db.collection("users").document(user_id).get()
    return doc.to_dict() if doc.exists else None

def save_user(user_id: str, data: dict):
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    db.collection("users").document(user_id).set(data, merge=True)

def delete_user(user_id: str):
    db.collection("users").document(user_id).delete()

def is_banned(user_id: str) -> bool:
    doc = db.collection("banned_users").document(user_id).get()
    return doc.exists

def ban_user(user_id: str, reason: str = ""):
    db.collection("banned_users").document(user_id).set(
        {"reason": reason, "banned_at": datetime.now(timezone.utc).isoformat()}
    )

def unban_user(user_id: str):
    db.collection("banned_users").document(user_id).delete()

def get_all_users() -> list[dict]:
    return [{"id": d.id, **d.to_dict()} for d in db.collection("users").stream()]

# ══════════════════════════════════════════════
#  KEYBOARD BUILDERS
# ══════════════════════════════════════════════
def main_menu_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("🔍 Find Partner"), KeyboardButton("👤 My Profile")],
            [KeyboardButton("🔗 Refer Now"), KeyboardButton("📊 My Referrals")],
            [KeyboardButton("🎮 Games"), KeyboardButton("ℹ️ Help")],
        ],
        resize_keyboard=True,
    )

def gender_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("👦 Male", callback_data="gender_Male"),
                InlineKeyboardButton("👧 Female", callback_data="gender_Female"),
            ],
            [InlineKeyboardButton("🏳️‍🌈 Other", callback_data="gender_Other")],
        ]
    )

def interest_keyboard():
    options = ["Friendship 🤝", "Relationship 💑", "Just Chat 💬", "Gaming 🎮", "Music 🎵"]
    rows = [[InlineKeyboardButton(o, callback_data=f"interest_{o}")] for o in options]
    return InlineKeyboardMarkup(rows)

def chat_action_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎮 Play Game", callback_data="chat_game"),
                InlineKeyboardButton("👤 View Profile", callback_data="chat_profile"),
            ],
            [InlineKeyboardButton("🚫 End Chat", callback_data="chat_stop")],
        ]
    )

def game_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎲 Dice Roll", callback_data="game_dice")],
            [InlineKeyboardButton("✊ Rock Paper Scissors", callback_data="game_rps")],
            [InlineKeyboardButton("🔢 Number Guess", callback_data="game_number")],
            [InlineKeyboardButton("❓ Truth or Dare", callback_data="game_tod")],
            [InlineKeyboardButton("🔙 Back", callback_data="game_back")],
        ]
    )

def rps_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✊ Rock", callback_data="rps_rock"),
                InlineKeyboardButton("✋ Paper", callback_data="rps_paper"),
                InlineKeyboardButton("✌️ Scissors", callback_data="rps_scissors"),
            ]
        ]
    )

def accept_decline_keyboard(requester_id: str):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Accept", callback_data=f"match_accept_{requester_id}"),
                InlineKeyboardButton("❌ Decline", callback_data=f"match_decline_{requester_id}"),
            ]
        ]
    )

# ══════════════════════════════════════════════
#  REGISTRATION FLOW
# ══════════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    if is_banned(user_id):
        await update.message.reply_text("🚫 You are banned from using this bot.")
        return

    existing = get_user(user_id)
    if existing:
        await update.message.reply_text(
            f"👋 Welcome back, *{existing['name']}*!\nChoose an option below:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu_keyboard(),
        )
        return

    context.user_data.clear()
    
    # Store Referral Code
    args = context.args
    if args and args[0] != user_id:
        context.user_data["temp_referrer"] = args[0]

    await update.message.reply_text(
        "🎉 *Welcome to SimpleMatches 2.O!*\n\n"
        "Connect with real people.\n"
        "Let's set up your profile first!\n\n"
        "👤 Please enter your *name*:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data["reg_step"] = "name"

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    text = update.message.text.strip()

    # ── Main menu button presses ──
    if text == "🔍 Find Partner":
        await find(update, context)
        return
    if text == "👤 My Profile":
        await my_profile(update, context)
        return
    if text == "🎮 Games":
        await show_games(update, context)
        return
    if text == "ℹ️ Help":
        await help_cmd(update, context)
        return
    if text == "🔗 Refer Now":
        bot_username = context.bot.username
        await update.message.reply_text(
            f"🚀 *Earn VIP Match Points!*\n\n"
            f"Share your unique link below. You get *+3 Points* every time someone registers using it!\n\n"
            f"`https://t.me/{bot_username}?start={user_id}`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
    if text == "📊 My Referrals":
        user = get_user(user_id)
        if user:
            pts = user.get("points", 0)
            ref_count = user.get("referrals", 0)
            await update.message.reply_text(
                f"🌟 *Your Referral Stats*\n\n"
                f"👥 People Referred: *{ref_count}*\n"
                f"💰 Total Points: *{pts}*\n\n"
                f"_(You need 4 points to do a VIP Interest Match!)_",
                parse_mode=ParseMode.MARKDOWN
            )
        return

    # ── Registration flow ──
    step = context.user_data.get("reg_step")
    if step:
        await _handle_registration(update, context, user_id, text, step)
        return

    # ── Number-guess game input ──
    if context.user_data.get("number_guess_active"):
        await _handle_number_guess(update, context, user_id, text)
        return

    # ── Active chat relay ──
    user = get_user(user_id)
    if user and user.get("chatting_with"):
        partner_id = user["chatting_with"]
        try:
            await context.bot.send_message(
                chat_id=int(partner_id),
                text=f"💬 {text}",
            )
        except Exception:
            await update.message.reply_text("⚠️ Could not deliver message. Partner may have left.")
        return

    # ── Not registered yet ──
    if not get_user(user_id):
        await update.message.reply_text(
            "Please use /start to register first.", reply_markup=ReplyKeyboardRemove()
        )

async def _handle_registration(update, context, user_id, text, step):
    temp = context.user_data.setdefault("profile_temp", {})

    if step == "name":
        if len(text) < 2 or len(text) > 30:
            await update.message.reply_text("❌ Name must be 2–30 characters. Try again:")
            return
        if not all(c.isalpha() or c.isspace() for c in text):
            await update.message.reply_text("❌ Name should contain only letters. Try again:")
            return
        temp["name"] = text.title()
        await update.message.reply_text("🎂 How old are you? (Enter a number, 13–60):")
        context.user_data["reg_step"] = "age"

    elif step == "age":
        if not text.isdigit() or not (18 <= int(text) <= 60):
            await update.message.reply_text("❌ Please enter a valid age between 18 and 60:")
            return
        temp["age"] = int(text)
        await update.message.reply_text(
            "⚧ Select your gender:", reply_markup=gender_keyboard()
        )
        context.user_data["reg_step"] = "gender_wait"

    elif step == "place":
        if len(text) < 2 or len(text) > 50:
            await update.message.reply_text("❌ Location must be 2–50 characters. Try again:")
            return
        temp["place"] = text.title()
        await update.message.reply_text(
            "💭 Tell us about yourself in a short bio (or type *skip*):",
            parse_mode=ParseMode.MARKDOWN,
        )
        context.user_data["reg_step"] = "bio"

    elif step == "bio":
        temp["bio"] = "" if text.lower() == "skip" else text[:200]
        await update.message.reply_text(
            "💡 What are you looking for?", reply_markup=interest_keyboard()
        )
        context.user_data["reg_step"] = "interest_wait"

async def _finish_registration(update, context, user_id):
    temp = context.user_data.get("profile_temp", {})
    
    # 10 points for all new users!
    profile = {
        "name": temp["name"],
        "age": temp["age"],
        "gender": temp["gender"],
        "interested_gender": temp.get("interested_gender", "Other"),
        "place": temp["place"],
        "bio": temp.get("bio", ""),
        "interest": temp.get("interest", "Just Chat 💬"),
        "chatting_with": None,
        "waiting_for_acceptance": False,
        "joined_at": datetime.now(timezone.utc).isoformat(),
        "chat_count": 0,
        "points": 10
    }
    save_user(user_id, profile)
    
    referrer = context.user_data.get("temp_referrer")
    if referrer:
        ref_user = get_user(referrer)
        if ref_user:
            ref_pts = ref_user.get("points", 0) + 3
            ref_count = ref_user.get("referrals", 0) + 1
            save_user(referrer, {"points": ref_pts, "referrals": ref_count})
            try:
                await context.bot.send_message(
                    chat_id=int(referrer),
                    text=f"🎉 Someone used your referral link! You earned 3 points. Total: {ref_pts}"
                )
            except Exception:
                pass

    context.user_data.clear()

    msg = (
        f"✅ *Profile Created!*\n\n"
        f"👤 Name: *{profile['name']}*\n"
        f"🎂 Age: *{profile['age']}*\n"
        f"⚧ Gender: *{profile['gender']}*\n"
        f"📍 Place: *{profile['place']}*\n"
        f"💡 Looking for: *{profile['interest']}*\n"
        f"📝 Bio: _{profile['bio'] or 'Not set'}_\n\n"
        f"Use the menu below to find a chat partner! 🎉"
    )
    await update.effective_message.reply_text(
        msg, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard()
    )

# ══════════════════════════════════════════════
#  CALLBACK QUERY HANDLER (inline buttons)
# ══════════════════════════════════════════════
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = str(query.from_user.id)

    # ── Gender selection ──
    if data.startswith("gender_"):
        if context.user_data.get("reg_step") == "gender_wait":
            context.user_data["profile_temp"]["gender"] = data.split("_", 1)[1]
            await query.edit_message_text("🎯 Which gender are you interested in connecting with?", reply_markup=gender_keyboard())
            context.user_data["reg_step"] = "interested_gender_wait"
        elif context.user_data.get("reg_step") == "interested_gender_wait":
            context.user_data["profile_temp"]["interested_gender"] = data.split("_", 1)[1]
            await query.edit_message_text("📍 What city/state are you from?")
            context.user_data["reg_step"] = "place"

    # ── Interest selection ──
    elif data.startswith("interest_"):
        if context.user_data.get("reg_step") != "interest_wait":
            return
        context.user_data["profile_temp"]["interest"] = data.split("_", 1)[1]
        await query.edit_message_text("⏳ Saving your profile...")
        await _finish_registration(update, context, user_id)

    # ── Match accept / decline ──
    elif data.startswith("match_accept_"):
        requester_id = data.split("_", 2)[2]
        await _do_accept(update, context, user_id, requester_id)

    elif data.startswith("match_decline_"):
        requester_id = data.split("_", 2)[2]
        await query.edit_message_text("❌ You declined the match request.")
        try:
            await context.bot.send_message(
                chat_id=int(requester_id),
                text="😔 Your match request was declined. Use 🔍 Find Partner to try again.",
            )
        except Exception:
            pass

    # ── Find Callbacks ──
    elif data == "find_free":
        await _do_find(update, context, user_id, is_vip=False, target_gender=None)
        
    elif data == "find_vip_menu":
        user = get_user(user_id)
        if user and user.get("points", 0) < 4:
            await query.edit_message_text("❌ You don't have enough points (4 required). Use '🔗 Refer Now' from the menu to earn more!")
            return
            
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("👦 Male", callback_data="find_vip_Male"),
             InlineKeyboardButton("👧 Female", callback_data="find_vip_Female")],
            [InlineKeyboardButton("🏳️‍🌈 Other", callback_data="find_vip_Other")]
        ])
        await query.edit_message_text("🎯 Select the gender you want to connect with:\n\n_(4 points will be deducted when matched)_", reply_markup=kb)

    elif data.startswith("find_vip_") and data != "find_vip_menu":
        target = data.split("_")[2]
        await _do_find(update, context, user_id, is_vip=True, target_gender=target)

    # ── Chat actions ──
    elif data == "chat_stop":
        await _end_chat(update, context, user_id)

    elif data == "chat_profile":
        user = get_user(user_id)
        partner_id = user.get("chatting_with") if user else None
        if partner_id:
            partner = get_user(partner_id)
            if partner:
                await query.edit_message_text(
                    f"👤 *Partner Profile*\n\n"
                    f"🎂 Age: {partner['age']}\n"
                    f"⚧ Gender: {partner['gender']}\n"
                    f"📍 Place: {partner['place']}\n"
                    f"💡 Looking for: {partner.get('interest','—')}\n"
                    f"📝 Bio: _{partner.get('bio','Not set')}_",
                    parse_mode=ParseMode.MARKDOWN,
                )

    elif data == "chat_game":
        await query.edit_message_text("🎮 Choose a game to play with your partner:", reply_markup=game_menu_keyboard())

    elif data == "game_back":
        await query.edit_message_text("💬 Chat options:", reply_markup=chat_action_keyboard())

    # ── Games ──
    elif data == "game_dice":
        await _game_dice(update, context, user_id)

    elif data == "game_rps":
        await _game_rps_start(update, context, user_id)

    elif data.startswith("rps_"):
        await _game_rps_play(update, context, user_id, data.split("_", 1)[1])

    elif data == "game_number":
        await _game_number_start(update, context, user_id)

    elif data == "game_tod":
        await _game_truth_or_dare(update, context, user_id)

# ══════════════════════════════════════════════
#  FIND / MATCH
# ══════════════════════════════════════════════
async def find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    if is_banned(user_id):
        await update.effective_message.reply_text("🚫 You are banned.")
        return

    user = get_user(user_id)
    if not user:
        await update.effective_message.reply_text(
            "❌ Please register first with /start.", reply_markup=ReplyKeyboardRemove()
        )
        return

    if user.get("chatting_with"):
        await update.effective_message.reply_text(
            "❗ You're already in a chat. Use 🚫 End Chat first.",
            reply_markup=chat_action_keyboard(),
        )
        return

    # User points
    points = user.get("points", 0)

    # Show inline keyboard
    kb = InlineKeyboardMarkup([
       [InlineKeyboardButton("🎲 Random Match (Free)", callback_data="find_free")],
       [InlineKeyboardButton(f"🎯 VIP Select Gender (4 Pts) | Pts: {points}", callback_data="find_vip_menu")]
    ])
    await update.effective_message.reply_text("How would you like to match?", reply_markup=kb)

async def _do_find(update, context, user_id, is_vip, target_gender=None):
    user = get_user(user_id)
    if not user: return
    
    if is_vip and user.get("points", 0) < 4:
        await update.callback_query.edit_message_text("❌ You don't have enough points (4 required). Share your profile to earn more!")
        return

    # Check if already in queue
    for q_id, _, _ in waiting_users:
        if q_id == user_id:
            await update.callback_query.edit_message_text("⏳ Already in queue! Please wait...")
            return

    # Try to match from queue
    # waiting_users is now deque of tuples: (user_id, is_vip, target_gender)
    for other_tuple in list(waiting_users):
        other_id, other_is_vip, other_target_gender = other_tuple
        if other_id == user_id: continue

        other = get_user(other_id)
        if not other:
            waiting_users.remove(other_tuple)
            continue
            
        match_ok = False
        
        # If I am VIP, other must be my explicitly selected gender
        my_req_met = True
        if is_vip:
            if other.get("gender") != target_gender:
                my_req_met = False
        else:
            # Random match, or default (opposite gender)
            if other.get("gender") == user.get("gender"):
                my_req_met = False

        # If Other is VIP, I must be their explicitly selected gender
        their_req_met = True
        if other_is_vip:
            if user.get("gender") != other_target_gender:
                their_req_met = False
        else:
            if other.get("gender") == user.get("gender"):
                their_req_met = False
                
        if my_req_met and their_req_met:
            match_ok = True

        if match_ok:
            waiting_users.remove(other_tuple)
            save_user(user_id, {"chatting_with": other_id})
            save_user(other_id, {"chatting_with": user_id, "waiting_for_acceptance": False})
            
            # Deduct points if VIP
            if is_vip: save_user(user_id, {"points": user.get("points", 0) - 4})
            if other_is_vip: save_user(other_id, {"points": other.get("points", 0) - 4})

            partner_name = user["name"]
            other_name = other["name"]

            await context.bot.send_message(
                chat_id=int(other_id),
                text=(
                    f"🎉 *Match Found!*\n\n"
                    f"You are now chatting with *{partner_name}* anonymously!\n"
                    f"Say hi! 👋\n\nUse the buttons below for chat options."
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=chat_action_keyboard(),
            )
            await update.callback_query.edit_message_text(
                f"🎉 *Match Found!*\n\n"
                f"You are now chatting with *{other_name}* anonymously!\n"
                f"Say hi! 👋",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=chat_action_keyboard(),
            )
            return

    # No match found — add to queue
    waiting_users.append((user_id, is_vip, target_gender))
    await update.callback_query.edit_message_text(
        "⏳ *Searching for a partner...*\n\nYou'll be notified when matched!\n"
        "_(You can keep using the bot while waiting)_",
        parse_mode=ParseMode.MARKDOWN,
    )

async def _do_accept(update, context, user_id, requester_id):
    user = get_user(user_id)
    requester = get_user(requester_id)

    if not user or not requester:
        await update.callback_query.edit_message_text("❌ User not found.")
        return

    save_user(user_id, {"chatting_with": requester_id})
    save_user(requester_id, {"chatting_with": user_id, "waiting_for_acceptance": False})

    await update.callback_query.edit_message_text(
        f"✅ Connected with *{requester['name']}*! Start chatting 💬",
        parse_mode=ParseMode.MARKDOWN,
    )
    await context.bot.send_message(
        chat_id=int(requester_id),
        text=f"✅ *{user['name']}* accepted your request! Start chatting 💬",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=chat_action_keyboard(),
    )

async def _end_chat(update, context, user_id):
    user = get_user(user_id)
    partner_id = user.get("chatting_with") if user else None

    save_user(user_id, {"chatting_with": None})
    if partner_id:
        save_user(partner_id, {"chatting_with": None})
        try:
            await context.bot.send_message(
                chat_id=int(partner_id),
                text="😔 Your chat partner has left. Use 🔍 Find Partner to find someone new!",
                reply_markup=main_menu_keyboard(),
            )
        except Exception:
            pass

    if update.callback_query:
        await update.callback_query.edit_message_text("✅ Chat ended. Use the menu to find a new partner.")
    else:
        await update.effective_message.reply_text(
            "✅ Chat ended.", reply_markup=main_menu_keyboard()
        )

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    await _end_chat(update, context, user_id)

# ══════════════════════════════════════════════
#  PROFILE
# ══════════════════════════════════════════════
async def my_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user = get_user(user_id)
    if not user:
        await update.effective_message.reply_text("❌ Not registered. Use /start.")
        return

    edit_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Edit Name", callback_data="edit_name"),
         InlineKeyboardButton("✏️ Edit Bio", callback_data="edit_bio")],
        [InlineKeyboardButton("✏️ Edit Place", callback_data="edit_place")],
    ])

    bot_username = context.bot.username
    await update.effective_message.reply_text(
        f"👤 *Your Profile*\n\n"
        f"📛 Name: *{user['name']}*\n"
        f"🎂 Age: *{user['age']}*\n"
        f"⚧ Gender: *{user['gender']}*\n"
        f"📍 Place: *{user['place']}*\n"
        f"💡 Looking for: *{user.get('interest','—')}*\n"
        f"📝 Bio: _{user.get('bio','Not set')}_\n"
        f"💬 Total Chats: *{user.get('chat_count',0)}*\n\n"
        f"🌟 *Points: {user.get('points', 0)}*\n"
        f"🔗 *Invite Link (+3 pts each):*\n`https://t.me/{bot_username}?start={user_id}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=edit_keyboard,
    )

# ══════════════════════════════════════════════
#  GAMES
# ══════════════════════════════════════════════
async def show_games(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user = get_user(user_id)
    if not user or not user.get("chatting_with"):
        await update.effective_message.reply_text(
            "🎮 *Games*\n\nYou need to be in a chat to play games with your partner!\n"
            "Use 🔍 Find Partner first.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    await update.effective_message.reply_text(
        "🎮 Choose a game to play with your partner:",
        reply_markup=game_menu_keyboard(),
    )

async def _game_dice(update, context, user_id):
    user = get_user(user_id)
    partner_id = user.get("chatting_with") if user else None

    my_roll = random.randint(1, 6)
    partner_roll = random.randint(1, 6)

    result = "🏆 You win!" if my_roll > partner_roll else ("🤝 It's a tie!" if my_roll == partner_roll else "😔 Partner wins!")

    msg = (
        f"🎲 *Dice Roll!*\n\n"
        f"You rolled: *{my_roll}*\n"
        f"Partner rolled: *{partner_roll}*\n\n"
        f"{result}"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN)
    else:
        await update.effective_message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    if partner_id:
        partner_result = "🏆 You win!" if partner_roll > my_roll else ("🤝 It's a tie!" if partner_roll == my_roll else "😔 Partner wins!")
        try:
            await context.bot.send_message(
                chat_id=int(partner_id),
                text=(
                    f"🎲 *Dice Roll (invited by partner)!*\n\n"
                    f"You rolled: *{partner_roll}*\n"
                    f"Partner rolled: *{my_roll}*\n\n"
                    f"{partner_result}"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

async def _game_rps_start(update, context, user_id):
    user = get_user(user_id)
    if not user or not user.get("chatting_with"):
        await update.callback_query.edit_message_text("❌ You need to be in a chat to play!")
        return
    context.user_data["rps_waiting"] = True
    await update.callback_query.edit_message_text(
        "✊ *Rock Paper Scissors!*\n\nMake your choice:", reply_markup=rps_keyboard()
    )

async def _game_rps_play(update, context, user_id, choice):
    partner_choice = random.choice(["rock", "paper", "scissors"])
    wins = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
    emoji = {"rock": "✊", "paper": "✋", "scissors": "✌️"}

    if choice == partner_choice:
        result = "🤝 It's a tie!"
    elif wins[choice] == partner_choice:
        result = "🏆 You win!"
    else:
        result = "😔 You lose!"

    msg = (
        f"✊ *Rock Paper Scissors!*\n\n"
        f"You: {emoji[choice]} {choice.title()}\n"
        f"Partner: {emoji[partner_choice]} {partner_choice.title()}\n\n"
        f"{result}"
    )
    await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN)

    user = get_user(user_id)
    partner_id = user.get("chatting_with") if user else None
    if partner_id:
        p_result = "😔 You lose!" if result == "🏆 You win!" else ("🏆 You win!" if result == "😔 You lose!" else "🤝 It's a tie!")
        try:
            await context.bot.send_message(
                chat_id=int(partner_id),
                text=(
                    f"✊ *Rock Paper Scissors (partner played)!*\n\n"
                    f"Partner: {emoji[choice]} {choice.title()}\n"
                    f"You: {emoji[partner_choice]} {partner_choice.title()}\n\n"
                    f"{p_result}"
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

async def _game_number_start(update, context, user_id):
    user = get_user(user_id)
    if not user or not user.get("chatting_with"):
        await update.callback_query.edit_message_text("❌ You need to be in a chat to play!")
        return
    secret = random.randint(1, 20)
    context.user_data["number_secret"] = secret
    context.user_data["number_attempts"] = 0
    context.user_data["number_guess_active"] = True
    await update.callback_query.edit_message_text(
        "🔢 *Number Guessing Game!*\n\n"
        "I'm thinking of a number between *1 and 20*.\n"
        "Type your guess! You have 5 attempts.",
        parse_mode=ParseMode.MARKDOWN,
    )

async def _handle_number_guess(update, context, user_id, text):
    if not text.isdigit():
        await update.message.reply_text("Please enter a valid number.")
        return
    guess = int(text)
    secret = context.user_data["number_secret"]
    context.user_data["number_attempts"] += 1
    attempts = context.user_data["number_attempts"]

    if guess == secret:
        context.user_data["number_guess_active"] = False
        await update.message.reply_text(
            f"🎉 *Correct!* The number was *{secret}*!\n"
            f"You got it in {attempts} attempt(s)! 🏆",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=chat_action_keyboard(),
        )
    elif attempts >= 5:
        context.user_data["number_guess_active"] = False
        await update.message.reply_text(
            f"😔 *Game Over!* The number was *{secret}*.\nBetter luck next time!",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=chat_action_keyboard(),
        )
    elif guess < secret:
        await update.message.reply_text(f"📈 Too low! Try higher. ({5 - attempts} attempts left)")
    else:
        await update.message.reply_text(f"📉 Too high! Try lower. ({5 - attempts} attempts left)")

TRUTHS = [
    "What's the most embarrassing thing you've ever done?",
    "What's your biggest fear?",
    "Have you ever lied to a close friend?",
    "What's your hidden talent?",
    "What's your biggest regret?",
    "Describe your ideal relationship.",
    "What's the weirdest dream you've ever had?",
]
DARES = [
    "Send a funny message to your best friend right now!",
    "Type 'I love pizza' 10 times in a row!",
    "Tell your chat partner something surprising about yourself.",
    "Send the most recent photo in your gallery to your partner.",
    "Write a poem about today (2 lines minimum).",
    "Describe your day using only emojis.",
]

async def _game_truth_or_dare(update, context, user_id):
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💡 Truth", callback_data="tod_truth"),
            InlineKeyboardButton("💪 Dare", callback_data="tod_dare"),
        ]
    ])
    await update.callback_query.edit_message_text(
        "❓ *Truth or Dare?*\n\nPick one!", reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN
    )

# Handle truth/dare answer separately via callback
async def _tod_answer(update, context, choice):
    if choice == "truth":
        question = random.choice(TRUTHS)
        await update.callback_query.edit_message_text(
            f"💡 *Truth:*\n\n_{question}_", parse_mode=ParseMode.MARKDOWN
        )
    else:
        dare = random.choice(DARES)
        await update.callback_query.edit_message_text(
            f"💪 *Dare:*\n\n_{dare}_", parse_mode=ParseMode.MARKDOWN
        )

# ══════════════════════════════════════════════
#  ADMIN COMMANDS
# ══════════════════════════════════════════════
def require_admin(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_USER_ID:
            await update.effective_message.reply_text("🚫 Admins only.")
            return
        return await func(update, context)
    return wrapper

@require_admin
async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = get_all_users()
    active = sum(1 for u in users if u.get("chatting_with"))
    waiting = len(waiting_users)
    await update.message.reply_text(
        f"📊 *Bot Statistics*\n\n"
        f"👥 Total Users: *{len(users)}*\n"
        f"💬 Active Chats: *{active // 2}*\n"
        f"⏳ Waiting: *{waiting}*",
        parse_mode=ParseMode.MARKDOWN,
    )

@require_admin
async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = " ".join(context.args)
    if not msg:
        await update.message.reply_text("Usage: /broadcast Your message here")
        return
    users = get_all_users()
    success = failed = 0
    for u in users:
        try:
            await context.bot.send_message(chat_id=int(u["id"]), text=f"📢 *Announcement:*\n\n{msg}", parse_mode=ParseMode.MARKDOWN)
            success += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"✅ Sent to {success}, failed {failed}.")

@require_admin
async def admin_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /ban <user_id> [reason]")
        return
    target = context.args[0]
    reason = " ".join(context.args[1:]) or "No reason"
    ban_user(target, reason)
    await update.message.reply_text(f"✅ User {target} banned. Reason: {reason}")

@require_admin
async def admin_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /unban <user_id>")
        return
    unban_user(context.args[0])
    await update.message.reply_text(f"✅ User {context.args[0]} unbanned.")

@require_admin
async def admin_delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /deleteuser <user_id>")
        return
    delete_user(context.args[0])
    await update.message.reply_text(f"✅ User {context.args[0]} deleted.")

@require_admin
async def admin_givepoints(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /givepoints <user_id> <amount>")
        return
    try:
        user_id = context.args[0]
        amount = int(context.args[1])
        user = get_user(user_id)
        if not user:
            await update.message.reply_text("❌ User not found.")
            return
        new_pts = user.get("points", 0) + amount
        save_user(user_id, {"points": new_pts})
        await update.message.reply_text(f"✅ Added {amount} points to {user_id}. New balance: {new_pts}")
        try:
            await context.bot.send_message(chat_id=int(user_id), text=f"🎁 An admin mysteriously gave you {amount} points! Balance: {new_pts}")
        except Exception:
            pass
    except ValueError:
        await update.message.reply_text("Amount must be an integer.")

@require_admin
async def admin_topreferrals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = get_all_users()
    sorted_users = sorted(users, key=lambda x: x.get("points", 0), reverse=True)
    top_10 = sorted_users[:10]
    
    msg = "🏆 *Top 10 Users by Points*\n\n"
    for i, u in enumerate(top_10, 1):
        msg += f"{i}. {u.get('name', 'Unknown')} (`{u['id']}`) - *{u.get('points', 0)}* pts\n"
        
    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

# ══════════════════════════════════════════════
#  GENERAL COMMANDS
# ══════════════════════════════════════════════
async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "ℹ️ *Anonymous Chat Bot — Help*\n\n"
        "🔍 *Find Partner* — Match with a random user\n"
        "👤 *My Profile* — View/edit your profile\n"
        "🎮 *Games* — Play games with your partner\n"
        "🚫 End Chat — Stop current chat session\n\n"
        "💡 *During a chat:* just type and messages are relayed anonymously.\n\n"
        "🔒 Your identity stays hidden at all times!",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu_keyboard(),
    )

async def my_profile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await my_profile(update, context)

# ══════════════════════════════════════════════
#  CALLBACK ROUTER (extended)
# ══════════════════════════════════════════════
async def full_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = str(query.from_user.id)

    if data.startswith("tod_"):
        await _tod_answer(update, context, data.split("_", 1)[1])
    elif data.startswith("edit_"):
        field = data.split("_", 1)[1]
        await query.edit_message_text(f"✏️ Enter your new {field}:")
        context.user_data["edit_field"] = field
    else:
        await callback_handler(update, context)

# ══════════════════════════════════════════════
#  EDIT PROFILE (in handle_text)
# ══════════════════════════════════════════════
async def handle_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: str, text: str):
    field = context.user_data.pop("edit_field", None)
    if field == "name":
        if not (2 <= len(text) <= 30) or not all(c.isalpha() or c.isspace() for c in text):
            await update.message.reply_text("❌ Invalid name. Try again from profile.")
            return
        save_user(user_id, {"name": text.title()})
        await update.message.reply_text(f"✅ Name updated to *{text.title()}*!", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())
    elif field == "bio":
        save_user(user_id, {"bio": text[:200]})
        await update.message.reply_text("✅ Bio updated!", reply_markup=main_menu_keyboard())
    elif field == "place":
        save_user(user_id, {"place": text.title()})
        await update.message.reply_text(f"✅ Location updated to *{text.title()}*!", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_keyboard())

# ══════════════════════════════════════════════
#  FINAL TEXT HANDLER (wrap everything)
# ══════════════════════════════════════════════
async def smart_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)

    if context.user_data.get("edit_field"):
        await handle_edit(update, context, user_id, update.message.text.strip())
        return

    await handle_text(update, context)

# ══════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════
def main():
    app = ApplicationBuilder().token(TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("find", find))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("profile", my_profile_cmd))

    # Admin commands
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("broadcast", admin_broadcast))
    app.add_handler(CommandHandler("ban", admin_ban))
    app.add_handler(CommandHandler("unban", admin_unban))
    app.add_handler(CommandHandler("deleteuser", admin_delete_user))
    app.add_handler(CommandHandler("givepoints", admin_givepoints))
    app.add_handler(CommandHandler("topreferrals", admin_topreferrals))

    # Inline button callbacks
    app.add_handler(CallbackQueryHandler(full_callback_handler))

    # Text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, smart_text_handler))

    if WEBHOOK_URL:
        # Vercel / production — webhook mode
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=WEBHOOK_PATH,
            webhook_url=f"{WEBHOOK_URL}{WEBHOOK_PATH}",
        )
    else:
        # Local development — polling mode
        logger.info("Running in POLLING mode (local dev)")
        app.run_polling()

app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Bot is running!"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port)

# Start Flask web server in a background thread for Render/production port binding
Thread(target=run_web).start()

if __name__ == "__main__":
    main()
