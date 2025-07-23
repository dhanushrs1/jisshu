import asyncio
import secrets
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from info import ADMINS, REDIRECT_CHANNEL
from utils import temp, get_poster

# A temporary dictionary to hold preview data before confirmation
PREVIEW_CACHE = {}

@Client.on_message(filters.command("createlink") & filters.user(ADMINS))
async def generate_link_with_preview(client, message):
    if REDIRECT_CHANNEL == 0:
        return await message.reply("`REDIRECT_CHANNEL` is not set. Please configure it in your info.py or environment variables.")
        
    if len(message.command) < 2:
        return await message.reply("Please provide a search query. Usage: `/createlink <movie name>`")

    search_query = message.text.split(" ", 1)[1]
    
    sts = await message.reply("Fetching details, please wait...")

    # Fetch movie poster and details
    imdb_data = await get_poster(search_query)
    
    if not imdb_data:
        return await sts.edit(f"Could not find any details for '{search_query}'. Please check the spelling.")

    # Prepare the final components
    bot_username = temp.U_NAME
    start_link = f"https://t.me/{bot_username}?start=getfile-{search_query.replace(' ', '-')}"
    final_markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Click Here to Get Files ✅", url=start_link)]]
    )
    caption = f"📂 **{imdb_data.get('title', search_query)} ({imdb_data.get('year', 'N/A')})**\n\nClick the button below to get your files."
    poster_url = imdb_data.get("poster")

    # Generate a unique ID for this preview session
    preview_id = secrets.token_hex(8)
    
    # Store the data in the cache
    PREVIEW_CACHE[preview_id] = {
        "poster": poster_url,
        "caption": caption,
        "final_markup": final_markup,
        "original_query": search_query
    }

    # Prepare confirmation buttons
    confirm_markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Confirm & Post", callback_data=f"confirm_post#{preview_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_post#{preview_id}")
            ]
        ]
    )

    # Send the preview to the admin
    try:
        # **FIX:** Check if poster_url is a valid HTTP link before sending
        if poster_url and poster_url.startswith("http"):
            await message.reply_photo(
                photo=poster_url,
                caption=f"**PREVIEW**\n\n{caption}",
                reply_markup=confirm_markup
            )
        else:
            # Fallback to text if no valid poster is found
            await message.reply_text(
                text=f"**PREVIEW (No Poster Found)**\n\n{caption}",
                reply_markup=confirm_markup,
                disable_web_page_preview=True
            )
        await sts.delete()
    except Exception as e:
        # This will catch errors if the URL is valid but the image is broken
        await sts.edit(f"An error occurred while generating the preview photo. It might be a broken image link.\n\n`{e}`\n\nSending a text-only preview instead.")
        await asyncio.sleep(2)
        await sts.delete()
        await message.reply_text(
                text=f"**PREVIEW (No Poster Found)**\n\n{caption}",
                reply_markup=confirm_markup,
                disable_web_page_preview=True
            )

@Client.on_callback_query(filters.regex(r"^confirm_post#"))
async def confirm_post_handler(client, query):
    if query.from_user.id not in ADMINS:
        return await query.answer("This is not for you!", show_alert=True)
        
    preview_id = query.data.split("#")[1]
    
    preview_data = PREVIEW_CACHE.get(preview_id)
    
    if not preview_data:
        # Check if the message is a photo or text to use the correct edit method
        if query.message.photo:
            return await query.message.edit_caption("**This request has expired or is invalid.**")
        else:
            return await query.message.edit_text("**This request has expired or is invalid.**")

    # Use edit_caption for photos, edit_text for text messages
    if query.message.photo:
        await query.message.edit_caption("**Confirmed!** Posting to the Link Hub channel...")
    else:
        await query.message.edit_text("**Confirmed!** Posting to the Link Hub channel...")


    try:
        # Post the content to the redirect channel
        if preview_data["poster"] and preview_data["poster"].startswith("http"):
            sent_message = await client.send_photo(
                chat_id=REDIRECT_CHANNEL,
                photo=preview_data["poster"],
                caption=preview_data["caption"],
                reply_markup=preview_data["final_markup"]
            )
        else:
            sent_message = await client.send_message(
                chat_id=REDIRECT_CHANNEL,
                text=preview_data["caption"],
                reply_markup=preview_data["final_markup"],
                disable_web_page_preview=True
            )
            
        message_link = sent_message.link
        
        # Edit the preview message with the final link
        final_caption = f"**Post created successfully!**\n\nHere is the permanent link for **'{preview_data['original_query']}'**:\n\n`{message_link}`"
        if query.message.photo:
             await query.message.edit_caption(caption=final_caption, reply_markup=None)
        else:
            await query.message.edit_text(text=final_caption, reply_markup=None)
        
    except Exception as e:
        await query.message.edit_caption(f"An error occurred while posting: {e}")
    finally:
        if preview_id in PREVIEW_CACHE:
            del PREVIEW_CACHE[preview_id]

@Client.on_callback_query(filters.regex(r"^cancel_post#"))
async def cancel_post_handler(client, query):
    if query.from_user.id not in ADMINS:
        return await query.answer("This is not for you!", show_alert=True)
        
    preview_id = query.data.split("#")[1]
    
    if preview_id in PREVIEW_CACHE:
        del PREVIEW_CACHE[preview_id]
        
    await query.message.delete()
