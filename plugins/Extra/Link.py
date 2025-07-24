import asyncio
import secrets
import re
import os
import json
import time
from pyrogram import Client, filters, ContinuePropagation
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from info import ADMINS, REDIRECT_CHANNEL
from utils import list_to_str, get_poster # Using the proven get_poster function
from database.ia_filterdb import get_search_results
from plugins.pm_filter import auto_filter

# --- Configuration ---
REDIRECT_URL = "https://files.hdcinema.fun/"
LINK_DB_FILE = "permanent_links.json"
LINK_ID_PREFIX = "link_"

# --- Globals ---
PREVIEW_CACHE = {}
ADMIN_CONVERSATION_STATE = {}

# --- Link Database Functions ---
def load_link_db():
    if not os.path.exists(LINK_DB_FILE): return {}
    try:
        with open(LINK_DB_FILE, "r") as f: return json.load(f)
    except: return {}

def save_link_db(db_data):
    with open(LINK_DB_FILE, "w") as f:
        json.dump(db_data, f, indent=4)

# ==================== Caption Logic ====================
def generate_caption(**kwargs):
    """Generates a well-formatted caption without the plot, using the correct keys."""
    title = kwargs.get("title", "N/A")
    year = kwargs.get("year", "N/A")
    genre = kwargs.get("genres", "N/A") # The key from get_poster is 'genres'
    rating = kwargs.get("rating", "N/A")
    runtime = kwargs.get("runtime", "N/A")

    caption = f"🎬 **{title} ({year})**\n\n"
    if genre and genre != "N/A": caption += f"🎭 **Genre:** {genre}\n"
    if rating and rating != "N/A" and rating != "0": caption += f"⭐ **IMDb Rating:** {rating}/10\n"
    if runtime and runtime != "N/A": caption += f"⏱️ **Runtime:** {runtime}\n"
    caption += "\n📂 **Click the button below to get your files.**"
    return caption

# ==================== /createlink and Preview Workflow ====================

@Client.on_message(filters.command("createlink") & filters.user(ADMINS))
async def generate_link_command(client, message):
    if len(message.command) < 2:
        return await message.reply("ℹ️ **Usage:** `/createlink <movie name>`")

    search_query = message.text.split(" ", 1)[1].strip()
    sts = await message.reply("🔍 **Searching...**")

    files, _, _ = await get_search_results(search_query, max_results=1)
    if not files:
        return await sts.edit(f"❌ **No files found for:** `{search_query}` in the bot's database.")

    # Use the proven get_poster function for reliable IMDb data
    imdb_data = await get_poster(search_query) or {}
    
    unique_id = secrets.token_hex(4)
    link_id_with_prefix = f"{LINK_ID_PREFIX}{unique_id}"
    
    link_db = load_link_db()
    link_db[link_id_with_prefix] = search_query
    save_link_db(link_db)
    
    permanent_link = f"{REDIRECT_URL}?id={link_id_with_prefix}"
    
    # Set default values if IMDb fetch fails, which is now less likely
    imdb_data.setdefault("title", search_query.title())
    imdb_data.setdefault("year", "N/A")
    
    caption = generate_caption(**imdb_data)
    
    preview_id = secrets.token_hex(8)
    PREVIEW_CACHE[preview_id] = {
        "poster": imdb_data.get("poster"),
        "caption": caption,
        "permanent_link": permanent_link,
        "admin_id": message.from_user.id,
        "details": imdb_data
    }
    
    await sts.delete()
    await send_preview(client, message.from_user.id, preview_id)

async def send_preview(client, user_id, preview_id):
    """Sends or updates the preview message with full admin edit buttons."""
    preview_data = PREVIEW_CACHE.get(preview_id)
    if not preview_data: return

    caption = f"**🔍 PREVIEW**\n\n{preview_data['caption']}"
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm & Post", callback_data=f"confirm_post#{preview_id}")],
        [
            InlineKeyboardButton("🖼️ Edit Poster", callback_data=f"edit_post#poster#{preview_id}"),
            InlineKeyboardButton("✏️ Edit Details", callback_data=f"edit_post#details#{preview_id}")
        ],
        [
            InlineKeyboardButton("📝 Edit Caption", callback_data=f"edit_post#caption#{preview_id}"),
            InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_post#{preview_id}")
        ]
    ])

    # Clean up old preview message if it exists
    if "preview_message_id" in preview_data:
        try:
            await client.delete_messages(user_id, preview_data["preview_message_id"])
        except: pass

    try:
        poster = preview_data.get("poster")
        if poster and isinstance(poster, str) and poster.startswith("http"):
            sent_message = await client.send_photo(user_id, photo=poster, caption=caption, reply_markup=markup)
        else:
            sent_message = await client.send_message(user_id, text=f"**🔍 PREVIEW (No Poster Found)**\n\n{caption}", reply_markup=markup, disable_web_page_preview=True)
        
        preview_data["preview_message_id"] = sent_message.id
    except Exception as e:
        sent_message = await client.send_message(user_id, f"**Could not send preview (Invalid Poster URL):** `{e}`\n\n{caption}", reply_markup=markup, disable_web_page_preview=True)
        preview_data["preview_message_id"] = sent_message.id

