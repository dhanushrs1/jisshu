import asyncio
import secrets
import aiohttp
import re
import logging
from typing import Dict, Optional, Tuple, Any
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from pyrogram.errors import MessageNotModified, MessageDeleteForbidden, BadRequest
from info import ADMINS, REDIRECT_CHANNEL, OMDB_API_KEY
from utils import temp
from database.ia_filterdb import get_search_results

# Set up logging
logger = logging.getLogger(__name__)

# Constants
MAX_CAPTION_LENGTH = 1024
PREVIEW_EXPIRY_TIME = 3600  # 1 hour in seconds
POSTER_URL_PATTERN = re.compile(r'^https?://.+\.(jpg|jpeg|png|gif|webp)(\?.*)?$', re.IGNORECASE)
QUALITY_PATTERN = re.compile(r'\b(1080p|720p|480p|4k|uhd|hd|dvdrip|hdrip|web-dl|bluray|cam|ts|tc)\b', re.IGNORECASE)

# Global state dictionaries with type hints
ADMIN_CONVERSATION_STATE: Dict[int, Dict[str, Any]] = {}
PREVIEW_CACHE: Dict[str, Dict[str, Any]] = {}

class OMDbError(Exception):
    """Custom exception for OMDb API errors"""
    pass

class ValidationError(Exception):
    """Custom exception for validation errors"""
    pass

async def clean_expired_previews():
    """Clean up expired preview cache entries"""
    import time
    current_time = time.time()
    expired_keys = [
        key for key, data in PREVIEW_CACHE.items()
        if current_time - data.get('created_at', 0) > PREVIEW_EXPIRY_TIME
    ]
    for key in expired_keys:
        del PREVIEW_CACHE[key]
        logger.info(f"Cleaned expired preview: {key}")

async def get_omdb_data_for_link(query: str) -> Optional[Dict[str, str]]:
    """
    Fetch movie data from OMDb API with improved error handling and query cleaning
    
    Args:
        query: The movie name to search for
        
    Returns:
        Dictionary with title, year, and poster URL, or None if not found
    """
    if not OMDB_API_KEY:
        logger.warning("OMDB_API_KEY is not set. Cannot fetch details.")
        return None
    
    try:
        # Clean the query more thoroughly
        cleaned_query = QUALITY_PATTERN.sub('', query).strip()
        # Remove common file extensions and brackets
        cleaned_query = re.sub(r'\[[^\]]*\]|\([^)]*\)|\{[^}]*\}', '', cleaned_query)
        cleaned_query = re.sub(r'\.(mkv|mp4|avi|mov|wmv|flv|webm)$', '', cleaned_query, flags=re.IGNORECASE)
        cleaned_query = re.sub(r'\s+', ' ', cleaned_query).strip()
        
        if not cleaned_query:
            logger.warning(f"Query became empty after cleaning: {query}")
            return None
        
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            url = f"http://www.omdbapi.com/?t={cleaned_query}&apikey={OMDB_API_KEY}"
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise OMDbError(f"HTTP {resp.status}: {await resp.text()}")
                
                data = await resp.json()
                
                if data.get("Response") != "True":
                    error_msg = data.get("Error", "Unknown error")
                    logger.info(f"OMDb API error for '{cleaned_query}': {error_msg}")
                    return None
                
                # Validate poster URL
                poster_url = data.get("Poster")
                if poster_url and poster_url != "N/A" and not POSTER_URL_PATTERN.match(poster_url):
                    logger.warning(f"Invalid poster URL format: {poster_url}")
                    poster_url = None
                
                return {
                    "title": data.get("Title", "Unknown Title"),
                    "year": data.get("Year", "N/A"),
                    "poster": poster_url if poster_url != "N/A" else None,
                    "plot": data.get("Plot", "No plot available")[:200] + "..." if len(data.get("Plot", "")) > 200 else data.get("Plot", ""),
                    "genre": data.get("Genre", "N/A"),
                    "imdb_rating": data.get("imdbRating", "N/A")
                }
                
    except asyncio.TimeoutError:
        logger.error(f"OMDb API timeout for query: {query}")
    except OMDbError as e:
        logger.error(f"OMDb API error for '{query}': {e}")
    except Exception as e:
        logger.error(f"Unexpected error fetching OMDb data for '{query}': {e}")
    
    return None

