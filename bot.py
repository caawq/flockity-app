"""
Flockity Telegram Bot (aiogram 3.x)
Launches Mini App via inline WebApp button with automatic session creation
"""
import os
import asyncio
import logging
import requests
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton

# ===== CONFIGURATION =====

# 1. Get your BOT TOKEN from @BotFather
#    - Open Telegram and search for @BotFather
#    - Send /newbot and follow instructions
#    - Copy the token below
BOT_TOKEN = "8917265297:AAH5X9585lFgGVtNepFtA6u-JGQbJWBqchs"  # ← Replace with your token from @BotFather

# 2. Set up public HTTPS URL for your Mini App
#    After running Cloudflare Tunnel or ngrok, paste the HTTPS URL here:
#
#    Example using Cloudflare Tunnel:
#      Run: cloudflared tunnel --url http://localhost:8000
#      Output: "https://abc-xyz-123.trycloudflare.com"
#      Paste that URL below (without trailing slash)
#
#    Example using ngrok:
#      Run: ngrok http 8000
#      Output: "Forwarding https://1234-56-78.ngrok-free.app -> localhost:8000"
#      Paste that URL below (without trailing slash)
#
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://fallback.trycloudflare.com")  # ← Replace with YOUR tunnel URL

# Local API endpoint (adjust if backend runs on different port)
API_BASE = os.getenv("API_BASE", "http://localhost:8000")

# ===== SETUP =====

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """
    Handle /start command:
    1. Create new session via API
    2. Send Mini App launch button with session_id in URL
    """
    user_id = str(message.from_user.id)

    try:
        # Create new session (staging area)
        response = requests.post(
            f"{API_BASE}/api/session/new",
            data={"user_id": user_id},
            timeout=10
        )
        response.raise_for_status()
        session_data = response.json()
        session_id = session_data.get("session_id")

        if not session_id:
            raise ValueError("No session_id returned from API")

        # Build Mini App URL with session_id
        webapp_url_with_session = f"{WEBAPP_URL}?session_id={session_id}"

        logging.info(f"✅ Session created: user={user_id}, session={session_id}")

    except Exception as e:
        logging.error(f"❌ Session creation failed: {e}")
        # Fallback: Open Mini App without session_id (will create on frontend)
        webapp_url_with_session = WEBAPP_URL

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎵 Launch Flockity",
                    web_app=WebAppInfo(url=webapp_url_with_session)
                )
            ]
        ]
    )

    await message.answer(
        "🎛️ <b>Welcome to Flockity</b>\n\n"
        "Generate unique audio stems with VINE plugin and FL Studio 2026.\n\n"
        "Tap the button below to start:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


async def main():
    """Start bot polling"""
    print("=" * 60)
    print("🤖 Flockity Telegram Bot Starting...")
    print("=" * 60)
    print(f"Bot Token: {'✅ Set' if BOT_TOKEN != 'YOUR_BOT_TOKEN_HERE' else '❌ NOT SET - Update bot.py'}")
    print(f"WebApp URL: {'✅ ' + WEBAPP_URL if WEBAPP_URL != 'https://your-tunnel-url-here.com' else '❌ NOT SET - Update bot.py'}")
    print("=" * 60)
    print("\n⚠️  BEFORE RUNNING THIS BOT:")
    print("1. Get bot token from @BotFather and update BOT_TOKEN in bot.py")
    print("2. Start your tunnel (cloudflared or ngrok) and update WEBAPP_URL in bot.py")
    print("3. Register Mini App with @BotFather using /newapp")
    print("=" * 60)

    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("\n❌ ERROR: BOT_TOKEN not set! Get it from @BotFather.")
        return

    if WEBAPP_URL == "https://your-tunnel-url-here.com":
        print("\n⚠️  WARNING: WEBAPP_URL not set! The bot will not work correctly.")
        print("   Start cloudflared/ngrok first, then update WEBAPP_URL in bot.py\n")

    print("\n✅ Bot is running. Send /start to your bot in Telegram!\n")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
