import asyncio
import secrets
import aiohttp
import re
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from info import ADMINS, REDIRECT_CHANNEL, OMDB_API_KEY
from utils import temp
from database.ia_filterdb import get_search_results

# This dictionary will store the state of the conversation for each admin
ADMIN_CONVERSATION_STATE = {}
PREVIEW_CACHE = {}

# --- Self-contained OMDb fetcher for this feature only ---
async def get_omdb_data_for_link(query):
    if not OMDB_API_KEY:
        print("OMDB_API_KEY is not set. Cannot fetch details.")
        return None
    try:
        cleaned_query = re.sub(r'\b(1080p|720p|480p|dvdrip|hdrip|web-dl|bluray)\b', '', query, flags=re.IGNORECASE).strip()
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://www.omdbapi.com/?t={cleaned_query}&apikey={OMDB_API_KEY}") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("Response") == "True":
                        return {"title": data.get("Title"), "year": data.get("Year"), "poster": data.get("Poster")}
    except Exception as e:
        print(f"OMDb Error for /createlink: {e}")
    return None

# --- Helper function to generate and send the preview message ---
async def send_preview(client, user_id, preview_id):
    preview_data = PREVIEW_CACHE[preview_id]
    
    caption = f"**PREVIEW**\n\n{preview_data['caption']}"
    
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm & Post", callback_data=f"confirm_post#{preview_id}")],
        [
            InlineKeyboardButton("🖼️ Edit Poster", callback_data=f"edit_post#poster#{preview_id}"),
            InlineKeyboardButton("✏️ Edit Details", callback_data=f"edit_post#details#{preview_id}")
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_post#{preview_id}")]
    ])
    
    original_message_id = preview_data.get("preview_message_id")

    try:
        chat_id = user_id
        
        if original_message_id:
            original_message = await client.get_messages(chat_id, original_message_id)
            if preview_data["poster"] and preview_data["poster"].startswith("http"):
                await original_message.edit_media(media={"type": "photo", "media": preview_data["poster"], "caption": caption}, reply_markup=markup)
            else:
                await original_message.edit(text=caption, reply_markup=markup, disable_web_page_preview=True)
        else:
            if preview_data["poster"] and preview_data["poster"].startswith("http"):
                sent_msg = await client.send_photo(chat_id, photo=preview_data["poster"], caption=caption, reply_markup=markup)
            else:
                sent_msg = await client.send_message(chat_id, text=f"**PREVIEW (No/Invalid Poster)**\n\n{caption}", reply_markup=markup, disable_web_page_preview=True)
            PREVIEW_CACHE[preview_id]["preview_message_id"] = sent_msg.id
            
    except Exception as e:
        await client.send_message(user_id, f"Error updating preview: `{e}`. The poster URL might be invalid. Please use the edit button to fix it.")

# --- The main command to start the process ---
@Client.on_message(filters.command("createlink") & filters.user(ADMINS))
async def generate_link_command(client, message):
    if REDIRECT_CHANNEL == 0:
        return await message.reply("`REDIRECT_CHANNEL` is not set.")
        
    if len(message.command) < 2:
        return await message.reply("Usage: `/createlink <movie name>`")

    search_query = message.text.split(" ", 1)[1]
    sts = await message.reply("Searching my database for accurate results...")

    files, _, total_results = await get_search_results(search_query, max_results=1)
    if not files:
        return await sts.edit(f"I don't have any files for the query: `{search_query}`. Cannot create a link.")
    
    accurate_name = files[0].file_name
    await sts.edit(f"Found file: `{accurate_name}`. Fetching details...")

    imdb_data = await get_omdb_data_for_link(accurate_name)
    if not imdb_data:
        imdb_data = {}

    bot_username = temp.U_NAME
    start_link = f"https://t.me/{bot_username}?start=getfile-{search_query.replace(' ', '-')}"
    
    preview_id = secrets.token_hex(8)
    PREVIEW_CACHE[preview_id] = {
        "poster": imdb_data.get("poster"),
        "title": imdb_data.get('title', "Not Found - Please Edit"),
        "year": imdb_data.get('year', 'N/A'),
        "start_link": start_link,
        "original_query": search_query,
    }
    PREVIEW_CACHE[preview_id]["caption"] = f"📂 **{PREVIEW_CACHE[preview_id]['title']} ({PREVIEW_CACHE[preview_id]['year']})**\n\nClick the button below to get your files."

    await sts.delete()
    await send_preview(client, message.from_user.id, preview_id)