def validate_poster_url(url: str) -> bool:
    """Validate if the URL is a valid image URL"""
    if not url or not isinstance(url, str):
        return False
    return bool(POSTER_URL_PATTERN.match(url.strip()))

def create_caption(data: Dict[str, Any]) -> str:
    """Create a formatted caption from movie data"""
    title = data.get('title', 'Unknown Title')
    year = data.get('year', 'N/A')
    genre = data.get('genre', '')
    rating = data.get('imdb_rating', '')
    plot = data.get('plot', '')
    
    caption = f"🎬 **{title}**"
    if year != 'N/A':
        caption += f" ({year})"
    caption += "\n\n"
    
    if genre and genre != 'N/A':
        caption += f"🎭 **Genre:** {genre}\n"
    if rating and rating != 'N/A':
        caption += f"⭐ **IMDb Rating:** {rating}/10\n"
    if plot:
        caption += f"\n📖 **Plot:** {plot}\n"
    
    caption += "\n📥 Click the button below to get your files."
    
    # Ensure caption doesn't exceed Telegram's limit
    if len(caption) > MAX_CAPTION_LENGTH:
        caption = caption[:MAX_CAPTION_LENGTH-3] + "..."
    
    return caption

async def send_preview(client: Client, user_id: int, preview_id: str) -> bool:
    """
    Send or update the preview message
    
    Returns:
        True if successful, False otherwise
    """
    if preview_id not in PREVIEW_CACHE:
        logger.error(f"Preview ID {preview_id} not found in cache")
        return False
    
    preview_data = PREVIEW_CACHE[preview_id]
    caption = f"**🔍 PREVIEW**\n\n{preview_data['caption']}"
    
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
        if original_message_id:
            try:
                original_message = await client.get_messages(user_id, original_message_id)
                
                if preview_data.get("poster") and validate_poster_url(preview_data["poster"]):
                    await original_message.edit_media(
                        media={"type": "photo", "media": preview_data["poster"], "caption": caption},
                        reply_markup=markup
                    )
                else:
                    await original_message.edit_text(
                        text=f"**🔍 PREVIEW (No/Invalid Poster)**\n\n{caption}",
                        reply_markup=markup,
                        disable_web_page_preview=True
                    )
            except MessageNotModified:
                logger.info("Message content unchanged, skipping edit")
                return True
            except Exception as e:
                logger.error(f"Error editing existing message: {e}")
                # Fall back to sending new message
                original_message_id = None
        
        if not original_message_id:
            if preview_data.get("poster") and validate_poster_url(preview_data["poster"]):
                sent_msg = await client.send_photo(
                    user_id, 
                    photo=preview_data["poster"], 
                    caption=caption, 
                    reply_markup=markup
                )
            else:
                sent_msg = await client.send_message(
                    user_id, 
                    text=f"**🔍 PREVIEW (No/Invalid Poster)**\n\n{caption}",
                    reply_markup=markup, 
                    disable_web_page_preview=True
                )
            PREVIEW_CACHE[preview_id]["preview_message_id"] = sent_msg.id
            
        return True
        
    except Exception as e:
        logger.error(f"Error sending preview to {user_id}: {e}")
        try:
            await client.send_message(
                user_id, 
                f"❌ Error updating preview: `{e}`\n\nThe poster URL might be invalid. Please use the edit button to fix it."
            )
        except Exception as send_error:
            logger.error(f"Error sending error message: {send_error}")
        return False

