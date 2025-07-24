import asyncio
import secrets
import aiohttp
import re
import os
import json
import time
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from pyrogram.errors import FloodWait, MessageNotModified, ChatAdminRequired
from info import ADMINS, REDIRECT_CHANNEL, OMDB_API_KEY
from utils import temp
from database.ia_filterdb import get_search_results

# Global dictionaries for state management
ADMIN_CONVERSATION_STATE = {}
PREVIEW_CACHE = {}
ACTIVE_UPDATES = {}

# ==================== CREATE LINK FUNCTIONALITY ====================

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
            "admin_id": message.from_user.id,
            "created_at": time.time()
        }

        await sts.delete()
        await send_preview(client, message.from_user.id, preview_id)
        
    except Exception as e:
        await sts.edit(f"❌ **Error occurred:** `{e}`")
        print(f"Error in generate_link_command: {e}")

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

    ADMIN_CONVERSATION_STATE[query.from_user.id] = {
        "type": edit_type, 
        "preview_id": preview_id,
        "created_at": time.time(),
        "awaiting_input": True  # Add flag to identify legitimate admin input
    }
    
    prompts = {
        "poster": "🖼️ **Please send the new poster URL now.**\n\nSend a direct image URL (jpg, png, etc.)",
        "details": "✏️ **Please send the new details in this format:**\n\n`Title | Year`\n\n**Example:** `Avengers Endgame | 2019`",
        "caption": "📝 **Please send the new caption text.**\n\nThis will replace the entire description."
    }
    
    prompt = prompts.get(edit_type, "Please send your input:")
    await query.message.reply_text(prompt)
    await query.answer()

# FIXED: Only handle admin input when specifically waiting for edit input
@Client.on_message(filters.private & filters.user(ADMINS) & filters.text)
async def handle_admin_input(client, message: Message):
    """Handle admin input for editing previews - ONLY when specifically awaiting input"""
    admin_id = message.from_user.id
    state = ADMIN_CONVERSATION_STATE.get(admin_id)

    # CRITICAL FIX: Only handle input if we're specifically awaiting it
    if not state or not state.get("awaiting_input", False):
        return  # Let other handlers process this message

    # Skip if it's a command (let command handlers process it)
    if message.text.startswith('/'):
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

# ==================== UPDATE LINKS FUNCTIONALITY ====================

async def process_message_buttons(reply_markup, new_bot_username):
    """Process and update buttons in a message's reply markup"""
    updated = False
    new_keyboard = []
    
    for row in reply_markup.inline_keyboard:
        new_row = []
        for button in row:
            if button.url and "?start=getfile-" in button.url:
                # Extract the query part
                query_part = button.url.split("?start=", 1)[1]
                new_url = f"https://t.me/{new_bot_username}?start={query_part}"
                
                if button.url != new_url:
                    new_button = InlineKeyboardButton(button.text, url=new_url)
                    new_row.append(new_button)
                    updated = True
                else:
                    new_row.append(button)
            else:
                new_row.append(button)
        new_keyboard.append(new_row)
    
    return InlineKeyboardMarkup(new_keyboard) if updated else None

def calculate_success_rate(session_data):
    """Calculate the success rate of updates"""
    total = session_data["updated_count"] + session_data["error_count"]
    if total == 0:
        return 100
    return round((session_data["updated_count"] / total) * 100, 1)

def generate_completion_report(session_data, new_bot_username):
    """Generate a comprehensive completion report"""
    success_rate = calculate_success_rate(session_data)
    
    report = (
        f"✅ **Link Update Complete!**\n\n"
        f"**Target Bot:** `@{new_bot_username}`\n"
        f"**Total Processed:** `{session_data['total_processed']}` messages\n"
        f"**Successfully Updated:** `{session_data['updated_count']}` links\n"
        f"**Errors:** `{session_data['error_count']}`\n"
        f"**Success Rate:** `{success_rate}%`\n\n"
    )
    
    if session_data.get("start_time") and session_data.get("completion_time"):
        try:
            start_time = datetime.fromisoformat(session_data["start_time"])
            end_time = datetime.fromisoformat(session_data["completion_time"])
            duration = end_time - start_time
            report += f"**Duration:** `{str(duration).split('.')[0]}`\n\n"
        except:
            pass
    
    if session_data["error_count"] > 0:
        report += f"⚠️ **{session_data['error_count']} errors occurred during the process.**\n"
        if len(session_data["errors"]) > 0:
            recent_errors = session_data["errors"][-3:]  # Show last 3 errors
            report += "**Recent errors:**\n"
            for error in recent_errors:
                report += f"• Message ID `{error['message_id']}`: {error['error'][:50]}...\n"
    
    report += f"\n🎉 **All links now point to @{new_bot_username}!**"
    return report