# --- Callback for the Edit buttons ---
@Client.on_callback_query(filters.regex(r"^edit_post#"))
async def edit_post_callback(client, query):
    if query.from_user.id not in ADMINS: return await query.answer("This is not for you!", show_alert=True)
    
    _, edit_type, preview_id = query.data.split("#")
    
    if preview_id not in PREVIEW_CACHE:
        return await query.message.edit_text("This request has expired.")

    ADMIN_CONVERSATION_STATE[query.from_user.id] = {"type": edit_type, "preview_id": preview_id}
    
    prompt = ""
    if edit_type == "poster":
        prompt = "Please send the new poster URL now."
    elif edit_type == "details":
        prompt = "Please send the new details in the format: `Title | Year`"
        
    await query.message.reply_text(prompt)
    await query.answer()

# --- CORRECTED Message handler to catch the admin's reply ---
@Client.on_message(filters.private & filters.user(ADMINS) & filters.text & ~filters.command(prefixes="/"))
async def handle_admin_input(client, message: Message):
    admin_id = message.from_user.id
    state = ADMIN_CONVERSATION_STATE.get(admin_id)

    if not state:
        return # Not a reply we are waiting for

    preview_id = state["preview_id"]
    edit_type = state["type"]
    
    if edit_type == "poster":
        PREVIEW_CACHE[preview_id]["poster"] = message.text
    elif edit_type == "details":
        try:
            title, year = message.text.split("|")
            PREVIEW_CACHE[preview_id]["title"] = title.strip()
            PREVIEW_CACHE[preview_id]["year"] = year.strip()
            PREVIEW_CACHE[preview_id]["caption"] = f"📂 **{title.strip()} ({year.strip()})**\n\nClick the button below to get your files."
        except:
            await message.reply("Invalid format. Please use `Title | Year`.")
            return

    del ADMIN_CONVERSATION_STATE[admin_id]
    await message.reply("Details updated. Here is the new preview:")
    await send_preview(client, admin_id, preview_id)

# --- Callback for the Confirm and Cancel buttons ---
@Client.on_callback_query(filters.regex(r"^(confirm_post|cancel_post)#"))
async def confirm_cancel_handler(client, query):
    if query.from_user.id not in ADMINS: return await query.answer("This is not for you!", show_alert=True)
        
    action, preview_id = query.data.split("#")
    
    if preview_id not in PREVIEW_CACHE:
        return await query.message.edit_caption("This request has expired or is invalid.")

    if action == "confirm_post":
        preview_data = PREVIEW_CACHE[preview_id]
        await query.message.edit_caption("✅ **Confirmed!** Posting to channel...")
        try:
            final_markup = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Click Here to Get Files ✅", url=preview_data["start_link"])]])
            
            if preview_data["poster"] and preview_data["poster"].startswith("http"):
                sent_message = await client.send_photo(
                    chat_id=REDIRECT_CHANNEL, photo=preview_data["poster"],
                    caption=preview_data["caption"], reply_markup=final_markup
                )
            else:
                sent_message = await client.send_message(
                    chat_id=REDIRECT_CHANNEL, text=preview_data["caption"],
                    reply_markup=final_markup, disable_web_page_preview=True
                )
                
            await query.message.edit_caption(f"**Post created successfully!**\n\n`{sent_message.link}`", reply_markup=None)
        except Exception as e:
            await query.message.edit_caption(f"Error posting: {e}")
        finally:
            if preview_id in PREVIEW_CACHE: del PREVIEW_CACHE[preview_id]
    
    elif action == "cancel_post":
        if preview_id in PREVIEW_CACHE: del PREVIEW_CACHE[preview_id]
        await query.message.delete()