@Client.on_message(filters.command("createlink") & filters.user(ADMINS))
async def generate_link_command(client: Client, message: Message):
    """Main command to start the link creation process"""
    
    # Clean expired previews periodically
    await clean_expired_previews()
    
    if REDIRECT_CHANNEL == 0:
        return await message.reply("❌ `REDIRECT_CHANNEL` is not configured.")
    
    if len(message.command) < 2:
        return await message.reply(
            "📝 **Usage:** `/createlink <movie name>`\n\n"
            "**Example:** `/createlink Avengers Endgame`"
        )

    search_query = message.text.split(" ", 1)[1].strip()
    
    if len(search_query) < 2:
        return await message.reply("❌ Search query too short. Please provide at least 2 characters.")
    
    sts = await message.reply("🔍 Searching database for accurate results...")

    try:
        files, _, total_results = await get_search_results(search_query, max_results=1)
        
        if not files:
            return await sts.edit(
                f"❌ No files found for query: `{search_query}`\n\n"
                "Please check the spelling or try a different search term."
            )
        
        accurate_name = files[0].file_name
        await sts.edit(f"✅ Found: `{accurate_name}`\n\n🎬 Fetching movie details...")

        # Get movie data from OMDb
        imdb_data = await get_omdb_data_for_link(accurate_name)
        
        if not imdb_data:
            # Fallback data if OMDb fails
            imdb_data = {
                "title": "Please Edit - OMDb Data Not Found",
                "year": "N/A",
                "poster": None,
                "plot": "No plot available",
                "genre": "N/A",
                "imdb_rating": "N/A"
            }
            logger.warning(f"No OMDb data found for: {accurate_name}")

        # Generate bot link
        bot_username = temp.U_NAME
        if not bot_username:
            return await sts.edit("❌ Bot username not configured properly.")
        
        start_link = f"https://t.me/{bot_username}?start=getfile-{search_query.replace(' ', '-')}"
        
        # Create preview cache entry
        import time
        preview_id = secrets.token_hex(8)
        PREVIEW_CACHE[preview_id] = {
            "poster": imdb_data.get("poster"),
            "title": imdb_data.get('title'),
            "year": imdb_data.get('year'),
            "genre": imdb_data.get('genre'),
            "imdb_rating": imdb_data.get('imdb_rating'),
            "plot": imdb_data.get('plot'),
            "start_link": start_link,
            "original_query": search_query,
            "accurate_name": accurate_name,
            "created_at": time.time()
        }
        
        PREVIEW_CACHE[preview_id]["caption"] = create_caption(PREVIEW_CACHE[preview_id])

        await sts.delete()
        
        success = await send_preview(client, message.from_user.id, preview_id)
        if not success:
            await message.reply("❌ Failed to send preview. Please try again.")
            if preview_id in PREVIEW_CACHE:
                del PREVIEW_CACHE[preview_id]
        
    except Exception as e:
        logger.error(f"Error in generate_link_command: {e}")
        await sts.edit(f"❌ An error occurred: `{e}`")

@Client.on_callback_query(filters.regex(r"^edit_post#"))
async def edit_post_callback(client: Client, query: CallbackQuery):
    """Handle edit button callbacks"""
    
    if query.from_user.id not in ADMINS:
        return await query.answer("❌ This feature is restricted to admins only!", show_alert=True)
    
    try:
        _, edit_type, preview_id = query.data.split("#")
    except ValueError:
        return await query.answer("❌ Invalid callback data", show_alert=True)
    
    if preview_id not in PREVIEW_CACHE:
        return await query.message.edit_text("❌ This preview has expired. Please create a new link.")

    ADMIN_CONVERSATION_STATE[query.from_user.id] = {
        "type": edit_type,
        "preview_id": preview_id,
        "timestamp": asyncio.get_event_loop().time()
    }
    
    if edit_type == "poster":
        prompt = (
            "🖼️ **Edit Poster**\n\n"
            "Please send the new poster URL.\n"
            "Make sure it's a direct link to an image (jpg, png, gif, etc.)\n\n"
            "**Example:** `https://example.com/poster.jpg`"
        )
    elif edit_type == "details":
        current_data = PREVIEW_CACHE[preview_id]
        prompt = (
            f"✏️ **Edit Details**\n\n"
            f"**Current:** {current_data['title']} | {current_data['year']}\n\n"
            "Please send the new details in this format:\n"
            "`Title | Year`\n\n"
            "**Example:** `Avengers Endgame | 2019`"
        )
    else:
        return await query.answer("❌ Invalid edit type", show_alert=True)
        
    await query.message.reply_text(prompt)
    await query.answer()