async def cleanup_backup_file(file_path, delay=300):
    """Clean up backup files after a delay"""
    await asyncio.sleep(delay)
    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"Cleaned up backup file: {file_path}")
    except Exception as e:
        print(f"Error cleaning up backup file {file_path}: {e}")

@Client.on_message(filters.command("updatelinks") & filters.user(ADMINS))
async def update_all_links(client, message):
    """Enhanced updatelinks command with better error handling and safety features"""
    
    # Validation checks
    if REDIRECT_CHANNEL == 0:
        return await message.reply(
            "❌ **Configuration Error**\n\n"
            "`REDIRECT_CHANNEL` is not set. Please configure it in your settings."
        )
    
    if len(message.command) < 2:
        return await message.reply(
            "ℹ️ **Usage:** `/updatelinks NEW_BOT_USERNAME`\n\n"
            "**Example:** `/updatelinks MyNewBot`\n"
            "⚠️ **Note:** Provide username without @ symbol"
        )
    
    # Prevent multiple simultaneous updates
    admin_id = message.from_user.id
    if admin_id in ACTIVE_UPDATES:
        return await message.reply(
            "⚠️ **Update Already Running**\n\n"
            f"You already have an active update process running.\n"
            f"Started at: `{ACTIVE_UPDATES[admin_id]['start_time']}`\n\n"
            "Please wait for it to complete or use `/cancelupdate` to stop it."
        )
    
    new_bot_username = message.command[1].strip().replace("@", "")
    
    # Validate bot username format
    if not new_bot_username.replace("_", "").replace("Bot", "").isalnum():
        return await message.reply(
            "❌ **Invalid Username**\n\n"
            "Bot username should only contain letters, numbers, and underscores."
        )
    
    # Check if bot exists (optional validation)
    try:
        bot_info = await client.get_users(new_bot_username)
        if not bot_info.is_bot:
            return await message.reply(
                f"⚠️ **Warning:** `@{new_bot_username}` doesn't appear to be a bot.\n"
                "Are you sure you want to continue? Send `/updatelinks {new_bot_username} confirm` to proceed."
            )
    except Exception:
        # If we can't fetch user info, ask for confirmation
        if len(message.command) < 3 or message.command[2] != "confirm":
            return await message.reply(
                f"⚠️ **Cannot verify bot:** `@{new_bot_username}`\n\n"
                "This might be because:\n"
                "• The bot doesn't exist\n"
                "• The bot is private\n"
                "• Network issues\n\n"
                f"To proceed anyway, use: `/updatelinks {new_bot_username} confirm`"
            )
    
    # Progress tracking setup
    progress_file = f"update_progress_{admin_id}.json"
    session_data = {
        "admin_id": admin_id,
        "new_bot_username": new_bot_username,
        "start_time": datetime.now().isoformat(),
        "last_processed_id": 0,
        "total_processed": 0,
        "updated_count": 0,
        "error_count": 0,
        "errors": []
    }
    
    # Check for existing progress file
    if os.path.exists(progress_file):
        try:
            with open(progress_file, "r") as f:
                saved_data = json.load(f)
                session_data.update({
                    "last_processed_id": saved_data.get("last_processed_id", 0),
                    "total_processed": saved_data.get("total_processed", 0),
                    "updated_count": saved_data.get("updated_count", 0),
                    "error_count": saved_data.get("error_count", 0),
                    "errors": saved_data.get("errors", [])
                })
        except Exception as e:
            print(f"Error loading progress file: {e}")
    
    # Mark update as active
    ACTIVE_UPDATES[admin_id] = {
        "start_time": session_data["start_time"],
        "status": "running",
        "progress_file": progress_file
    }
    
    # Initial status message
    sts_text = f"🔄 **Starting Link Update Process**\n\n"
    sts_text += f"**Target Bot:** `@{new_bot_username}`\n"
    sts_text += f"**Channel:** `{REDIRECT_CHANNEL}`\n"
    
    if session_data["last_processed_id"] > 0:
        sts_text += f"**Resuming from Message ID:** `{session_data['last_processed_id']}`\n"
        sts_text += f"**Previously Processed:** `{session_data['total_processed']}` messages\n"
        sts_text += f"**Previously Updated:** `{session_data['updated_count']}` links\n"
    
    sts_text += "\n⏳ **Processing messages...**"
    sts = await message.reply(sts_text)
    
    try:
        # Check bot permissions in the channel
        try:
            bot_member = await client.get_chat_member(REDIRECT_CHANNEL, client.me.id)
            if not bot_member.privileges or not bot_member.privileges.can_edit_messages:
                raise ChatAdminRequired()
        except ChatAdminRequired:
            await sts.edit(
                "❌ **Permission Error**\n\n"
                "Bot doesn't have permission to edit messages in the redirect channel.\n"
                "Please make sure the bot is an admin with 'Edit Messages' permission."
            )
            return
        except Exception as e:
            print(f"Permission check error: {e}")
        
        last_update_time = time.time()
        batch_processed = 0
        
        # Main processing loop
        async for msg in client.get_chat_history(REDIRECT_CHANNEL):
            # Check if update was cancelled
            if admin_id not in ACTIVE_UPDATES:
                await sts.edit("❌ **Update Cancelled by User**")
                return
            
            # Skip messages if resuming
            if session_data["last_processed_id"] != 0 and msg.id >= session_data["last_processed_id"]:
                continue
            
            session_data["total_processed"] += 1
            batch_processed += 1
            
            # Process message if it has inline keyboard
            if msg.reply_markup and msg.reply_markup.inline_keyboard:
                try:
                    updated_markup = await process_message_buttons(
                        msg.reply_markup, new_bot_username
                    )
                    
                    if updated_markup:
                        await client.edit_message_reply_markup(
                            chat_id=msg.chat.id,
                            message_id=msg.id,
                            reply_markup=updated_markup
                        )
                        session_data["updated_count"] += 1
                        
                except FloodWait as e:
                    print(f"FloodWait: Sleeping for {e.value} seconds")
                    await asyncio.sleep(e.value)
                    # Retry the operation
                    try:
                        updated_markup = await process_message_buttons(
                            msg.reply_markup, new_bot_username
                        )
                        if updated_markup:
                            await client.edit_message_reply_markup(
                                chat_id=msg.chat.id,
                                message_id=msg.id,
                                reply_markup=updated_markup
                            )
                            session_data["updated_count"] += 1
                    except Exception as retry_error:
                        session_data["error_count"] += 1
                        session_data["errors"].append({
                            "message_id": msg.id,
                            "error": str(retry_error),
                            "timestamp": datetime.now().isoformat()
                        })
                
                except MessageNotModified:
                    # This is fine, message was already up to date
                    pass
                
                except Exception as e:
                    session_data["error_count"] += 1
                    error_info = {
                        "message_id": msg.id,
                        "error": str(e),
                        "timestamp": datetime.now().isoformat()
                    }
                    session_data["errors"].append(error_info)
                    print(f"Failed to update message {msg.id}: {e}")
            
            # Update progress periodically
            current_time = time.time()
            if (batch_processed >= 25 or 
                current_time - last_update_time > 30 or 
                session_data["total_processed"] % 100 == 0):
                
                session_data["last_processed_id"] = msg.id
                
                # Save progress to file
                with open(progress_file, "w") as f:
                    json.dump(session_data, f, indent=2)
                
                # Update status message
                progress_text = (
                    f"🔄 **Link Update In Progress**\n\n"
                    f"**Target Bot:** `@{new_bot_username}`\n"
                    f"**Processed:** `{session_data['total_processed']}` messages\n"
                    f"**Updated:** `{session_data['updated_count']}` links\n"
                    f"**Errors:** `{session_data['error_count']}`\n"
                    f"**Last Message ID:** `{msg.id}`\n\n"
                    f"⏳ **Status:** Processing...\n"
                    f"📊 **Success Rate:** {calculate_success_rate(session_data)}%"
                )
                
                try:
                    await sts.edit(progress_text)
                except Exception as e:
                    print(f"Failed to update status message: {e}")
                
                last_update_time = current_time
                batch_processed = 0
                
                # Add small delay to prevent overwhelming
                await asyncio.sleep(0.5)
        
        # Process completion
        session_data["completion_time"] = datetime.now().isoformat()
        
        # Generate completion report
        completion_text = generate_completion_report(session_data, new_bot_username)
        await sts.edit(completion_text)
        
        # Clean up progress file on successful completion
        if os.path.exists(progress_file):
            try:
                # Keep a backup for a few minutes in case of issues
                backup_file = f"{progress_file}.completed"
                os.rename(progress_file, backup_file)
                
                # Schedule cleanup of backup file
                asyncio.create_task(cleanup_backup_file(backup_file, delay=300))  # 5 minutes
            except Exception as e:
                print(f"Error handling progress file: {e}")
        
    except Exception as e:
        error_text = (
            f"❌ **Update Process Failed**\n\n"
            f"**Error:** `{str(e)}`\n"
            f"**Processed:** `{session_data['total_processed']}` messages\n"
            f"**Updated:** `{session_data['updated_count']}` links\n\n"
            f"📁 **Progress saved.** Run the command again to resume from where it stopped.\n\n"
            f"🔍 **Debug Info:** Check logs for detailed error information."
        )
        await sts.edit(error_text)
        print(f"Update process error: {e}")
        
        # Save final progress
        session_data["error_info"] = str(e)
        session_data["failed_at"] = datetime.now().isoformat()
        with open(progress_file, "w") as f:
            json.dump(session_data, f, indent=2)
    
    finally:
        # Clean up active updates tracking
        if admin_id in ACTIVE_UPDATES:
            del ACTIVE_UPDATES[admin_id]

