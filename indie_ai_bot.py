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
    CallbackQueryHandler,
    ConversationHandler
)
from dotenv import load_dotenv
import requests
from io import BytesIO
from PIL import Image

# Load environment variables
load_dotenv()

# --- Configuration ---
TOGETHER_API_KEY = os.getenv("TOGETHER_API_KEY")
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID"))
MAX_CONCURRENT_REQUESTS = 10
FIXED_STEPS = 4
CREDITS_PER_IMAGE = 1
DEFAULT_CREDITS = 2

# --- Conversation States ---
SELECTING_SIZE, ENTERING_PROMPT = range(2)

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
        [InlineKeyboardButton("📜 History", callback_data='history')]
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
         InlineKeyboardButton("📊 Statistics", callback_data='admin_stats')]
    ])

# --- Command Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = get_user(update.effective_user.id)
    welcome_msg = f"""
🎨 *Indie AI Image Generator* 🌟

✨ Credits Available: {user[2]} {'🔸' * user[2]}

Choose an option below:
    """
    await update.message.reply_text(
        welcome_msg,
        reply_markup=main_menu_keyboard(),
        parse_mode='Markdown'
    )

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data == 'generate':
        user = get_user(user_id)
        if user[3] == 1:
            await query.message.reply_text("❌ Your account is blocked!")
            return ConversationHandler.END
            
        if user[2] < CREDITS_PER_IMAGE:
            await query.message.reply_text("❌ Insufficient credits! Use /redeem")
            return ConversationHandler.END
            
        await query.message.reply_text(
            "🖼 Choose image size:",
            reply_markup=size_keyboard()
        )
        return SELECTING_SIZE

    elif data in ['512x512', '768x768', '1024x1024']:
        context.user_data['size'] = data
        await query.message.reply_text("📝 Enter your creative prompt:")
        return ENTERING_PROMPT

    elif data == 'custom':
        await query.message.reply_text("Enter custom size (format: WIDTHxHEIGHT)\nExample: 1280x720")
        return SELECTING_SIZE

    elif data == 'credits':
        user = get_user(user_id)
        await query.message.reply_text(f"💰 Your Credits: {user[2]}")
        
    elif data == 'redeem':
        await query.message.reply_text("Enter coupon code using /redeem <CODE>")
        
    return ConversationHandler.END

