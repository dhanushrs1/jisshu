import asyncio
import secrets
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from info import ADMINS, REDIRECT_CHANNEL
from utils import temp, get_poster
from database.ia_filterdb import get_search_results

PREVIEW_CACHE = {}

async def send_preview(client, message, preview_id, is_edit=False):
    preview_data = PREVIEW_CACHE[preview_id]
    caption = f"**PREVIEW**\n\n{preview_data['caption']}"
    
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm & Post", callback_data=f"confirm_post#{preview_id}")],
        [
            InlineKeyboardButton("🖼️ Change Poster", callback_data=f"edit_post#poster#{preview_id}"),
            InlineKeyboardButton("✏️ Edit Details", callback_data=f"edit_post#details#{preview_id}")
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_post#{preview_id}")]
    ])
    
    try:
        if is_edit:
            if preview_data["poster"] and preview_data["poster"].startswith("http"):
                await message.edit_media(media={"type": "photo", "media": preview_data["poster"], "caption": caption}, reply_markup=markup)
            else:
                 await message.edit_text(text=caption, reply_markup=markup, disable_web_page_preview=True)
        else:
            if preview_data["poster"] and preview_data["poster"].startswith("http"):
                await message.reply_photo(photo=preview_data["poster"], caption=caption, reply_markup=markup)
            else:
                await message.reply_text(text=caption, reply_markup=markup, disable_web_page_preview=True)
    except Exception as e:
        await message.reply(f"Error sending preview: {e}")

@Client.on_message(filters.command("createlink") & filters.user(ADMINS))
async def generate_link_command(client, message):
    if REDIRECT_CHANNEL == 0:
        return await message.reply("`REDIRECT_CHANNEL` is not set.")
        
    if len(message.command) < 2:
        return await message.reply("Usage: `/createlink <movie name>`")

    search_query = message.text.split(" ", 1)[1]
    sts = await message.reply("Searching my database for accurate results...")

    # Step 1: Search own DB for accuracy
    files, _, total_results = await get_search_results(search_query, max_results=1)
    if not files:
        return await sts.edit(f"I don't have any files for the query: `{search_query}`. Cannot create a link.")
    
    # Use the accurate file name for fetching metadata
    accurate_name = files[0].file_name
    await sts.edit(f"Found file: `{accurate_name}`. Fetching details...")

    imdb_data = await get_poster(accurate_name)
    if not imdb_data:
        return await sts.edit(f"Could not find IMDb details for `{accurate_name}`.")

    bot_username = temp.U_NAME
    start_link = f"https://t.me/{bot_username}?start=getfile-{search_query.replace(' ', '-')}"
    
    preview_id = secrets.token_hex(8)
    PREVIEW_CACHE[preview_id] = {
        "poster": imdb_data.get("poster"),
        "title": imdb_data.get('title', search_query),
        "year": imdb_data.get('year', 'N/A'),
        "caption": f"📂 **{imdb_data.get('title', search_query)} ({imdb_data.get('year', 'N/A')})**\n\nClick the button below to get your files.",
        "start_link": start_link,
        "original_query": search_query,
        "state": None # To track conversational state
    }

    await sts.delete()
    await send_preview(client, message, preview_id)

@Client.on_callback_query(filters.regex(r"^edit_post#"))
async def edit_post_callback(client, query):
    if query.from_user.id not in ADMINS: return
    
    _, edit_type, preview_id = query.data.split("#")
    
    if preview_id not in PREVIEW_CACHE:
        return await query.message.edit_text("This request has expired.")

    PREVIEW_CACHE[preview_id]["state"] = edit_type
    
    prompt = ""
    if edit_type == "poster":
        prompt = "Please send the new poster URL."
    elif edit_type == "details":
        prompt = "Please send the new details in the format: `Title | Year`"
        
    await query.message.reply_text(prompt)
    await query.answer()

@Client.on_message(filters.private & filters.user(ADMINS))
async def handle_admin_input(client, message):
    # Check if this message is a reply to one of our prompts
    active_preview_id = None
    for pid, data in PREVIEW_CACHE.items():
        if data.get("state") and message.from_user.id in ADMINS:
            active_preview_id = pid
            break
            
    if not active_preview_id:
        return # Not a reply we are waiting for

    state = PREVIEW_CACHE[active_preview_id]["state"]
    
    if state == "poster":
        PREVIEW_CACHE[active_preview_id]["poster"] = message.text
    elif state == "details":
        try:
            title, year = message.text.split("|")
            PREVIEW_CACHE[active_preview_id]["title"] = title.strip()
            PREVIEW_CACHE[active_preview_id]["year"] = year.strip()
            PREVIEW_CACHE[active_preview_id]["caption"] = f"📂 **{title.strip()} ({year.strip()})**\n\nClick the button below to get your files."
        except:
            await message.reply("Invalid format. Please use `Title | Year`.")
            return

    # Reset state and show updated preview
    PREVIEW_CACHE[active_preview_id]["state"] = None
    await message.reply("Details updated. Here is the new preview:")
    await send_preview(client, message, active_preview_id)

@Client.on_callback_query(filters.regex(r"^confirm_post#"))
async def confirm_post_handler(client, query):
    if query.from_user.id not in ADMINS: return
        
    preview_id = query.data.split("#")[1]
    preview_data = PREVIEW_CACHE.get(preview_id)
    if not preview_data:
        return await query.message.edit_text("This request has expired.")

    await query.message.edit_caption("✅ **Confirmed!** Posting to channel...")

    try:
        final_markup = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Click Here to Get Files ✅", url=preview_data["start_link"])]])
        
        if preview_data["poster"] and preview_data["poster"].startswith("http"):
            sent_message = await client.send_photo(
                chat_id=REDIRECT_CHANNEL,
                photo=preview_data["poster"],
                caption=preview_data["caption"],
                reply_markup=final_markup
            )
        else:
            sent_message = await client.send_message(
                chat_id=REDIRECT_CHANNEL,
                text=preview_data["caption"],
                reply_markup=final_markup,
                disable_web_page_preview=True
            )
            
        await query.message.edit_caption(f"**Post created successfully!**\n\n`{sent_message.link}`", reply_markup=None)
        
    except Exception as e:
        await query.message.edit_caption(f"Error posting: {e}")
    finally:
        if preview_id in PREVIEW_CACHE: del PREVIEW_CACHE[preview_id]

@Client.on_callback_query(filters.regex(r"^cancel_post#"))
async def cancel_post_handler(client, query):
    if query.from_user.id not in ADMINS: return
        
    preview_id = query.data.split("#")[1]
    if preview_id in PREVIEW_CACHE: del PREVIEW_CACHE[preview_id]
        
    await query.message.delete()