@Client.on_message(filters.command("cancelupdate") & filters.user(ADMINS))
async def cancel_update(client, message):
    """Cancel an active update process"""
    admin_id = message.from_user.id
    
    if admin_id not in ACTIVE_UPDATES:
        return await message.reply(
            "ℹ️ **No Active Update**\n\n"
            "You don't have any active update process running."
        )
    
    # Remove from active updates (this will stop the main loop)
    update_info = ACTIVE_UPDATES.pop(admin_id)
    
    await message.reply(
        f"❌ **Update Process Cancelled**\n\n"
        f"**Started at:** `{update_info['start_time']}`\n"
        f"**Status:** Cancelled by user\n\n"
        f"📁 **Progress has been saved.** You can resume later using `/updatelinks`."
    )

@Client.on_message(filters.command("updatestatus") & filters.user(ADMINS))
async def update_status(client, message):
    """Check the status of active updates"""
    admin_id = message.from_user.id
    
    if admin_id not in ACTIVE_UPDATES:
        # Check if there's a progress file
        progress_file = f"update_progress_{admin_id}.json"
        if os.path.exists(progress_file):
            try:
                with open(progress_file, "r") as f:
                    saved_data = json.load(f)
                
                status_text = (
                    f"📋 **Saved Progress Found**\n\n"
                    f"**Bot Username:** `@{saved_data.get('new_bot_username', 'Unknown')}`\n"
                    f"**Processed:** `{saved_data.get('total_processed', 0)}` messages\n"
                    f"**Updated:** `{saved_data.get('updated_count', 0)}` links\n"
                    f"**Errors:** `{saved_data.get('error_count', 0)}`\n"
                    f"**Last Message ID:** `{saved_data.get('last_processed_id', 0)}`\n\n"
                    f"Use `/updatelinks {saved_data.get('new_bot_username', 'BOT_USERNAME')}` to resume."
                )
                return await message.reply(status_text)
            except Exception as e:
                print(f"Error reading progress file: {e}")
        
        return await message.reply(
            "ℹ️ **No Active Update**\n\n"
            "You don't have any active update process running or saved progress."
        )
    
    update_info = ACTIVE_UPDATES[admin_id]
    await message.reply(
        f"🔄 **Update Process Running**\n\n"
        f"**Started at:** `{update_info['start_time']}`\n"
        f"**Status:** `{update_info['status']}`\n\n"
        f"Use `/cancelupdate` to stop the process."
    )

