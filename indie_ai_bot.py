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
1. Use the format: `/generate <width>x<height> <prompt>`
   Example: `/generate 1024x768 A futuristic cityscape`
2. Width and height must be multiples of 32
3. Maximum size: 2048x2048
4. Image quality is optimized with 4 steps (fixed)

⚡ System Status: {active}/{max} slots available
    """.format(active=MAX_CONCURRENT_REQUESTS-active_requests, max=MAX_CONCURRENT_REQUESTS)
    
    await update.message.reply_text(welcome_message, parse_mode='Markdown')

async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global active_requests
    
    try:
        # Parse input
        args = context.args
        if len(args) < 2:
            await update.message.reply_text("❌ Please provide dimensions and a prompt.\nExample: /generate 1024x768 A beautiful landscape")
            return
        
        dimensions = args[0].lower().split('x')
        if len(dimensions) != 2:
            await update.message.reply_text("❌ Invalid dimensions format. Use WxH (e.g., 1024x768)")
            return
        
        width = int(dimensions[0])
        height = int(dimensions[1])
        prompt = ' '.join(args[1:])
        
        # Validate dimensions
        if width % 32 != 0 or height % 32 != 0:
            await update.message.reply_text("❌ Dimensions must be multiples of 32")
            return
        if width > 2048 or height > 2048:
            await update.message.reply_text("❌ Maximum size is 2048x2048")
            return
        
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
        
    except ValueError:
        await update.message.reply_text("❌ Invalid dimensions. Please use numbers (e.g., 1024x768)")
    except Exception as e:
        logging.error(f"Error in generate command: {e}")
        await update.message.reply_text("❌ An error occurred. Please try again.")

async def process_request(update: Update, context: ContextTypes.DEFAULT_TYPE, prompt: str, width: int, height: int):
    try:
        typing_task = asyncio.create_task(
            update.message.reply_chat_action(action="upload_photo")
        )
        
        status_msg = await update.message.reply_text(f"🖌️ Creating {width}x{height} masterpiece...")
        
        # Call Together API with fixed steps
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
                
                await update.message.reply_photo(
                    photo=InputFile(png_buffer, filename="artwork.png"),
                    caption=f"🖼️ Your {width}x{height} Indie AI Masterpiece (PNG)"
                )
                await status_msg.delete()
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

async def check_queue():
    while not request_queue.empty() and active_requests < MAX_CONCURRENT_REQUESTS:
        async with processing_lock:
            if active_requests >= MAX_CONCURRENT_REQUESTS:
                return
            active_requests += 1

        item = await request_queue.get()
        update, context, prompt, width, height, wait_msg = item
        
        try:
            await wait_msg.delete()
            await process_request(update, context, prompt, width, height)
        except Exception as e:
            logging.error(f"Error processing queued request: {e}")
        finally:
            request_queue.task_done()

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logging.error(f"Update {update} caused error: {context.error}")
    if update.message:
        await update.message.reply_text("⚠️ An unexpected error occurred. Please try again.")

def main():
    application = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('generate', generate))
    application.add_error_handler(error_handler)

    # Start bot
    application.run_polling()

if __name__ == '__main__':
    main()
