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
CREDIT_PER_IMAGE = 0.5  # 1 credit = 2 images

# Database setup
DATABASE = "indie_ai.db"

def init_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            credits REAL DEFAULT 0,
            blocked INTEGER DEFAULT 0,
            images_generated INTEGER DEFAULT 0
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS coupons (
            code TEXT PRIMARY KEY,
            credits REAL,
            used INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

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

VALID_DIMENSIONS = [
    "512x512", "768x768", "1024x1024", "1280x1280", "1536x1536", "2048x2048",
    "512x768", "768x1024", "1024x1280", "1280x1536", "1536x2048"
]

LOADING_EMOJIS = ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"]
PROGRESS_BAR_LENGTH = 10

async def show_loading_animation(message, prompt):
    loading_message = await message.reply_text(
        f"🎨 **Creating Artwork**\n\n"
        f"📝 *Prompt:* {prompt}\n\n"
        f"{LOADING_EMOJIS[0]} |{'▱' * PROGRESS_BAR_LENGTH}| 0%\n"
        f"⏳ Estimated time: 15-25 seconds"
    )
    for i in range(1, 9):
        await asyncio.sleep(2)
        progress = min(i * 12, 100)
        bar = "▰" * int(PROGRESS_BAR_LENGTH * i/8) + "▱" * (PROGRESS_BAR_LENGTH - int(PROGRESS_BAR_LENGTH * i/8))
        try:
            await loading_message.edit_text(
                f"🎨 **Creating Artwork**\n\n"
                f"📝 *Prompt:* {prompt}\n\n"
                f"{LOADING_EMOJIS[i % len(LOADING_EMOJIS)]} |{bar}| {progress}%\n"
                f"⏳ Remaining: {25 - i*2} seconds"
            )
        except:
            pass
    return loading_message

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    user = get_user(user_id)
    welcome_msg = (
        "🌟 *Welcome to Indie AI Studio* 🎨\n\n"
        "Transform your imagination into stunning digital art!\n\n"
        "💎 **Your Credits:** {:.1f}\n"
        "🖼️ **Images Available:** {}\n\n"
        "✨ *Quick Start:*\n"
        "1. Type `/generate A magical forest`\n"
        "2. Or specify size: `/generate 1024x768 A space station`\n\n"
        "📚 *Commands:*\n"
        "- /sizes : Show available dimensions\n"
        "- /credits : Check your balance\n"
        "- /redeem : Apply coupon code\n"
        "- /help : Full instructions"
    ).format(user[1], int(user[1] * 2))
    if user_id == ADMIN_ID:
        welcome_msg += "\n\n👑 *Admin Panel:* /admin"
    await update.message.reply_text(welcome_msg, parse_mode='Markdown')

async def list_sizes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sizes_msg = "📏 *Valid Dimensions (Width x Height):*\n" + "\n".join(VALID_DIMENSIONS)
    await update.message.reply_text(sizes_msg, parse_mode='Markdown')

async def check_credits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    user = get_user(user_id)
    await update.message.reply_text(
        f"💎 *Your Credits:* {user[1]:.1f}\n"
        f"🖼️ *Images Available:* {int(user[1] * 2)}")

async def redeem_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    try:
        code = context.args[0].upper()
        coupon = get_coupon(code)
        if not coupon:
            await update.message.reply_text("❌ Invalid coupon code")
        elif coupon[2]:
            await update.message.reply_text("❌ Coupon already used")
        else:
            user = get_user(user_id)
            new_credits = user[1] + coupon[1]
            update_user(user_id, credits=new_credits)
            mark_coupon_used(code)
            await update.message.reply_text(f"✅ Added {coupon[1]} credits!\nNew Balance: {new_credits:.1f}")
    except:
        await update.message.reply_text("❌ Usage: /redeem <code>")

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
📈 /stats - System statistics"""
    await update.message.reply_text(admin_msg, parse_mode='Markdown')

async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    users = get_all_users()
    user_list = "\n".join(
        f"ID: {u[0]} | Credits: {u[1]} | Blocked: {bool(u[2])} | Images: {u[3]}"
        for u in users)
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

async def handle_generation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    add_user(user_id)
    user = get_user(user_id)
    
    if user[2]:  # Check if blocked
        await update.message.reply_text("🔴 *Account Restricted*\n\nYour account has been restricted from generating art.")
        return
    
    if user[1] < CREDIT_PER_IMAGE:
        await update.message.reply_text(
            "💎 *Insufficient Credits*\n\n"
            f"You need {CREDIT_PER_IMAGE} credits to generate an image.\n"
            f"Current balance: {user[1]:.1f} credits\n\n"
            "Use /credits to check your balance\n"
            "Use /redeem to apply a coupon code")
        return
    
    try:
        text = update.message.text.strip()
        
        # Parse dimensions and prompt
        if 'x' in text:
            parts = text.split()
            dimensions = parts[0]
            prompt = ' '.join(parts[1:]) if len(parts) > 1 else ""
            
            # Validate dimensions format
            if 'x' in dimensions:
                width, height = dimensions.split('x')
                if width.isdigit() and height.isdigit():
                    width = int(width)
                    height = int(height)
                    if f"{width}x{height}" in VALID_DIMENSIONS:
                        # Dimensions are valid, proceed with generation
                        pass
                    else:
                        await update.message.reply_text(
                            "⚠️ *Invalid Dimensions*\n\n"
                            "Please choose from these standard sizes:\n"
                            + "\n".join(VALID_DIMENSIONS) +
                            "\n\nExample: `/generate 1024x768 A futuristic city`",
                            parse_mode='Markdown')
                        return
                else:
                    await update.message.reply_text(
                        "⚠️ *Invalid Dimensions*\n\n"
                        "Width and height must be numbers.\n"
                        "Example: `/generate 1024x768 A futuristic city`",
                        parse_mode='Markdown')
                    return
            else:
                await update.message.reply_text(
                    "⚠️ *Invalid Dimensions*\n\n"
                    "Please use the format: `/generate WxH <prompt>`\n"
                    "Example: `/generate 1024x768 A futuristic city`",
                    parse_mode='Markdown')
                return
        else:
            # Default dimensions if none specified
            width, height = 1024, 768
            prompt = text
        
        # Start loading animation
        loading_task = asyncio.create_task(show_loading_animation(update.message, prompt))
        
        # Generate image
        response = requests.post(
            "https://api.together.xyz/v1/images/generations",
            headers={
                "Authorization": f"Bearer {TOGETHER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "stability-ai/sd-turbo",
                "prompt": prompt,
                "width": width,
                "height": height,
                "steps": FIXED_STEPS,
                "n": 1,
                "response_format": "b64_json"
            })
        
        # Cancel loading animation
        loading_message = await loading_task
        await loading_message.delete()

        if response.status_code == 200:
            image_data = response.json().get('data', [{}])[0].get('b64_json', '')
            if image_data:
                # Convert and send image
                image_bytes = base64.b64decode(image_data)
                img = Image.open(BytesIO(image_bytes))
                png_buffer = BytesIO()
                img.save(png_buffer, format='PNG')
                png_buffer.seek(0)
                
                # Create beautiful caption
                caption = (
                    f"🖼️ *Your AI Masterpiece is Ready!*\n\n"
                    f"📝 **Prompt:** {prompt}\n"
                    f"📐 **Dimensions:** {width}x{height}\n"
                    f"💎 **Credits Used:** {CREDIT_PER_IMAGE}\n"
                    f"🏆 **Total Artworks:** {user[3] + 1}\n\n"
                    f"✨ Keep creating with /generate")
                
                await update.message.reply_photo(
                    photo=InputFile(png_buffer, filename="artwork.png"),
                    caption=caption,
                    parse_mode='Markdown')
                
                # Update user credits
                new_credits = user[1] - CREDIT_PER_IMAGE
                new_images = user[3] + 1
                update_user(user_id, credits=new_credits, images_generated=new_images)
            else:
                await update.message.reply_text(
                    "⚠️ *Generation Failed*\n\n"
                    "The AI couldn't create an image for this prompt.\n"
                    "Please try a different description.")
        else:
            await update.message.reply_text(
                "⚠️ *Service Unavailable*\n\n"
                "The AI art generator is currently busy.\n"
                "Please try again in a few minutes.")
    except Exception as e:
        logging.error(f"Error generating image: {e}")
        await update.message.reply_text(
            "⚠️ *Unexpected Error*\n\n"
            "We're experiencing technical difficulties.\n"
            "Our team has been notified. Please try again later.")
        else:
            await update.message.reply_text(
                "⚠️ *Service Unavailable*\n\n"
                "The AI art generator is currently busy.\n"
                "Please try again in a few minutes.")
    except Exception as e:
        logging.error(f"Error generating image: {e}")
        await update.message.reply_text(
            "⚠️ *Unexpected Error*\n\n"
            "We're experiencing technical difficulties.\n"
            "Our team has been notified. Please try again later.")

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log errors and notify user"""
    logging.error(f"Error while handling update {update}: {context.error}")
    if update.message:
        await update.message.reply_text(
            "⚠️ *Oops! Something went wrong*\n\n"
            "Our engineers have been notified about this issue.\n"
            "Please try again later."
        )

def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('generate', handle_generation))
    application.add_handler(CommandHandler('sizes', list_sizes))
    application.add_handler(CommandHandler('credits', check_credits))
    application.add_handler(CommandHandler('redeem', redeem_coupon))
    application.add_handler(CommandHandler('admin', admin_menu))
    application.add_handler(CommandHandler('users', list_users))
    application.add_handler(CommandHandler('createcoupon', create_coupon))
    application.add_error_handler(error_handler)
    application.run_polling()

if __name__ == '__main__':
    main()