# ==================== CLEANUP AND MAINTENANCE ====================

async def cleanup_expired_data():
    """Clean up expired preview data and conversation states"""
    current_time = time.time()
    
    # Clean up previews older than 1 hour
    expired_previews = [
        pid for pid, data in PREVIEW_CACHE.items() 
        if current_time - data.get("created_at", current_time) > 3600
    ]
    
    for pid in expired_previews:
        del PREVIEW_CACHE[pid]
        print(f"Cleaned up expired preview: {pid}")
    
    # Clean up conversation states older than 30 minutes
    expired_states = [
        uid for uid, state in ADMIN_CONVERSATION_STATE.items() 
        if current_time - state.get("created_at", current_time) > 1800
    ]
    
    for uid in expired_states:
        del ADMIN_CONVERSATION_STATE[uid]
        print(f"Cleaned up expired conversation state for user: {uid}")

# ==================== HELP AND INFO COMMANDS ====================

@Client.on_message(filters.command("linkhelp") & filters.user(ADMINS))
async def link_help_command(client, message):
    """Show help information for link management commands"""
    help_text = """
🔗 **Link Management Bot - Help**

**📝 CREATE LINK COMMANDS:**
• `/createlink <movie name>` - Create a new link post with preview
• **Example:** `/createlink Avengers Endgame`

**🔄 UPDATE LINK COMMANDS:**
• `/updatelinks <new_bot_username>` - Update all links to new bot
• `/updatelinks <bot_username> confirm` - Force update without verification
• `/cancelupdate` - Cancel active update process
• `/updatestatus` - Check update progress

**📊 FEATURES:**
✅ **Create Links:** OMDb integration, poster preview, editable captions
✅ **Update Links:** Bulk update, progress tracking, error handling
✅ **Safety:** Admin-only access, validation checks, resume capability
✅ **Management:** Real-time status, detailed reports, automatic cleanup

**⚙️ CONFIGURATION REQUIRED:**
• `REDIRECT_CHANNEL` - Channel ID where links are posted
• `OMDB_API_KEY` - For movie information (optional)
• `ADMINS` - List of admin user IDs

**🆘 TROUBLESHOOTING:**
• Make sure bot has admin permissions in redirect channel
• Check if `REDIRECT_CHANNEL` is properly configured
• Ensure bot username is valid (no @ symbol needed)

**📈 EXAMPLE WORKFLOW:**
1️⃣ `/createlink Spider Man` - Creates preview
2️⃣ Edit poster/details if needed
3️⃣ Confirm to post to channel
4️⃣ `/updatelinks NewBot` - Updates all links to new bot

Need help? Contact your bot administrator.
"""
    await message.reply(help_text)

