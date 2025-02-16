import os
import logging
import sqlite3
import base64
import uuid
from datetime import datetime, timedelta
from dotenv import load_dotenv
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, 
    ContextTypes, CallbackQueryHandler, ConversationHandler
)
from PIL import Image
from io import BytesIO

# Load environment variables
load_dotenv()

# Configuration
TOGETHER_API_URL = "https://api.together.xyz/v1/images/generations"
MODEL_NAME = "black-forest-labs/FLUX.1-schnell-Free"
DATABASE_NAME = "indie_ai.sqlite"
STEPS = 4
RATE_LIMIT = timedelta(minutes=1)  # 1 minute cooldown
REFERRAL_TOKENS = 3  # Tokens awarded for successful referral

# Conversation states and constants
PROMPT, DIMENSIONS = range(2)

# Initialize logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Database setup
def init_db():
    conn = sqlite3.connect(DATABASE_NAME)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, username TEXT, tokens INTEGER DEFAULT 0, 
                 registered_at DATETIME, referral_code TEXT UNIQUE, referred_by INTEGER,
                 last_generated DATETIME)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS coupons
                 (code TEXT PRIMARY KEY, tokens INTEGER, created_at DATETIME, 
                 claimed_by INTEGER, claimed_at DATETIME)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS generations
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, prompt TEXT, 
                  generated_at DATETIME, cost INTEGER, width INTEGER, height INTEGER)''')
    
    conn.commit()
    conn.close()

init_db()

# Database helper functions (same structure as before)

# Enhanced Telegram Bot Functions
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    # Check if referral link used
    referral_code = context.args[0] if context.args else None
    
    if not db_fetch("SELECT * FROM users WHERE user_id = ?", (user_id,)):
        ref_code = str(uuid.uuid4())[:8].upper()
        db_execute("INSERT INTO users (user_id, username, registered_at, referral_code) VALUES (?, ?, ?, ?)",
                   (user_id, user.username, datetime.now(), ref_code))
        
        # Handle referral
        if referral_code:
            referrer = db_fetch("SELECT user_id FROM users WHERE referral_code = ?", (referral_code,))
            if referrer:
                referrer_id = referrer[0][0]
                db_execute("UPDATE users SET tokens = tokens + ? WHERE user_id = ?", (REFERRAL_TOKENS, referrer_id))
                db_execute("UPDATE users SET referred_by = ?, tokens = tokens + ? WHERE user_id = ?",
                           (referrer_id, REFERRAL_TOKENS, user_id))
    
    try:
        with open('welcome_banner.png', 'rb') as photo:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=photo,
                caption=f"🌟 *Welcome to INDIE AI* 🌟\n\n"
                       f"Hello {user.first_name}! I'm your creative AI companion.\n"
                       "Transform your ideas into stunning visual art with the power of AI!\n\n"
                       "✨ *Features* ✨\n"
                       "- Text-to-Image Generation 🎨\n"
                       "- Premium Quality Outputs 💎\n"
                       "- Referral Rewards System 🎁\n"
                       "- Daily Bonus Opportunities 🪙",
                parse_mode='Markdown'
            )
    except FileNotFoundError:
        await update.message.reply_text("🖼️ Welcome to INDIE AI - Your Creative Companion!")
    
    await show_user_dashboard(update, user_id)

async def show_user_dashboard(update: Update, user_id: int):
    user_data = db_fetch("SELECT tokens, referral_code FROM users WHERE user_id = ?", (user_id,))[0]
    keyboard = [
        [InlineKeyboardButton("🎨 Generate Art", callback_data='generate_art'),
         InlineKeyboardButton("💼 My Profile", callback_data='my_profile')],
        [InlineKeyboardButton("🎁 Redeem Code", callback_data='redeem_code'),
         InlineKeyboardButton("🌟 Refer Friends", callback_data='refer_friends')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.effective_message.reply_text(
        f"🔮 *INDIE AI Dashboard* 🔮\n\n"
        f"🪙 Tokens: {user_data[0]}\n"
        f"👥 Referral Code: `{user_data[1]}`\n\n"
        "Choose an option below:",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def handle_dashboard_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == 'generate_art':
        await generate_image_start(update, context)
    elif query.data == 'my_profile':
        await show_profile(update)
    elif query.data == 'redeem_code':
        await query.message.reply_text("Enter your coupon code using /claim CODE")
    elif query.data == 'refer_friends':
        await show_referral_info(update)

async def show_profile(update: Update):
    user = update.effective_user
    user_data = db_fetch(
        "SELECT tokens, referral_code, registered_at FROM users WHERE user_id = ?",
        (user.id,)
    )[0]
    generations = db_fetch("SELECT COUNT(*) FROM generations WHERE user_id = ?", (user.id,))[0][0]
    
    await update.effective_message.reply_text(
        f"📌 *INDIE AI Profile*\n\n"
        f"👤 Name: {user.full_name}\n"
        f"🆔 ID: `{user.id}`\n"
        f"🪙 Tokens: {user_data[0]}\n"
        f"🎨 Artworks Created: {generations}\n"
        f"📅 Member Since: {user_data[2].split()[0]}\n\n"
        f"🔑 Referral Code: `{user_data[1]}`\n"
        f"👉 Share your code to earn {REFERRAL_TOKENS} tokens per referral!",
        parse_mode='Markdown'
    )

async def show_referral_info(update: Update):
    user_data = db_fetch(
        "SELECT referral_code FROM users WHERE user_id = ?",
        (update.effective_user.id,)
    )[0]
    
    await update.effective_message.reply_text(
        f"🌟 *Referral Program*\n\n"
        f"Share your unique referral code and earn {REFERRAL_TOKENS} tokens "
        f"for every friend who joins!\n\n"
        f"Your personal code:\n`{user_data[0]}`\n\n"
        f"Share this link:\nhttps://t.me/{context.bot.username}?start={user_data[0]}",
        parse_mode='Markdown'
    )

async def generate_image_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    last_gen = db_fetch("SELECT last_generated FROM users WHERE user_id = ?", (user_id,))[0][0]
    
    # Rate limiting check
    if last_gen and (datetime.now() - datetime.strptime(last_gen, "%Y-%m-%d %H:%M:%S.%f")) < RATE_LIMIT:
        remaining = RATE_LIMIT - (datetime.now() - datetime.strptime(last_gen, "%Y-%m-%d %H:%M:%S.%f"))
        await update.message.reply_text(
            f"⏳ Please wait {remaining.seconds//60} minutes before generating another image."
        )
        return ConversationHandler.END
    
    if get_user_tokens(user_id) < 1:
        await update.message.reply_text(
            "💡 Oops! You need more tokens to generate art.\n"
            "Consider these options:\n"
            "- Invite friends using /invite\n"
            "- Redeem a coupon code using /claim\n"
            "- Contact support for assistance"
        )
        return ConversationHandler.END
    
    await update.message.reply_text(
        "🎨 *Let's Create Art!*\n\n"
        "Describe your vision in detail:\n"
        "(e.g., 'A cyberpunk cityscape at sunset with flying cars')",
        parse_mode='Markdown'
    )
    return PROMPT

# (Modify receive_dimensions to update last_generated timestamp)
async def receive_dimensions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... previous code ...
    
    # Update last generated time
    db_execute("UPDATE users SET last_generated = ? WHERE user_id = ?",
               (datetime.now(), user_id))
    
    # ... rest of the code ...

# Enhanced Admin Panel
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... previous auth check ...
    
    keyboard = [
        [InlineKeyboardButton("📊 Statistics", callback_data='stats'),
         InlineKeyboardButton("🎫 Create Coupon", callback_data='create_coupon')],
        [InlineKeyboardButton("👥 User List", callback_data='view_users'),
         InlineKeyboardButton("📜 Generation Logs", callback_data='view_logs')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "🔒 *Admin Panel* 🔒",
        parse_mode='Markdown',
        reply_markup=reply_markup
    )

async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... previous code ...
    
    if query.data == 'stats':
        total_users = db_fetch("SELECT COUNT(*) FROM users")[0][0]
        total_generations = db_fetch("SELECT COUNT(*) FROM generations")[0][0]
        active_coupons = db_fetch("SELECT COUNT(*) FROM coupons WHERE claimed_by IS NULL")[0][0]
        
        await query.edit_message_text(
            f"📊 *System Statistics*\n\n"
            f"👥 Total Users: {total_users}\n"
            f"🎨 Total Generations: {total_generations}\n"
            f"🎫 Active Coupons: {active_coupons}\n"
            f"🪙 Circulating Tokens: {sum([u[0] for u in db_fetch('SELECT tokens FROM users')])}",
            parse_mode='Markdown'
        )
    elif query.data == 'view_logs':
        logs = db_fetch("SELECT user_id, prompt, generated_at FROM generations ORDER BY id DESC LIMIT 10")
        message = "📜 *Recent Generations*\n\n" + "\n".join(
            [f"{log[2].split()[0]}: User {log[0]} - {log[1][:30]}..." for log in logs]
        )
        await query.edit_message_text(message, parse_mode='Markdown')

# Add support command
async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🛎️ *INDIE AI Support*\n\n"
        "Need help? Contact our support team:\n"
        "📧 Email: support@indieai.com\n"
        "🌐 Website: https://indieai.com/support\n\n"
        "For quick assistance, please include:\n"
        "- Your User ID\n"
        "- Screenshots if applicable\n"
        "- Detailed description of the issue",
        parse_mode='Markdown'
    )

def main():
    application = Application.builder().token(os.getenv("TELEGRAM_TOKEN")).build()

    # Add handlers
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CallbackQueryHandler(handle_dashboard_callback))
    application.add_handler(CommandHandler('support', support))
    
    # Add conversation handler and other required handlers
    # ... (existing conversation and admin handlers) ...
    
    application.run_polling()

if __name__ == "__main__":
    main()
