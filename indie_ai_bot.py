import os
import logging
import base64
from datetime import datetime
from dotenv import load_dotenv
import requests
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
from PIL import Image
from io import BytesIO

# Load environment variables
load_dotenv()

# Configuration
TOGETHER_API_URL = "https://api.together.xyz/v1/images/generations"
MODEL_NAME = "black-forest-labs/FLUX.1-schnell-Free"
STEPS = 1  # Match API sample configuration
WELCOME_MESSAGE = """
🌟 Welcome to INDIE AI Image Generator! 🌟

Transform your ideas into stunning visuals using our AI-powered platform.

How to use:
1. Send /generate
2. Describe your image concept
3. Choose dimensions (multiples of 32)
4. Receive your masterpiece!

Example: "A futuristic cityscape at twilight"
"""

# Conversation states
PROMPT, DIMENSIONS = range(2)

# Initialize logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(WELCOME_MESSAGE)

async def generate_image_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎨 Describe the image you want to create:\n"
        "(Example: 'A futuristic spaceship orbiting a neon planet')"
    )
    return PROMPT

async def receive_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['prompt'] = update.message.text
    await update.message.reply_text(
        "📏 Now enter width and height (both multiples of 32):\n"
        "Example: 1024 768"
    )
    return DIMENSIONS

def validate_dimensions(dimensions):
    try:
        width, height = map(int, dimensions.split())
        if width % 32 != 0 or height % 32 != 0:
            return False, "Dimensions must be multiples of 32"
        if width < 64 or height < 64:
            return False, "Minimum size is 64x64"
        return True, (width, height)
    except:
        return False, "Invalid format. Use: WIDTH HEIGHT"

async def receive_dimensions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    valid, result = validate_dimensions(user_input)
    
    if not valid:
        await update.message.reply_text(f"❌ {result}\nPlease enter valid dimensions:")
        return DIMENSIONS

    width, height = result

    try:
        await update.message.reply_text("🎨 Generating your masterpiece...")
        
        # Format prompt according to API requirements
        prompt_text = f"[{context.user_data['prompt']}]"  # Wrap in square brackets
        
        response = requests.post(
            TOGETHER_API_URL,
            headers={
                "Authorization": f"Bearer {os.getenv('TOGETHER_API_KEY')}",
                "Content-Type": "application/json"
            },
            json={
                "model": MODEL_NAME,
                "prompt": prompt_text,  # Use formatted prompt
                "width": width,
                "height": height,
                "steps": STEPS,
                "n": 1,
                "response_format": "b64_json"
            },
            timeout=30
        )

        if response.status_code == 200:
            data = response.json()
            if 'data' not in data or not data['data']:
                raise ValueError("Empty response from API")
                
            image_data = base64.b64decode(data['data'][0]['b64_json'])
            
            # Convert to RGB mode if necessary and save as PNG
            with Image.open(BytesIO(image_data)) as img:
                if img.mode in ('RGBA', 'LA'):
                    rgb_img = Image.new("RGB", img.size, (255, 255, 255))
                    rgb_img.paste(img, mask=img.split()[-1])
                    img = rgb_img
                
                img_byte_arr = BytesIO()
                img.save(img_byte_arr, format='PNG', quality=95)
                img_byte_arr.seek(0)

            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=img_byte_arr,
                caption=f"✨ Generated: {context.user_data['prompt']}\n"
                       f"Dimensions: {width}x{height}"
            )
        else:
            error_msg = response.text[:500]  # Truncate long error messages
            logging.error(f"API Error {response.status_code}: {error_msg}")
            await update.message.reply_text(
                f"⚠️ Generation failed (Error {response.status_code}). Please try again."
            )

    except Exception as e:
        logging.error(f"Generation error: {str(e)}")
        await update.message.reply_text("⚠️ Failed to generate image. Please check your description and try again.")
    
    context.user_data.clear()
    return ConversationHandler.END

# Cancel handler and main function remain the same

def main():
    application = Application.builder().token(os.getenv("TELEGRAM_TOKEN")).build()

    # Conversation handler for image generation
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('generate', generate_image_start)],
        states={
            PROMPT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_prompt)],
            DIMENSIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_dimensions)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    application.add_handler(conv_handler)
    application.add_handler(CommandHandler("start", start))
    
    application.run_polling()

if __name__ == "__main__":
    main()