async def handle_custom_size(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        size = update.message.text.lower()
        width, height = map(int, size.split('x'))
        
        if width % 32 != 0 or height % 32 != 0:
            await update.message.reply_text("❌ Dimensions must be multiples of 32!")
            return SELECTING_SIZE
            
        if width > 2048 or height > 2048:
            await update.message.reply_text("❌ Maximum size is 2048x2048!")
            return SELECTING_SIZE
            
        context.user_data['size'] = f"{width}x{height}"
        await update.message.reply_text("📝 Now enter your prompt:")
        return ENTERING_PROMPT
        
    except:
        await update.message.reply_text("❌ Invalid format! Use WxH (e.g., 1024x768)")
        return SELECTING_SIZE

async def generate_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    prompt = update.message.text
    size = context.user_data.get('size', '512x512')
    
    try:
        width, height = map(int, size.split('x'))
    except:
        await update.message.reply_text("❌ Invalid size configuration")
        return ConversationHandler.END

    user = get_user(user_id)
    
    try:
        # Deduct credits
        c.execute("UPDATE users SET credits = ? WHERE user_id = ?",
                 (user[2] - CREDITS_PER_IMAGE, user_id))
        conn.commit()

        # Show processing status
        processing_msg = await update.message.reply_text("🎨 Painting your masterpiece...")

        # API Request
        response = requests.post(
            "https://api.together.xyz/v1/images/generations",
            headers={
                "Authorization": f"Bearer {TOGETHER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "black-forest-labs/FLUX.1-schnell-Free",
                "prompt": prompt,
                "width": width,
                "height": height,
                "steps": FIXED_STEPS,
                "n": 1,
                "response_format": "b64_json"
            },
            timeout=30
        )

        if response.status_code == 200:
            image_data = response.json()['data'][0]['b64_json']
            image_bytes = base64.b64decode(image_data)
            
            # Convert to PNG
            img = Image.open(BytesIO(image_bytes))
            png_buffer = BytesIO()
            img.save(png_buffer, format='PNG')
            png_buffer.seek(0)
            
            # Send image
            await update.message.reply_photo(
                photo=InputFile(png_buffer, filename="artwork.png"),
                caption=f"🖼 {width}x{height} | Credits left: {user[2]-1}"
            )
            
            # Save to history
            c.execute("INSERT INTO images (user_id, prompt, dimensions) VALUES (?, ?, ?)",
                     (user_id, prompt, f"{width}x{height}"))
            conn.commit()
            
            await processing_msg.delete()

        else:
            await update.message.reply_text("⚠️ Image creation failed. Credit refunded!")
            # Refund credits
            c.execute("UPDATE users SET credits = ? WHERE user_id = ?",
                     (user[2], user_id))
            conn.commit()

    except Exception as e:
        logging.error(f"Generation error: {str(e)}")
        await update.message.reply_text("❌ Generation failed. Credit refunded!")
        # Refund credits
        c.execute("UPDATE users SET credits = ? WHERE user_id = ?",
                 (user[2], user_id))
        conn.commit()

    return ConversationHandler.END

# --- Admin Commands ---
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Command not found!")
        return
    
    await update.message.reply_text(
        "🔒 Admin Panel",
        reply_markup=admin_keyboard()
    )

async def create_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    try:
        value = int(context.args[0])
        days = int(context.args[1])
        uses = int(context.args[2])
    except:
        await update.message.reply_text("Usage: /createcoupon <CREDITS> <DAYS_VALID> <MAX_USES>")
        return
    
    code = create_coupon_code()
    expires = datetime.now() + timedelta(days=days)
    
    c.execute("INSERT INTO coupons VALUES (?, ?, ?, ?)",
             (code, value, expires, uses))
    conn.commit()
    
    await update.message.reply_text(
        f"🎫 Coupon Created\n"
        f"Code: `{code}`\n"
        f"Value: {value} credits\n"
        f"Expires: {expires.strftime('%Y-%m-%d')}\n"
        f"Uses Left: {uses}",
        parse_mode='Markdown'
    )

async def block_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    try:
        target_id = int(context.args[0])
        c.execute("UPDATE users SET blocked=1 WHERE user_id=?", (target_id,))
        conn.commit()
        await update.message.reply_text(f"✅ User {target_id} blocked")
    except:
        await update.message.reply_text("Usage: /block <USER_ID>")

# --- User Commands ---
async def redeem_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    try:
        code = context.args[0].upper().strip()
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
        await update.message.reply_text("❌ No uses left")
        return
        
    # Update coupon
    c.execute("UPDATE coupons SET uses_left = ? WHERE code = ?",
             (coupon[3]-1, code))
    
    # Update user
    new_credits = user[2] + coupon[1]
    c.execute("UPDATE users SET credits = ? WHERE user_id = ?",
             (new_credits, user_id))
    conn.commit()
    
    await update.message.reply_text(
        f"🎉 Added {coupon[1]} credits!\n"
        f"New balance: {new_credits}"
    )

# --- Main Application ---
def main():
    # Validate environment variables
    required_vars = ['TOGETHER_API_KEY', 'BOT_TOKEN', 'ADMIN_USER_ID']
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        logging.error(f"Missing environment variables: {', '.join(missing)}")
        return

    application = Application.builder().token(BOT_TOKEN).build()

    # Conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_callback)],
        states={
            SELECTING_SIZE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_custom_size),
                CallbackQueryHandler(handle_callback)
            ],
            ENTERING_PROMPT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, generate_image)
            ]
        },
        fallbacks=[CommandHandler('cancel', lambda u,c: ConversationHandler.END)]
    )

    # Add handlers
    application.add_handler(conv_handler)
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('admin', admin_panel))
    application.add_handler(CommandHandler('block', block_user))
    application.add_handler(CommandHandler('redeem', redeem_coupon))
    application.add_handler(CommandHandler('createcoupon', create_coupon))

    # Start bot
    application.run_polling()

if __name__ == '__main__':
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    main()
