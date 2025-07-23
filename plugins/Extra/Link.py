# powered by Jisshu_bots and ZISHAN KHAN
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import temp # Import temp to get the bot's username dynamically

@Client.on_message(filters.command("link"))
async def generate_link(client, message):
    """
    Generates a direct, shareable link to trigger the bot's file search.
    """
    # Use the bot's current username dynamically instead of a hardcoded one
    bot_username = temp.U_NAME

    command_text = message.text.split(maxsplit=1)
    if len(command_text) < 2:
        await message.reply(
            "**Please provide a name for the movie!**\n\nExample: `/link game of thrones`"
        )
        return

    movie_name = command_text[1].replace(" ", "-")
    
    # Construct the link with the bot's actual username
    link = f"https://t.me/{bot_username}?start=getfile-{movie_name}"

    await message.reply(
        text=f"**Here is your direct link:**\n`{link}`",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        text="Share Link",
                        # URL-encode the link to make it safe for sharing
                        url=f"https://t.me/share/url?url={link}&text=Check%20out%20this%20movie!"
                    )
                ]
            ]
        ),
    )
