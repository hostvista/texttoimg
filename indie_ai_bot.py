import os
import logging
import asyncio
import base64
from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import requests
from io import BytesIO
from PIL import Image

# Configuration (Replace with your actual keys/token)
TOGETHER_API_KEY = "tgp_v1_9Mj45vGmCp1OCbi7V3d96QfBlR2BYmWLUgZzEo9DfFU"  # Replace with your Together API key
BOT_TOKEN = "7279159630:AAEbKizuZoudyTHSAz7_2L6L-RL7g9tkIbQ"      # Replace with your Telegram bot token
MAX_CONCURRENT_REQUESTS = 10                    # Max concurrent requests
QUEUE_CHECK_INTERVAL = 5                        # Queue check interval in seconds
FIXED_STEPS = 4                                 # Fixed steps for image generation
DEFAULT_WIDTH = 1024                            # Default image width
DEFAULT_HEIGHT = 768                            # Default image height

# Global states
active_requests = 0
request_queue = asyncio.Queue()
processing_lock = asyncio.Lock()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_message = """
🎨 *Welcome to Indie AI* 🚀

_Transform your ideas into stunning visual art with AI!_

✨ **How to Use:**
1. Simply type your idea (e.g., `A futuristic cityscape`).
2. The bot will generate a 1024x768 image by default.
3. For custom sizes, use the format: `WxH <prompt>` (e.g., `512x512 A cute puppy`).

⚡ System Status: {active}/{max} slots available
    """.format(active=MAX_CONCURRENT_REQUESTS-active_requests, max=MAX_CONCURRENT_REQUESTS)
    
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_requests
    
    try:
        # Parse input
        text = update.message.text.strip()
        
        # Check if the user specified dimensions
        if 'x' in text and text.split('x')[0].isdigit() and text.split('x')[1].split()[0].isdigit():
            dimensions, *prompt_parts = text.split()
            width, height = map(int, dimensions.split('x'))
            prompt = ' '.join(prompt_parts)
        else:
            width, height = DEFAULT_WIDTH, DEFAULT_HEIGHT
            prompt = text
        
        # Validate dimensions
        if width % 32 != 0 or height % 32 != 0:
            await update.message.reply_text("❌ Dimensions must be multiples of 32. Using default size (1024x768).")
            width, height = DEFAULT_WIDTH, DEFAULT_HEIGHT
        
        if width > 2048 or height > 2048:
            await update.message.reply_text("❌ Maximum size is 2048x2048. Using default size (1024x768).")
            width, height = DEFAULT_WIDTH, DEFAULT_HEIGHT
        
        async with processing_lock:
            if active_requests >= MAX_CONCURRENT_REQUESTS:
                queue_position = request_queue.qsize() + 1
                wait_msg = await update.message.reply_text(
                    f"⏳ Queue Position: #{queue_position}. We'll craft your art soon!"
                )
                await request_queue.put((update, context, prompt, width, height, wait_msg))
                return

            active_requests += 1

        await process_request(update, context, prompt, width, height)
        
    except Exception as e:
        logging.error(f"Error in handle_message: {e}")
        await update.message.reply_text("❌ An error occurred. Please try again.")

async def process_request(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, width: int, height: int):
    try:
        # Show typing action
        typing_task = asyncio.create_task(
            update.message.reply_chat_action(action="upload_photo")
        )
        
        # Show generating message
        status_msg = await update.message.reply_text("🎨 Generating your masterpiece... Please wait!")
        
        # Call Together API
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
                "steps": FIXED_STEPS,  # Fixed at 4 steps
                "n": 1,
                "response_format": "b64_json"
            }
        )

        if response.status_code == 200:
            image_data = response.json().get('data', [{}])[0].get('b64_json', '')
            if image_data:
                # Convert base64 to PNG
                image_bytes = base64.b64decode(image_data)
                img = Image.open(BytesIO(image_bytes))
                
                # Convert to PNG in memory
                png_buffer = BytesIO()
                img.save(png_buffer, format='PNG')
                png_buffer.seek(0)
                
                # Send the image
                await update.message.reply_photo(
                    photo=InputFile(png_buffer, filename="artwork.png"),
                    caption=f"🖼️ *Your Indie AI Masterpiece!* \n\n**Prompt:** {prompt}\n**Size:** {width}x{height}",
                    parse_mode='Markdown'
                )
                await status_msg.edit_text("✅ Done! Enjoy your artwork!")
            else:
                await update.message.reply_text("❌ No image generated. Please try a different prompt.")
        else:
            await update.message.reply_text("⚠️ Creation failed. Please refine your prompt.")
            
    except Exception as e:
        logging.error(f"Error processing request: {e}")
        await update.message.reply_text("❌ An error occurred. Please try again later.")
    finally:
        global active_requests
        async with processing_lock:
            active_requests -= 1
        await check_queue()

# ... [keep the check_queue and error_handler functions same as previous version]

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler('start', start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)

    # Start bot
    application.run_polling()

if __name__ == '__main__':
    main()
