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
    """Fetch movie data from OMDb API with improved error handling"""
    if not OMDB_API_KEY:
        print("OMDB_API_KEY is not set. Cannot fetch details.")
        return None
    
    try:
        # Clean the query by removing quality indicators and common keywords
        cleaned_query = re.sub(
            r'\b(1080p|720p|480p|4k|dvdrip|hdrip|web-dl|bluray|webrip|hdcam|cam|ts)\b', 
            '', query, flags=re.IGNORECASE
        ).strip()
        
        # Remove year patterns like (2023) or [2023]
        cleaned_query = re.sub(r'[\(\[]?\d{4}[\)\]]?', '', cleaned_query).strip()
        
        # Remove file extensions
        cleaned_query = re.sub(r'\.(mkv|mp4|avi|mov|wmv)$', '', cleaned_query, flags=re.IGNORECASE)
        
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(
                f"http://www.omdbapi.com/?t={cleaned_query}&apikey={OMDB_API_KEY}"
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data.get("Response") == "True":
                        return {
                            "title": data.get("Title", "Unknown Title"),
                            "year": data.get("Year", "N/A"),
                            "poster": data.get("Poster") if data.get("Poster") != "N/A" else None,
                            "plot": data.get("Plot", "No plot available"),
                            "rating": data.get("imdbRating", "N/A"),
                            "genre": data.get("Genre", "N/A")
                        }
                    else:
                        print(f"OMDb API returned error: {data.get('Error', 'Unknown error')}")
                else:
                    print(f"OMDb API request failed with status: {resp.status}")
    except asyncio.TimeoutError:
        print("OMDb API request timed out")
    except Exception as e:
        print(f"OMDb Error for /createlink: {e}")
    
    return None

def generate_caption(title, year, plot=None, rating=None, genre=None):
    """Generate a well-formatted caption for the post"""
    caption = f"🎬 **{title} ({year})**\n\n"
    
    if genre and genre != "N/A":
        caption += f"🎭 **Genre:** {genre}\n"
    
    if rating and rating != "N/A":
        caption += f"⭐ **IMDb Rating:** {rating}/10\n"
    
    if plot and plot != "No plot available" and len(plot) < 200:
        caption += f"\n📝 **Plot:** {plot}\n"
    
    caption += "\n📂 **Click the button below to get your files.**"
    return caption

# --- Helper function to generate and send the preview message ---
async def send_preview(client, user_id, preview_id):
    """Send or update the preview message with enhanced formatting"""
    if preview_id not in PREVIEW_CACHE:
        await client.send_message(user_id, "❌ Preview data not found. Please try again.")
        return
        
    preview_data = PREVIEW_CACHE[preview_id]
    
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
    
    original_message_id = preview_data.get("preview_message_id")

    try:
        chat_id = user_id
        
        if original_message_id:
            try:
                original_message = await client.get_messages(chat_id, original_message_id)
                if preview_data["poster"] and preview_data["poster"].startswith("http"):
                    await original_message.edit_media(
                        media={"type": "photo", "media": preview_data["poster"], "caption": caption}, 
                        reply_markup=markup
                    )
                else:
                    await original_message.edit(
                        text=caption, reply_markup=markup, disable_web_page_preview=True
                    )
            except Exception as e:
                print(f"Error editing message: {e}")
                # If editing fails, send a new message
                original_message_id = None
        
        if not original_message_id:
            if preview_data["poster"] and preview_data["poster"].startswith("http"):
                sent_msg = await client.send_photo(
                    chat_id, photo=preview_data["poster"], caption=caption, reply_markup=markup
                )
            else:
                sent_msg = await client.send_message(
                    chat_id, text=f"**🔍 PREVIEW (No/Invalid Poster)**\n\n{caption}", 
                    reply_markup=markup, disable_web_page_preview=True
                )
            PREVIEW_CACHE[preview_id]["preview_message_id"] = sent_msg.id
            
    except Exception as e:
        await client.send_message(
            user_id, 
            f"❌ **Error updating preview:** `{e}`\n\nThe poster URL might be invalid. Please use the edit button to fix it."
        )

# --- The main command to start the process ---
@Client.on_message(filters.command("createlink") & filters.user(ADMINS))
async def generate_link_command(client, message):
    """Enhanced createlink command with better error handling"""
    if REDIRECT_CHANNEL == 0:
        return await message.reply("❌ `REDIRECT_CHANNEL` is not set in your configuration.")
        
    if len(message.command) < 2:
        return await message.reply(
            "ℹ️ **Usage:** `/createlink <movie name>`\n\n"
            "**Example:** `/createlink Avengers Endgame`"
        )

    search_query = message.text.split(" ", 1)[1].strip()
    sts = await message.reply("🔍 **Searching database for accurate results...**")

    try:
        files, _, total_results = await get_search_results(search_query, max_results=1)
        if not files:
            return await sts.edit(
                f"❌ **No files found for:** `{search_query}`\n\n"
                "Cannot create a link without files in the database."
            )
        
        accurate_name = files[0].file_name
        await sts.edit(f"✅ **Found file:** `{accurate_name}`\n\n🎬 **Fetching movie details...**")

        # Get movie data from OMDb
        imdb_data = await get_omdb_data_for_link(accurate_name)
        if not imdb_data:
            imdb_data = {
                "title": search_query.title(),
                "year": "N/A",
                "poster": None,
                "plot": "No plot available",
                "rating": "N/A",
                "genre": "N/A"
            }

        # Generate bot link
        bot_username = temp.U_NAME
        start_link = f"https://t.me/{bot_username}?start=getfile-{search_query.replace(' ', '-')}"
        
        # Create preview data
        preview_id = secrets.token_hex(8)
        caption = generate_caption(
            imdb_data["title"], 
            imdb_data["year"], 
            imdb_data.get("plot"), 
            imdb_data.get("rating"), 
            imdb_data.get("genre")
        )
        
        PREVIEW_CACHE[preview_id] = {
            "poster": imdb_data.get("poster"),
            "title": imdb_data["title"],
            "year": imdb_data["year"],
            "plot": imdb_data.get("plot", "No plot available"),
            "rating": imdb_data.get("rating", "N/A"),
            "genre": imdb_data.get("genre", "N/A"),
            "start_link": start_link,
            "original_query": search_query,
            "caption": caption,
            "admin_id": message.from_user.id
        }

        await sts.delete()
        await send_preview(client, message.from_user.id, preview_id)
        
    except Exception as e:
        await sts.edit(f"❌ **Error occurred:** `{e}`")
        print(f"Error in generate_link_command: {e}")

# --- Callback for the Edit buttons ---
@Client.on_callback_query(filters.regex(r"^edit_post#"))
async def edit_post_callback(client, query):
    """Handle edit button callbacks with validation"""
    if query.from_user.id not in ADMINS: 
        return await query.answer("❌ This is not for you!", show_alert=True)
    
    try:
        _, edit_type, preview_id = query.data.split("#")
    except ValueError:
        return await query.answer("❌ Invalid callback data!", show_alert=True)
    
    if preview_id not in PREVIEW_CACHE:
        return await query.message.edit_text("❌ This request has expired or is invalid.")

    # Check if this admin created this preview
    if PREVIEW_CACHE[preview_id].get("admin_id") != query.from_user.id:
        return await query.answer("❌ You can only edit your own previews!", show_alert=True)

    ADMIN_CONVERSATION_STATE[query.from_user.id] = {"type": edit_type, "preview_id": preview_id}
    
    prompts = {
        "poster": "🖼️ **Please send the new poster URL now.**\n\nSend a direct image URL (jpg, png, etc.)",
        "details": "✏️ **Please send the new details in this format:**\n\n`Title | Year`\n\n**Example:** `Avengers Endgame | 2019`",
        "caption": "📝 **Please send the new caption text.**\n\nThis will replace the entire description."
    }
    
    prompt = prompts.get(edit_type, "Please send your input:")
    await query.message.reply_text(prompt)
    await query.answer()

# --- FIXED: Message handler to catch the admin's reply ---
@Client.on_message(
    filters.private & 
    filters.user(ADMINS) & 
    filters.text & 
    ~filters.command(["start", "help", "createlink"])  # Fixed: specify actual commands
)
async def handle_admin_input(client, message: Message):
    """Handle admin input for editing previews"""
    admin_id = message.from_user.id
    state = ADMIN_CONVERSATION_STATE.get(admin_id)

    if not state:
        return

    preview_id = state["preview_id"]
    edit_type = state["type"]
    
    if preview_id not in PREVIEW_CACHE:
        del ADMIN_CONVERSATION_STATE[admin_id]
        return await message.reply("❌ Preview data expired. Please create a new link.")
    
    try:
        if edit_type == "poster":
            # Validate URL
            if not message.text.startswith(("http://", "https://")):
                return await message.reply("❌ Please send a valid URL starting with http:// or https://")
            
            PREVIEW_CACHE[preview_id]["poster"] = message.text.strip()
            await message.reply("✅ Poster updated!")
            
        elif edit_type == "details":
            try:
                if "|" not in message.text:
                    return await message.reply("❌ Invalid format. Please use `Title | Year`")
                    
                title, year = message.text.split("|", 1)
                title = title.strip()
                year = year.strip()
                
                if not title or not year:
                    return await message.reply("❌ Both title and year are required.")
                
                PREVIEW_CACHE[preview_id]["title"] = title
                PREVIEW_CACHE[preview_id]["year"] = year
                
                # Regenerate caption with new details
                PREVIEW_CACHE[preview_id]["caption"] = generate_caption(
                    title, year,
                    PREVIEW_CACHE[preview_id].get("plot"),
                    PREVIEW_CACHE[preview_id].get("rating"),
                    PREVIEW_CACHE[preview_id].get("genre")
                )
                await message.reply("✅ Title and year updated!")
                
            except Exception as e:
                return await message.reply(f"❌ Error updating details: `{e}`")
                
        elif edit_type == "caption":
            PREVIEW_CACHE[preview_id]["caption"] = message.text.strip()
            await message.reply("✅ Caption updated!")

        # Clean up conversation state
        del ADMIN_CONVERSATION_STATE[admin_id]
        
        # Send updated preview
        await message.reply("🔄 **Generating updated preview...**")
        await send_preview(client, admin_id, preview_id)
        
    except Exception as e:
        await message.reply(f"❌ **Error updating preview:** `{e}`")
        print(f"Error in handle_admin_input: {e}")

# --- Callback for the Confirm and Cancel buttons ---
@Client.on_callback_query(filters.regex(r"^(confirm_post|cancel_post)#"))
async def confirm_cancel_handler(client, query):
    """Handle confirm and cancel callbacks with better error handling"""
    if query.from_user.id not in ADMINS: 
        return await query.answer("❌ This is not for you!", show_alert=True)
        
    try:
        action, preview_id = query.data.split("#")
    except ValueError:
        return await query.answer("❌ Invalid callback data!", show_alert=True)
    
    if preview_id not in PREVIEW_CACHE:
        return await query.message.edit_caption("❌ This request has expired or is invalid.")

    # Check if this admin created this preview
    if PREVIEW_CACHE[preview_id].get("admin_id") != query.from_user.id:
        return await query.answer("❌ You can only manage your own previews!", show_alert=True)

    if action == "confirm_post":
        preview_data = PREVIEW_CACHE[preview_id]
        await query.message.edit_caption("✅ **Confirmed!** Posting to channel...")
        
        try:
            final_markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Click Here to Get Files ✅", url=preview_data["start_link"])]
            ])
            
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
                
            success_message = (
                f"✅ **Post created successfully!**\n\n"
                f"📱 **Channel Link:** `{sent_message.link}`\n"
                f"🆔 **Message ID:** `{sent_message.id}`"
            )
            await query.message.edit_caption(success_message, reply_markup=None)
            
        except Exception as e:
            error_message = f"❌ **Error posting to channel:** `{e}`\n\nPlease check your channel settings and bot permissions."
            await query.message.edit_caption(error_message)
            print(f"Error posting to channel: {e}")
        finally:
            # Clean up
            if preview_id in PREVIEW_CACHE: 
                del PREVIEW_CACHE[preview_id]
            # Clean up any remaining conversation state
            if query.from_user.id in ADMIN_CONVERSATION_STATE:
                del ADMIN_CONVERSATION_STATE[query.from_user.id]
    
    elif action == "cancel_post":
        if preview_id in PREVIEW_CACHE: 
            del PREVIEW_CACHE[preview_id]
        # Clean up conversation state
        if query.from_user.id in ADMIN_CONVERSATION_STATE:
            del ADMIN_CONVERSATION_STATE[query.from_user.id]
        await query.message.delete()
        await query.answer("❌ Preview cancelled and deleted.", show_alert=True)

# --- Cleanup function to prevent memory leaks ---
async def cleanup_expired_data():
    """Clean up expired preview data and conversation states"""
    import time
    current_time = time.time()
    
    # Clean up previews older than 1 hour
    expired_previews = [
        pid for pid, data in PREVIEW_CACHE.items() 
        if current_time - data.get("created_at", current_time) > 3600
    ]
    
    for pid in expired_previews:
        del PREVIEW_CACHE[pid]
    
    # Clean up conversation states older than 30 minutes
    expired_states = [
        uid for uid, state in ADMIN_CONVERSATION_STATE.items() 
        if current_time - state.get("created_at", current_time) > 1800
    ]
    
    for uid in expired_states:
        del ADMIN_CONVERSATION_STATE[uid]

# Add timestamp to preview cache entries
def _add_timestamp_to_preview(preview_id):
    import time
    if preview_id in PREVIEW_CACHE:
        PREVIEW_CACHE[preview_id]["created_at"] = time.time()

def _add_timestamp_to_state(admin_id):
    import time
    if admin_id in ADMIN_CONVERSATION_STATE:
        ADMIN_CONVERSATION_STATE[admin_id]["created_at"] = time.time()
