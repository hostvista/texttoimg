import os
import logging
import sqlite3
import asyncio
import base64
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import requests
from io import BytesIO
from PIL import Image
from uuid import uuid4

# Configuration
ADMIN_ID = 5500026782  # Replace with your Telegram user ID
TOGETHER_API_KEY = "tgp_v1_9Mj45vGmCp1OCbi7V3d96QfBlR2BYmWLUgZzEo9DfFU"
BOT_TOKEN = "7279159630:AAEbKizuZoudyTHSAz7_2L6L-RL7g9tkIbQ"
MAX_CONCURRENT_REQUESTS = 10
FIXED_STEPS = 4
DEFAULT_SIZE = "1024x768"
CREDIT_PER_IMAGE = 0.5  # 1 credit = 2 images

# Database setup
DATABASE = "indie_ai.db"

def init_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    
    # Create users table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            credits REAL DEFAULT 0,
            blocked INTEGER DEFAULT 0,
            images_generated INTEGER DEFAULT 0
        )
    """)
    
    # Create coupons table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS coupons (
            code TEXT PRIMARY KEY,
            credits REAL,
            used INTEGER DEFAULT 0
        )
    """)
    
    conn.commit()
    conn.close()

# Initialize database
init_db()

# Database helper functions
def get_user(user_id):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def add_user(user_id):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
    conn.commit()
    conn.close()

def update_user(user_id, credits=None, blocked=None, images_generated=None):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    if credits is not None:
        cursor.execute("UPDATE users SET credits = ? WHERE user_id = ?", (credits, user_id))
    if blocked is not None:
        cursor.execute("UPDATE users SET blocked = ? WHERE user_id = ?", (blocked, user_id))
    if images_generated is not None:
        cursor.execute("UPDATE users SET images_generated = ? WHERE user_id = ?", (images_generated, user_id))
    conn.commit()
    conn.close()

def get_all_users():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users")
    users = cursor.fetchall()
    conn.close()
    return users

def add_coupon(code, credits):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO coupons (code, credits) VALUES (?, ?)", (code, credits))
    conn.commit()
    conn.close()

def get_coupon(code):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM coupons WHERE code = ?", (code,))
    coupon = cursor.fetchone()
    conn.close()
    return coupon

def mark_coupon_used(code):
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("UPDATE coupons SET used = 1 WHERE code = ?", (code,))
    conn.commit()
    conn.close()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    user = get_user(user_id)
    
    welcome_msg = """
🎨 *Welcome to INDIE AI*
🤩 Your Free High-Quality Text-to-Image Generator! 🚀
💎 Your Credits: {:.1f}

⚙️ *Commands:*
/generate [WxH] <prompt> - Create art
/credits - Check balance
/redeem <code> - Redeem coupon
/help - Show all commands
    """.format(user[1])
    
    if user_id == ADMIN_ID:
        welcome_msg += "\n👑 ADMIN: /admin"
    
    await update.message.reply_text(welcome_msg, parse_mode='Markdown')

async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Unauthorized!")
        return
    
    admin_msg = """
👑 *Admin Panel*

📊 /users - List all users
🔍 /finduser <id> - Find user details
🚫 /block <id> - Block user
🎟️ /createcoupon <credits> - Generate coupon
📈 /stats - System statistics
    """
    await update.message.reply_text(admin_msg, parse_mode='Markdown')

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    
    users = get_all_users()
    user_list = "\n".join(
        f"ID: {u[0]} | Credits: {u[1]} | Blocked: {bool(u[2])} | Images: {u[3]}"
        for u in users
    )
    await update.message.reply_text(f"📊 Users:\n{user_list}")

async def create_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    
    try:
        credits = float(context.args[0])
        code = str(uuid4())[:8].upper()
        add_coupon(code, credits)
        await update.message.reply_text(f"🎟️ New Coupon:\nCode: `{code}`\nValue: {credits} credits", parse_mode='Markdown')
    except:
        await update.message.reply_text("❌ Usage: /createcoupon <credits>")

async def redeem_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    
    try:
        code = context.args[0].upper()
        coupon = get_coupon(code)
        
        if not coupon:
            await update.message.reply_text("❌ Invalid coupon code")
        elif coupon[2]:  # Check if used
            await update.message.reply_text("❌ Coupon already used")
        else:
            user = get_user(user_id)
            new_credits = user[1] + coupon[1]
            update_user(user_id, credits=new_credits)
            mark_coupon_used(code)
            await update.message.reply_text(f"✅ Added {coupon[1]} credits!\nNew Balance: {new_credits:.1f}")
    except:
        await update.message.reply_text("❌ Usage: /redeem <code>")

async def check_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    user = get_user(user_id)
    
    await update.message.reply_text(
        f"💎 Your Credits: {user[1]:.1f}\n" +
        f"🖼️ Images Available: {int(user[1] * 2)}"
    )

async def handle_generation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    user = get_user(user_id)
    
    if user[2]:  # Check if blocked
        await update.message.reply_text("❌ Your account is blocked")
        return
    
    if user[1] < CREDIT_PER_IMAGE:
        await update.message.reply_text(f"❌ Insufficient credits! You need {CREDIT_PER_IMAGE} per image")
        return
    
    # ... [Keep previous generation logic from earlier versions]
    
    # Deduct credits after successful generation
    new_credits = user[1] - CREDIT_PER_IMAGE
    new_images = user[3] + 1
    update_user(user_id, credits=new_credits, images_generated=new_images)
    await update.message.reply_text(f"✅ Image generated! Credits left: {new_credits:.1f}")

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # User commands
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', start))
    application.add_handler(CommandHandler('credits', check_credits))
    application.add_handler(CommandHandler('redeem', redeem_coupon))
    
    # Admin commands
    application.add_handler(CommandHandler('admin', admin_menu))
    application.add_handler(CommandHandler('users', list_users))
    application.add_handler(CommandHandler('createcoupon', create_coupon))
    
    # Generation handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_generation))
    
    application.run_polling()

if __name__ == '__main__':
    main()
