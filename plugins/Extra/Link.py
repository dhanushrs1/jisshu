import asyncio
import secrets
import re
import os
import json
import time
from pyrogram import Client, filters, ContinuePropagation
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from info import ADMINS, REDIRECT_CHANNEL
from utils import list_to_str, get_poster # We will use the PROVEN get_poster function from utils
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
    genre = kwargs.get("genres", "N/A") # Correct key is 'genres' from get_poster
    rating = kwargs.get("rating", "N/A")
    runtime = kwargs.get("runtime", "N/A")

    caption = f"🎬 **{title} ({year})**\n\n"
    if genre and genre != "N/A": caption += f"🎭 **Genre:** {genre}\n"
    if rating and rating != "N/A" and rating != "0": caption += f"⭐ **IMDb Rating:** {rating}/10\n"
    if runtime and runtime != "N/A": caption += f"⏱️ **Runtime:** {runtime}\n"
    caption += "\n📂 **Click the button below to get your files.**"
    return caption

def is_valid_poster_url(url):
    """Check if the poster URL is valid for Telegram"""
    if not url or not isinstance(url, str):
        return False
    
    # Check if it starts with http/https
    if not url.startswith(('http://', 'https://')):
        return False
    
    # Check for common image extensions or known image hosting domains
    image_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp')
    image_domains = ['imdb.com', 'amazon.com', 'tmdb.org', 'imgur.com', 'cloudinary.com']
    
    # Check for file extension
    if any(url.lower().endswith(ext) for ext in image_extensions):
        return True
    
    # Check for known image hosting domains
    if any(domain in url.lower() for domain in image_domains):
        return True
    
    return False

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
    try:
        imdb_data = await get_poster(search_query) or {}
    except Exception as e:
        print(f"Error getting poster data: {e}")
        imdb_data = {
            "title": search_query.title(),
            "year": "N/A",
            "genres": "N/A",
            "rating": "N/A",
            "runtime": "N/A",
            "poster": None
        }
    
    unique_id = secrets.token_hex(4)
    link_id_with_prefix = f"{LINK_ID_PREFIX}{unique_id}"
    
    link_db = load_link_db()
    link_db[link_id_with_prefix] = search_query
    save_link_db(link_db)
    
    permanent_link = f"{REDIRECT_URL}?id={link_id_with_prefix}"
    
    # Ensure required fields have default values
    imdb_data.setdefault("title", search_query.title())
    imdb_data.setdefault("year", "N/A")
    imdb_data.setdefault("genres", "N/A")
    imdb_data.setdefault("rating", "N/A")
    imdb_data.setdefault("runtime", "N/A")
    
    # Validate poster URL
    poster_url = imdb_data.get("poster")
    if not is_valid_poster_url(poster_url):
        poster_url = None
        print(f"Invalid poster URL detected: {imdb_data.get('poster')}")
    
    imdb_data["poster"] = poster_url
    
    caption = generate_caption(**imdb_data)
    
    preview_id = secrets.token_hex(8)
    PREVIEW_CACHE[preview_id] = {
        "poster": poster_url,
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
        # Check if poster is a valid URL
        poster = preview_data.get("poster")
        if poster and is_valid_poster_url(poster):
            try:
                sent_message = await client.send_photo(
                    user_id, 
                    photo=poster, 
                    caption=caption, 
                    reply_markup=markup
                )
                preview_data["preview_message_id"] = sent_message.id
            except Exception as poster_error:
                print(f"Failed to send with poster {poster}: {poster_error}")
                # Fallback to text message without poster
                sent_message = await client.send_message(
                    user_id, 
                    text=f"**🔍 PREVIEW (Poster Error: {str(poster_error)[:50]}...)**\n\n{caption}", 
                    reply_markup=markup, 
                    disable_web_page_preview=True
                )
                preview_data["preview_message_id"] = sent_message.id
                # Clear the invalid poster
                preview_data["poster"] = None
        else:
            # Send as text message when no valid poster
            sent_message = await client.send_message(
                user_id, 
                text=f"**🔍 PREVIEW (No Valid Poster)**\n\n{caption}", 
                reply_markup=markup, 
                disable_web_page_preview=True
            )
            preview_data["preview_message_id"] = sent_message.id
            
    except Exception as e:
        # Ultimate fallback
        try:
            sent_message = await client.send_message(
                user_id, 
                f"**🔍 PREVIEW (Error: {str(e)[:50]}...)**\n\n{caption}", 
                reply_markup=markup, 
                disable_web_page_preview=True
            )
            preview_data["preview_message_id"] = sent_message.id
        except Exception as final_error:
            print(f"Critical error in send_preview: {final_error}")


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
            if poster and is_valid_poster_url(poster):
                try:
                    sent_message = await client.send_photo(
                        REDIRECT_CHANNEL, 
                        photo=poster, 
                        caption=preview_data["caption"], 
                        reply_markup=final_markup
                    )
                except Exception as poster_error:
                    print(f"Failed to post with poster: {poster_error}")
                    # Fallback to text message
                    sent_message = await client.send_message(
                        REDIRECT_CHANNEL, 
                        text=preview_data["caption"], 
                        reply_markup=final_markup, 
                        disable_web_page_preview=True
                    )
            else:
                sent_message = await client.send_message(
                    REDIRECT_CHANNEL, 
                    text=preview_data["caption"], 
                    reply_markup=final_markup, 
                    disable_web_page_preview=True
                )
            
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
        "poster": "🖼️ **Send the new poster URL now.**\n\n**Note:** Make sure the URL is a direct link to an image (jpg, png, etc.) or from a reliable image hosting service.",
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
        poster_url = message.text.strip()
        if is_valid_poster_url(poster_url):
            preview_data["poster"] = poster_url
            preview_data["details"]["poster"] = poster_url
            await message.reply("✅ **Poster updated successfully!**")
        else:
            await message.reply("❌ **Invalid poster URL.** Please send a valid image URL that:\n• Starts with http:// or https://\n• Points to an image file (.jpg, .png, etc.)\n• Is from a reliable hosting service")
            return
    
    elif edit_type == "caption":
        preview_data["caption"] = message.text.strip()
        await message.reply("✅ **Caption updated successfully!**")
        
    elif edit_type == "details":
        try:
            parts = [p.strip() for p in message.text.split("|")]
            if len(parts) != 5:
                await message.reply("❌ **Invalid format.** Please provide exactly 5 fields separated by '|':\n\n`Title | Year | Rating | Genre | Runtime`")
                return

            details = preview_data["details"]
            details["title"], details["year"], details["rating"], details["genres"], details["runtime"] = parts
            
            # Validate year format
            if details["year"] != "N/A" and not re.match(r'^\d{4}, details["year"]):
                await message.reply("⚠️ **Warning:** Year should be a 4-digit number (e.g., 2023) or 'N/A'")
            
            # Validate rating format
            if details["rating"] != "N/A":
                try:
                    rating_float = float(details["rating"])
                    if rating_float < 0 or rating_float > 10:
                        await message.reply("⚠️ **Warning:** Rating should be between 0-10 or 'N/A'")
                except ValueError:
                    await message.reply("⚠️ **Warning:** Rating should be a number (e.g., 8.5) or 'N/A'")
            
            preview_data["caption"] = generate_caption(**details)
            await message.reply("✅ **Movie details and caption updated successfully!**")
        except Exception as e:
            await message.reply(f"❌ **Error updating details:** `{str(e)}`")
            return

    del ADMIN_CONVERSATION_STATE[admin_id]
    
    # Send a status message before generating preview
    status_msg = await message.reply("🔄 **Generating updated preview...**")
    await send_preview(client, admin_id, preview_id)
    
    # Delete the status message after a short delay
    try:
        await asyncio.sleep(1)
        await status_msg.delete()
    except:
        pass

# ==================== /start Command Handler for Permanent Links ====================

@Client.on_message(filters.command("start"), group=1)
async def permanent_link_handler(client, message):
    if len(message.command) > 1 and message.command[1].startswith(LINK_ID_PREFIX):
        link_id = message.command[1]
        link_db = load_link_db()
        search_query = link_db.get(link_id)
        
        if search_query:
            # Create a loading message
            loading_msg = await message.reply("🔍 **Loading your files...**")
            
            mock_message = message
            mock_message.text = search_query
            try:
                # Use a try-except block to handle any errors during auto_filter
                await auto_filter(client, mock_message)
                # Delete loading message after successful filter
                try:
                    await loading_msg.delete()
                except:
                    pass
            except Exception as e:
                print(f"Error in auto_filter from link handler: {e}")
                await loading_msg.edit("❌ **An error occurred while fetching your files.** Please try again later or contact support.")
            return # Stop processing to prevent other handlers from running
        else:
            await message.reply("❌ **Invalid or expired link.** Please get a new link from the channel.")
            return

    # If it wasn't our specific link format, let other start command handlers process it.
    raise ContinuePropagation

# ==================== Additional Admin Commands ====================

@Client.on_message(filters.command("linkstats") & filters.user(ADMINS))
async def link_statistics(client, message):
    """Show statistics about permanent links"""
    link_db = load_link_db()
    total_links = len(link_db)
    
    if total_links == 0:
        return await message.reply("📊 **Link Statistics**\n\n❌ No permanent links created yet.")
    
    # Show recent links (last 10)
    recent_links = list(link_db.items())[-10:]
    
    stats_text = f"📊 **Link Statistics**\n\n"
    stats_text += f"🔗 **Total Links Created:** {total_links}\n\n"
    stats_text += f"🕒 **Recent Links (Last 10):**\n"
    
    for i, (link_id, search_query) in enumerate(recent_links, 1):
        stats_text += f"{i}. `{search_query[:30]}{'...' if len(search_query) > 30 else ''}`\n"
    
    await message.reply(stats_text)

@Client.on_message(filters.command("clearcache") & filters.user(ADMINS))
async def clear_preview_cache(client, message):
    """Clear the preview cache"""
    global PREVIEW_CACHE, ADMIN_CONVERSATION_STATE
    
    cleared_previews = len(PREVIEW_CACHE)
    cleared_states = len(ADMIN_CONVERSATION_STATE)
    
    PREVIEW_CACHE.clear()
    ADMIN_CONVERSATION_STATE.clear()
    
    await message.reply(f"🧹 **Cache Cleared**\n\n✅ Cleared {cleared_previews} preview(s) and {cleared_states} conversation state(s).")

print("✅ Enhanced Permanent Link System with Improved Poster Handling Loaded Successfully!")