@Client.on_message(filters.private & filters.user(ADMINS) & filters.text & ~filters.command(None))
async def handle_admin_input(client: Client, message: Message):
    """Handle admin input for editing preview details"""
    
    admin_id = message.from_user.id
    state = ADMIN_CONVERSATION_STATE.get(admin_id)

    if not state:
        return  # Not waiting for input from this admin

    # Check if the conversation state has expired (5 minutes timeout)
    if asyncio.get_event_loop().time() - state.get("timestamp", 0) > 300:
        del ADMIN_CONVERSATION_STATE[admin_id]
        return await message.reply("❌ Edit session expired. Please try again.")

    preview_id = state["preview_id"]
    edit_type = state["type"]
    
    if preview_id not in PREVIEW_CACHE:
        del ADMIN_CONVERSATION_STATE[admin_id]
        return await message.reply("❌ Preview has expired. Please create a new link.")
    
    try:
        if edit_type == "poster":
            poster_url = message.text.strip()
            if not validate_poster_url(poster_url):
                return await message.reply(
                    "❌ Invalid poster URL format.\n\n"
                    "Please provide a direct link to an image file (jpg, png, gif, etc.)\n"
                    "**Example:** `https://example.com/poster.jpg`"
                )
            PREVIEW_CACHE[preview_id]["poster"] = poster_url
            
        elif edit_type == "details":
            try:
                if "|" not in message.text:
                    raise ValueError("Missing separator")
                
                parts = message.text.split("|", 1)
                if len(parts) != 2:
                    raise ValueError("Invalid format")
                
                title, year = [part.strip() for part in parts]
                
                if not title:
                    raise ValueError("Title cannot be empty")
                
                PREVIEW_CACHE[preview_id]["title"] = title
                PREVIEW_CACHE[preview_id]["year"] = year if year else "N/A"
                PREVIEW_CACHE[preview_id]["caption"] = create_caption(PREVIEW_CACHE[preview_id])
                
            except ValueError as e:
                return await message.reply(
                    "❌ Invalid format. Please use: `Title | Year`\n\n"
                    "**Example:** `Avengers Endgame | 2019`\n"
                    "**Note:** Year is optional, but the pipe (|) symbol is required."
                )

        # Clear the conversation state
        del ADMIN_CONVERSATION_STATE[admin_id]
        
        # Send updated preview
        await message.reply("✅ Details updated! Here's the new preview:")
        success = await send_preview(client, admin_id, preview_id)
        
        if not success:
            await message.reply("❌ Failed to update preview. Please try again.")
            
    except Exception as e:
        logger.error(f"Error handling admin input: {e}")
        await message.reply(f"❌ An error occurred: `{e}`")
        if admin_id in ADMIN_CONVERSATION_STATE:
            del ADMIN_CONVERSATION_STATE[admin_id]

@Client.on_callback_query(filters.regex(r"^(confirm_post|cancel_post)#"))
async def confirm_cancel_handler(client: Client, query: CallbackQuery):
    """Handle confirm and cancel button callbacks"""
    
    if query.from_user.id not in ADMINS:
        return await query.answer("❌ This feature is restricted to admins only!", show_alert=True)
    
    try:
        action, preview_id = query.data.split("#")
    except ValueError:
        return await query.answer("❌ Invalid callback data", show_alert=True)
    
    if preview_id not in PREVIEW_CACHE:
        return await query.message.edit_text("❌ This preview has expired or is invalid.")

    if action == "confirm_post":
        preview_data = PREVIEW_CACHE[preview_id]
        await query.message.edit_caption("✅ **Confirmed!** Posting to channel...")
        
        try:
            final_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("📥 Get Files", url=preview_data["start_link"])
            ]])
            
            if preview_data.get("poster") and validate_poster_url(preview_data["poster"]):
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
            
            success_msg = (
                f"🎉 **Post Created Successfully!**\n\n"
                f"📂 **File:** `{preview_data.get('accurate_name', 'N/A')}`\n"
                f"🔗 **Link:** {sent_message.link}\n"
                f"📊 **Channel:** `{REDIRECT_CHANNEL}`"
            )
            
            await query.message.edit_caption(success_msg, reply_markup=None)
            logger.info(f"Successfully posted link for: {preview_data['title']}")
            
        except Exception as e:
            logger.error(f"Error posting to channel: {e}")
            await query.message.edit_caption(f"❌ **Error posting to channel:**\n`{e}`")
        finally:
            # Clean up cache
            if preview_id in PREVIEW_CACHE:
                del PREVIEW_CACHE[preview_id]
    
    elif action == "cancel_post":
        try:
            # Clean up cache and delete message
            if preview_id in PREVIEW_CACHE:
                del PREVIEW_CACHE[preview_id]
            await query.message.delete()
            logger.info(f"Cancelled post creation for preview: {preview_id}")
        except MessageDeleteForbidden:
            await query.message.edit_text("❌ **Cancelled** - Post creation aborted.")
        except Exception as e:
            logger.error(f"Error cancelling post: {e}")
            await query.answer("❌ Error occurred while cancelling", show_alert=True)
    
    await query.answer()
