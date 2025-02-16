import os
import logging
import asyncio
import base64
import sqlite3
from datetime import datetime, timedelta
from telegram import Update, InputFile, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler
)
import requests
from io import BytesIO
from PIL import Image

# --- Configuration ---
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))  # Your Telegram ID
MAX_CONCURRENT_REQUESTS = 10
FIXED_STEPS = 4
CREDITS_PER_IMAGE = 1
DEFAULT_CREDITS = 2  # Initial credits for new users

# --- Database Setup ---
conn = sqlite3.connect('users.db', check_same_thread=False)
c = conn.cursor()

# Create tables
c.execute('''CREATE TABLE IF NOT EXISTS users
             (user_id INTEGER PRIMARY KEY, 
              username TEXT,
              credits INTEGER DEFAULT 0,
              blocked INTEGER DEFAULT 0,
              joined DATETIME DEFAULT CURRENT_TIMESTAMP)''')

c.execute('''CREATE TABLE IF NOT EXISTS coupons
             (code TEXT PRIMARY KEY,
              value INTEGER,
              expires DATETIME,
              uses_left INTEGER)''')

c.execute('''CREATE TABLE IF NOT EXISTS images
             (id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER,
              prompt TEXT,
              dimensions TEXT,
              created_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')
conn.commit()

# --- Helper Functions ---
def get_user(user_id):
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    if not user:
        c.execute("INSERT INTO users (user_id, credits) VALUES (?, ?)", 
                 (user_id, DEFAULT_CREDITS))
        conn.commit()
        return (user_id, None, DEFAULT_CREDITS, 0, datetime.now())
    return user

def is_admin(user_id):
    return user_id == ADMIN_USER_ID

def create_coupon_code():
    return base64.b32encode(os.urandom(5)).decode().strip('=')

# --- Keyboard Layouts ---
def main_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🖼 Generate Art", callback_data='generate')],
        [InlineKeyboardButton("💰 My Credits", callback_data='credits'),
         InlineKeyboardButton("🎫 Redeem Coupon", callback_data='redeem')],
        [InlineKeyboardButton("📜 History", callback_data='history'),
         InlineKeyboardButton("❓ Help", callback_data='help')]
    ])

def size_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("512x512", callback_data='512x512'),
         InlineKeyboardButton("768x768", callback_data='768x768')],
        [InlineKeyboardButton("1024x1024", callback_data='1024x1024'),
         InlineKeyboardButton("Custom Size", callback_data='custom')]
    ])

def admin_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 All Users", callback_data='admin_users'),
         InlineKeyboardButton("🚫 Blocked Users", callback_data='admin_blocked')],
        [InlineKeyboardButton("🎫 Create Coupon", callback_data='admin_create_coupon'),
         InlineKeyboardButton("📊 Statistics", callback_data='admin_stats')],
        [InlineKeyboardButton("🔧 Bot Settings", callback_data='admin_settings')]
    ])

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    welcome_msg = f"""
🎨 *Welcome to Indie AI* 🌟

✨ *Your Credits*: {user[2]} {'🔸' * user[2]}

Choose an option below:
    """
    await update.message.reply_text(
        welcome_msg,
        reply_markup=main_menu_keyboard(),
        parse_mode='Markdown'
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data

    if data == 'generate':
        user = get_user(user_id)
        if user[3] == 1:
            await query.answer("❌ You are blocked from using the bot!")
            return
            
        if user[2] < CREDITS_PER_IMAGE:
            await query.answer("❌ Insufficient credits! Redeem coupons first.")
            return
            
        await query.message.reply_text(
            "🖼 Choose your canvas size:",
            reply_markup=size_keyboard()
        )
    
    elif data == 'credits':
        user = get_user(user_id)
        await query.message.reply_text(
            f"💰 *Your Credits*: {user[2]}\n"
            f"🔑 *Premium Features*: /subscribe\n"
            f"🎫 *Redeem Coupon*: /redeem"
        )
    
    # Add other callback handlers as needed

# --- Image Generation Flow ---
async def handle_size_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    size = query.data
    
    if size == 'custom':
        await query.message.reply_text("Enter custom size (e.g., 1024x768):")
        return
    
    context.user_data['size'] = size
    await query.message.reply_text("📝 Now send me your creative prompt:")

async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    prompt = update.message.text
    size = context.user_data.get('size', '512x512')
    
    try:
        width, height = map(int, size.split('x'))
    except:
        await update.message.reply_text("❌ Invalid size format!")
        return
    
    # Deduct credits
    user = get_user(user_id)
    c.execute("UPDATE users SET credits = ? WHERE user_id = ?",
              (user[2] - CREDITS_PER_IMAGE, user_id))
    conn.commit()
    
    # Generate image
    try:
        response = requests.post(
            "https://api.together.xyz/v1/images/generations",
            headers={"Authorization": f"Bearer {TOGETHER_API_KEY}"},
            json={
                "model": "black-forest-labs/FLUX.1-schnell-Free",
                "prompt": prompt,
                "width": width,
                "height": height,
                "steps": FIXED_STEPS,
                "n": 1,
                "response_format": "b64_json"
            }
        )

        if response.status_code == 200:
            image_data = response.json()['data'][0]['b64_json']
            image_bytes = base64.b64decode(image_data)
            img = Image.open(BytesIO(image_bytes))
            
            png_buffer = BytesIO()
            img.save(png_buffer, format='PNG')
            png_buffer.seek(0)
            
            await update.message.reply_photo(
                photo=InputFile(png_buffer, filename="artwork.png"),
                caption=f"🖼 {width}x{height} | Credits left: {user[2]-1}"
            )
            
            # Store generation history
            c.execute("INSERT INTO images (user_id, prompt, dimensions) VALUES (?, ?, ?)",
                     (user_id, prompt, f"{width}x{height}"))
            conn.commit()
            
        else:
            await update.message.reply_text("⚠️ Generation failed. Credit not deducted.")
            # Refund credit
            c.execute("UPDATE users SET credits = ? WHERE user_id = ?",
                     (user[2], user_id))
            conn.commit()

    except Exception as e:
        logging.error(f"Generation error: {e}")
        await update.message.reply_text("❌ Generation failed. Please try again.")

# --- Admin Commands ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Command not found!")
        return
    
    await update.message.reply_text(
        f"🔒 *Admin Panel* - Owner Only\n",
        reply_markup=admin_keyboard(),
        parse_mode='Markdown'
    )

async def create_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    try:
        value = int(context.args[0])
        days = int(context.args[1])
        uses = int(context.args[2])
    except:
        await update.message.reply_text("Usage: /createcoupon <value> <days_valid> <uses>")
        return
    
    code = create_coupon_code()
    expires = datetime.now() + timedelta(days=days)
    
    c.execute("INSERT INTO coupons VALUES (?, ?, ?, ?)",
             (code, value, expires, uses))
    conn.commit()
    
    await update.message.reply_text(
        f"🎫 New coupon created!\n"
        f"Code: `{code}`\n"
        f"Value: {value} credits\n"
        f"Expires: {expires.strftime('%Y-%m-%d')}\n"
        f"Max uses: {uses}",
        parse_mode='Markdown'
    )

async def block_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    try:
        target_id = int(context.args[0])
        c.execute("UPDATE users SET blocked=1 WHERE user_id=?", (target_id,))
        conn.commit()
        await update.message.reply_text(f"User {target_id} blocked ✅")
    except:
        await update.message.reply_text("Usage: /block <user_id>")

# --- User Commands ---
async def redeem_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    try:
        code = context.args[0].upper()
    except:
        await update.message.reply_text("Usage: /redeem <COUPON_CODE>")
        return
    
    c.execute("SELECT * FROM coupons WHERE code=?", (code,))
    coupon = c.fetchone()
    
    if not coupon:
        await update.message.reply_text("❌ Invalid coupon code")
        return
        
    if datetime.now() > datetime.strptime(coupon[2], "%Y-%m-%d %H:%M:%S.%f"):
        await update.message.reply_text("❌ Coupon expired")
        return
        
    if coupon[3] <= 0:
        await update.message.reply_text("❌ Coupon uses exhausted")
        return
        
    # Update coupon uses
    c.execute("UPDATE coupons SET uses_left = ? WHERE code = ?",
             (coupon[3]-1, code))
    
    # Add credits
    new_credits = user[2] + coupon[1]
    c.execute("UPDATE users SET credits = ? WHERE user_id = ?",
             (new_credits, user_id))
    conn.commit()
    
    await update.message.reply_text(
        f"🎉 {coupon[1]} credits added!\n"
        f"New balance: {new_credits} credits"
    )

# --- Main Application ---
def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('admin', admin_panel))
    application.add_handler(CommandHandler('block', block_user))
    application.add_handler(CommandHandler('redeem', redeem_coupon))
    application.add_handler(CommandHandler('createcoupon', create_coupon))
    
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, generate_image))

    # Start bot
    application.run_polling()

if __name__ == '__main__':
    # Set up logging
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    main()