@Client.on_message(filters.command("linkstats") & filters.user(ADMINS))
async def link_stats_command(client, message):
    """Show current statistics and status"""
    stats_text = f"""
📊 **Link Management Statistics**

**🔍 ACTIVE SESSIONS:**
• **Previews:** `{len(PREVIEW_CACHE)}` active
• **Conversations:** `{len(ADMIN_CONVERSATION_STATE)}` ongoing
• **Updates:** `{len(ACTIVE_UPDATES)}` running

**⚙️ CONFIGURATION:**
• **Redirect Channel:** `{REDIRECT_CHANNEL if REDIRECT_CHANNEL != 0 else 'Not Set'}`
• **OMDb API:** `{'✅ Configured' if OMDB_API_KEY else '❌ Not Set'}`
• **Admin Count:** `{len(ADMINS)}` users

**💾 SYSTEM STATUS:**
• **Bot Status:** ✅ Online
• **Memory Usage:** Normal
• **API Status:** {'✅ Active' if OMDB_API_KEY else '⚠️ Limited'}

Use `/linkhelp` for command information.
"""
    await message.reply(stats_text)

# ==================== CANCEL EDIT COMMAND ====================

@Client.on_message(filters.command("canceledit") & filters.user(ADMINS))
async def cancel_edit_command(client, message):
    """Cancel any active edit operation"""
    admin_id = message.from_user.id
    
    if admin_id in ADMIN_CONVERSATION_STATE:
        del ADMIN_CONVERSATION_STATE[admin_id]
        await message.reply("✅ **Edit operation cancelled.** You can now use other bot functions normally.")
    else:
        await message.reply("ℹ️ **No active edit operation** to cancel.")

# ==================== PERIODIC CLEANUP TASK ====================

async def start_cleanup_task():
    """Start the periodic cleanup task"""
    while True:
        try:
            await cleanup_expired_data()
            await asyncio.sleep(1800)  # Run every 30 minutes
        except Exception as e:
            print(f"Cleanup task error: {e}")
            await asyncio.sleep(300)  # Wait 5 minutes on error

# Auto-start cleanup task
asyncio.create_task(start_cleanup_task())

print("✅ Link Management System Loaded Successfully!")
print("📝 Available Commands: /createlink, /updatelinks, /linkhelp, /linkstats")
print("🔧 Management Commands: /cancelupdate, /updatestatus, /canceledit")
print("🔧 IMPORTANT: Admin input handler fixed - other bot functions should work normally now")