@Client.on_callback_query(filters.regex(r"^(confirm_post|cancel_post)#"))
async def confirm_cancel_handler(client, query):
    if query.from_user.id not in ADMINS:
        return await query.answer("This is not for you!", show_alert=True)
    
    action, preview_id = query.data.split("#")
    preview_data = PREVIEW_CACHE.get(preview_id)

    if not preview_data or preview_data["admin_id"] != query.from_user.id:
        return await query.message.edit_text("This request has expired or is invalid.")

    is_photo = bool(query.message.photo)
    edit_func = query.message.edit_caption if is_photo else query.message.edit_text

    if action == "confirm_post":
        await edit_func("✅ **Confirmed!** Posting to channel...")
        
        final_markup = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Click Here to Get Files ✅", url=preview_data["permanent_link"])]])
        
        try:
            poster = preview_data.get("poster")
            if poster and isinstance(poster, str) and poster.startswith("http"):
                sent_message = await client.send_photo(REDIRECT_CHANNEL, photo=poster, caption=preview_data["caption"], reply_markup=final_markup)
            else:
                sent_message = await client.send_message(REDIRECT_CHANNEL, text=preview_data["caption"], reply_markup=final_markup, disable_web_page_preview=True)
            
            await edit_func(f"✅ **Post created successfully!**\n\n📱 **Channel Link:** {sent_message.link}")
        except Exception as e:
            await edit_func(f"❌ **Error posting to channel:** `{e}`")
        finally:
            if preview_id in PREVIEW_CACHE: del PREVIEW_CACHE[preview_id]

    elif action == "cancel_post":
        if preview_id in PREVIEW_CACHE: del PREVIEW_CACHE[preview_id]
        await query.message.delete()
        await query.answer("❌ Preview cancelled.", show_alert=True)

# ==================== Admin Customization Workflow ====================

@Client.on_callback_query(filters.regex(r"^edit_post#"))
async def edit_post_callback(client, query):
    if query.from_user.id not in ADMINS:
        return await query.answer("This is not for you!", show_alert=True)
    
    _, edit_type, preview_id = query.data.split("#")
    
    if preview_id not in PREVIEW_CACHE or PREVIEW_CACHE[preview_id]["admin_id"] != query.from_user.id:
        return await query.message.edit_text("This request has expired or is invalid.")

    ADMIN_CONVERSATION_STATE[query.from_user.id] = {"type": edit_type, "preview_id": preview_id}
    
    prompts = {
        "poster": "🖼️ **Send the new poster URL now.**",
        "details": "✏️ **Send the new details in this format:**\n\n`Title | Year | Rating | Genre | Runtime`\n\n**Example:** `The Avengers | 2012 | 8.0 | Action, Sci-Fi | 143 min`",
        "caption": "📝 **Send the new full caption text.**"
    }
    
    await query.message.reply_text(prompts.get(edit_type, "Please send your input:"))
    await query.answer()

def admin_edit_filter(_, __, message):
    return message.from_user and message.from_user.id in ADMINS and message.from_user.id in ADMIN_CONVERSATION_STATE and not message.text.startswith('/')

@Client.on_message(filters.private & filters.text & filters.create(admin_edit_filter))
async def handle_admin_input(client, message: Message):
    admin_id = message.from_user.id
    state = ADMIN_CONVERSATION_STATE[admin_id]
    preview_id = state["preview_id"]

    if preview_id not in PREVIEW_CACHE:
        del ADMIN_CONVERSATION_STATE[admin_id]
        return await message.reply("❌ Preview data expired. Please create a new link.")
    
    edit_type = state["type"]
    preview_data = PREVIEW_CACHE[preview_id]
    
    if edit_type == "poster":
        if message.text.startswith(("http://", "https://")):
            preview_data["poster"] = message.text.strip()
            await message.reply("✅ Poster updated!")
        else:
            await message.reply("❌ Invalid URL. Please send a valid image link.")
    
    elif edit_type == "caption":
        preview_data["caption"] = message.text.strip()
        await message.reply("✅ Caption updated!")
        
    elif edit_type == "details":
        try:
            parts = [p.strip() for p in message.text.split("|")]
            if len(parts) != 5:
                await message.reply("❌ Invalid format. Please provide all 5 fields separated by '|'.")
                return

            details = preview_data["details"]
            details["title"], details["year"], details["rating"], details["genres"], details["runtime"] = parts
            preview_data["caption"] = generate_caption(**details)
            await message.reply("✅ Movie details and caption updated!")
        except Exception as e:
            await message.reply(f"❌ Error updating details: `{e}`")

    del ADMIN_CONVERSATION_STATE[admin_id]
    await message.reply("🔄 **Generating updated preview...**")
    await send_preview(client, admin_id, preview_id)

# ==================== /start Command Handler for Permanent Links ====================

@Client.on_message(filters.command("start"), group=1)
async def permanent_link_handler(client, message):
    if len(message.command) > 1 and message.command[1].startswith(LINK_ID_PREFIX):
        link_id = message.command[1]
        link_db = load_link_db()
        search_query = link_db.get(link_id)
        
        if search_query:
            mock_message = message
            mock_message.text = search_query
            try:
                await auto_filter(client, mock_message)
            except Exception as e:
                print(f"Error in auto_filter from link handler: {e}")
                await message.reply("An error occurred while fetching your file.")
            # Stop other start handlers from executing for this specific case
            return

    # If it's not our specific link, let other handlers run.
    raise ContinuePropagation

print("✅ Final Link System with Full Admin Customization Loaded Successfully!")
