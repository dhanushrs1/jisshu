import sys
import glob
import importlib
import asyncio
from pathlib import Path
from pyrogram import idle, __version__
from pyrogram.raw.all import layer
import logging
import logging.config
from aiohttp import web
from datetime import date, datetime
import pytz

# Get logging configurations
logging.config.fileConfig("logging.conf")
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("imdbpy").setLevel(logging.ERROR)

from database.ia_filterdb import Media
from database.users_chats_db import db
from info import *
from utils import temp
from Script import script
from plugins import web_server, check_expired_premium
from Jisshu.bot import JisshuBot
from Jisshu.util.keepalive import ping_server
from Jisshu.bot.clients import initialize_clients

# --- Main Asynchronous Function ---
async def main():
    """
    The main function to start the bot and all its components.
    """
    logging.info("Starting Jisshu Filter Bot...")
    
    # Start the main bot client
    await JisshuBot.start()
    bot_info = await JisshuBot.get_me()
    temp.ME = bot_info.id
    temp.U_NAME = bot_info.username
    temp.B_NAME = bot_info.first_name
    temp.B_LINK = bot_info.mention
    JisshuBot.username = f"@{bot_info.username}"

    # Initialize other clients if available
    await initialize_clients()

    # Import plugins
    ppath = "plugins/*.py"
    files = glob.glob(ppath)
    for name in files:
        with open(name) as a:
            patt = Path(a.name)
            plugin_name = patt.stem
            try:
                import_path = f"plugins.{plugin_name}"
                spec = importlib.util.spec_from_file_location(import_path, Path(f"plugins/{plugin_name}.py"))
                load = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(load)
                sys.modules[import_path] = load
                logging.info(f"Imported Plugin: {plugin_name}")
            except Exception as e:
                logging.error(f"Failed to import plugin {plugin_name}: {e}")

    # Start background tasks
    if ON_HEROKU or URL: # Keepalive ping is useful for any web service
        asyncio.create_task(ping_server())

    b_users, b_chats = await db.get_banned()
    temp.BANNED_USERS = b_users
    temp.BANNED_CHATS = b_chats
    
    await Media.ensure_indexes()

    # Start the premium expiry checker
    asyncio.create_task(check_expired_premium(JisshuBot))

    # Log bot start
    logging.info(f"{bot_info.first_name} with Pyrogram v{__version__} (Layer {layer}) started on {bot_info.username}.")
    logging.info(script.LOGO)
    
    tz = pytz.timezone("Asia/Kolkata")
    today = date.today()
    now = datetime.now(tz)
    time_str = now.strftime("%H:%M:%S %p")
    
    try:
        await JisshuBot.send_message(chat_id=LOG_CHANNEL, text=script.RESTART_TXT.format(bot_info.mention, today, time_str))
        if SUPPORT_GROUP:
            await JisshuBot.send_message(chat_id=SUPPORT_GROUP, text=f"<b>{bot_info.mention} is now online! 🤖</b>")
    except Exception as e:
        logging.warning(f"Could not send startup message to log/support channel: {e}")

    # Start the web server
    app = web.AppRunner(await web_server())
    await app.setup()
    # Use the PORT provided by the environment, default to 8080 if not set
    # Railway provides the PORT env var automatically.
    bind_address = "0.0.0.0"
    port = int(environ.get("PORT", 8080))
    await web.TCPSite(app, bind_address, port).start()
    logging.info(f"Web server started on {bind_address}:{port}")

    # Keep the bot running
    await idle()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Service stopped by user.")
    except Exception as e:
        logging.error(f"An error occurred during startup: {e}", exc_info=True)
