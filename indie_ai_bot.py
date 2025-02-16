import telegram
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackContext
import requests
import base64
import time
import sys
import sqlite3
import random
import string

# Database setup
db = sqlite3.connect('indie_ai.db', check_same_thread=False)
cursor = db.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, credits INTEGER)''')
cursor.execute('''CREATE TABLE IF NOT EXISTS coupons (code TEXT PRIMARY KEY, credits INTEGER)''')
db.commit()

# Bot token and API key
TOKEN = "7279159630:AAEbKizuZoudyTHSAz7_2L6L-RL7g9tkIbQ"
API_KEY = "tgp_v1_9Mj45vGmCp1OCbi7V3d96QfBlR2BYmWLUgZzEo9DfFU"
ADMIN_ID = 5500026782  # Replace with your Telegram user ID
# Generate loading bar
def display_loading_bar(duration=10):
    for i in range(101):
        time.sleep(duration / 100)
        sys.stdout.write(f'\rGenerating image: [{'#' * (i // 2)}{'-' * (50 - i // 2)}] {i}%')
        sys.stdout.flush()
    print('\nImage generation complete!')

# Command to start the bot
def start(update, context):
    user_id = update.message.from_user.id
    cursor.execute('INSERT OR IGNORE INTO users (user_id, credits) VALUES (?, ?)', (user_id, 0))
    db.commit()
    update.message.reply_text("Welcome to Indie Ai! Use /generate to create images.")

# Generate image
def generate_image(update, context):
    user_id = update.message.from_user.id
    cursor.execute('SELECT credits FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()

    if result and result[0] >= 1:
        prompt = update.message.text.replace('/generate ', '')
        display_loading_bar()
        
        response = requests.post("https://api.together.xyz/v1/images/generations", headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }, json={
            "model": "black-forest-labs/FLUX.1-schnell-Free",
            "prompt": prompt,
            "width": 1024,
            "height": 768,
            "steps": 4,
            "n": 1,
            "response_format": "b64_json"
        })
        
        if response.status_code == 200:
            image_data = response.json()['data'][0]['b64_json']
            image_bytes = base64.b64decode(image_data)
            with open('generated_image.png', 'wb') as f:
                f.write(image_bytes)

            with open('generated_image.png', 'rb') as photo:
                context.bot.send_photo(chat_id=user_id, photo=photo)

            cursor.execute('UPDATE users SET credits = credits - 1 WHERE user_id = ?', (user_id,))
            db.commit()
        else:
            update.message.reply_text("Failed to generate image.")
    else:
        update.message.reply_text("Not enough credits. Use /claim to redeem a coupon.")

# Claim coupon
def claim_coupon(update, context):
    user_id = update.message.from_user.id
    code = update.message.text.replace('/claim ', '')
    cursor.execute('SELECT credits FROM coupons WHERE code = ?', (code,))
    result = cursor.fetchone()
    if result:
        credits = result[0]
        cursor.execute('UPDATE users SET credits = credits + ? WHERE user_id = ?', (credits, user_id))
        cursor.execute('DELETE FROM coupons WHERE code = ?', (code,))
        db.commit()
        update.message.reply_text(f"Coupon redeemed! {credits} credits added.")
    else:
        update.message.reply_text("Invalid coupon code.")

# Admin command to create coupons
def create_coupon(update, context):
    if update.message.from_user.id == ADMIN_ID:
        if context.args:
            try:
                credits = int(context.args[0])
                code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
                cursor.execute('INSERT INTO coupons (code, credits) VALUES (?, ?)', (code, credits))
                db.commit()
                update.message.reply_text(f"Coupon created: {code} for {credits} credits")
            except ValueError:
                update.message.reply_text("Please provide a valid number of credits.")
        else:
            update.message.reply_text("Usage: /createcoupon <credits>")
    else:
        update.message.reply_text("Unauthorized.")

# Main function
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('generate', generate_image))
    app.add_handler(CommandHandler('claim', claim_coupon))
    app.add_handler(CommandHandler('createcoupon', create_coupon))
    
    app.run_polling()

if __name__ == '__main__':
    main()
    

