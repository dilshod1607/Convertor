import telebot
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup
from data import Database
import logging
from config import API_TOKEN, ADMINS, NOT_SUB_MESSAGE
# Initialize the bot with your API token

bot = telebot.TeleBot(API_TOKEN)

# Create a Database instance
db = Database(path_to_db="database.db")

user_images = {}
user_documents = {}

def show_channels():
    inline_keyboard = []
    channels = db.get_channels_from_db()
    print(f"Channels fetched: {channels}")
    for index, channel in enumerate(channels):
        btn = InlineKeyboardButton(text=channel[0], url=channel[2])
        inline_keyboard.append([btn])

    btn_done_sub = InlineKeyboardButton(text="Tekshirish", callback_data="subchanneldone")
    inline_keyboard.append([btn_done_sub])

    return InlineKeyboardMarkup(inline_keyboard)


# Handle callback queries
@bot.callback_query_handler(func=lambda call: True)
def handle_callback_query(call):
    query_data = call.data
    chat_id = call.from_user.id

    if query_data == "subchanneldone":
        subscribed = check_sub_channels(chat_id)
        bot.answer_callback_query(call.id, "Tekshirish amalga oshirildi.")
        if subscribed:
            bot.delete_message(chat_id, call.message.message_id)
    else:
        # Channel index should be included in the callback data
        channel_index = int(query_data)
        channels = db.get_channels_from_db()
        if 0 <= channel_index < len(channels):
            channel = channels[channel_index]
            chat_id = channel[1]
            try:
                member = bot.get_chat_member(chat_id, call.from_user.id)
                if member.status in ['member', 'administrator', 'creator']:
                    button_text = f"{channel[0]} ✅"
                else:
                    button_text = f"{channel[0]} ❌"
            except Exception as e:
                logging.error(f"Failed to check subscription for channel {chat_id}: {e}")
                button_text = f"{channel[0]} ❌"

            bot.edit_message_text(
                text="Siz hali kanallarga obuna bo'lmadingiz!",
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(button_text, url=channel[2])]]
                )
            )
        else:
            bot.answer_callback_query(call.id, "Invalid channel index.")


# Function to check user subscription status
def check_sub_channels(user_id, channel_index=None):
    channels = db.get_channels_from_db()  # Fetch channels from the database
    subscribed_channels = []
    not_subscribed_channels = []

    if channel_index is not None:
        # Check only the specific channel by index
        if channel_index < len(channels):  # Ensure the index is valid
            channel = channels[channel_index]
            chat_id = channel[1]
            try:
                member = bot.get_chat_member(chat_id, user_id)
                if member.status in ['member', 'administrator', 'creator']:
                    return True
                else:
                    return False
            except Exception as e:
                print(f"Failed to check subscription for channel {chat_id}: {e}")
                return False
        else:
            print("Invalid channel index provided.")
            return False

    # Check all channels if no specific index is provided
    for index, channel in enumerate(channels):
        chat_id = channel[1]
        try:
            member = bot.get_chat_member(chat_id, user_id)
            if member.status in ['member', 'administrator', 'creator']:
                subscribed_channels.append((index, channel))
            else:
                not_subscribed_channels.append((index, channel))
        except Exception as e:
            print(f"Failed to check subscription for channel {chat_id}: {e}")
            not_subscribed_channels.append((index, channel))

    # If subscribed to all channels
    if not not_subscribed_channels:
        bot.send_message(
            chat_id=user_id,
            text=(
                "<b>Salom</b>\n"
                "Konvertor botga xush kelibsiz\n"
                "Ushbu bot orqali rasmlarni PDF formatiga aylantira olasiz hamda barcha "
                "turdagi fayllarni ZIP formatiga aylantirishingiz mumkin"
            ),
            parse_mode='HTML'
        )
        return True

    # Create buttons
    keyboard = []
    for index, channel in subscribed_channels:
        channel_name = channel[0]  # Channel name
        channel_link = channel[2]  # Channel link
        button_text = f"{channel_name} ✅"
        keyboard.append([InlineKeyboardButton(button_text, url=channel_link)])

    for index, channel in not_subscribed_channels:
        channel_name = channel[0]  # Channel name
        channel_link = channel[2]  # Channel link
        button_text = f"{channel_name} ❌"
        keyboard.append([InlineKeyboardButton(button_text, url=channel_link)])

    # Add the check button
    keyboard.append([InlineKeyboardButton("Tekshirish", callback_data="subchanneldone")])

    # Send the message
    reply_markup = InlineKeyboardMarkup(keyboard)
    bot.send_message(
        chat_id=user_id,
        text="Siz hali kanallarga obuna bo'lmadingiz!",
        reply_markup=reply_markup
    )
    return False




# Function to handle '/start' command
def start(message):
    user = message.from_user

    # Initialize user session for image collection if not already initialized
    if user.id not in user_images:
        user_images[user.id] = []
    if user.id not in user_documents:
        user_documents[user.id] = []

    # Send welcome message
    if check_sub_channels(user.id, channel_index=1):
        user_status = db.select_user(user_id=user.id)
        if not user_status:
            # Add user to the database if not exists
            db.add_user(
                user_id=user.id,
                full_name=user.full_name,
                username=user.username
            )
            # Get the total count of users
            count = db.count_users()[0]
            bot.send_message(
                chat_id='-1002028043816',
                text=(
                    f"<b>🆕 Yangi foydalanuvchi!</b>\n"
                    f"<b>🧑🏻 Ism:</b> {user.first_name}\n"
                    f"<b>🌐 Username:</b> @{user.username if user.username else 'N/A'}\n"
                    f"<b>🆔 User ID:</b> [<code>{user.id}</code>]\n"
                    f"➖➖➖➖➖➖➖➖\n"
                    f"<b>⚙️ Umumiy:</b> {count} ta"
                ),
                parse_mode='HTML'
            )
            # Update the count of active users
            try:
                active = db.select_active()
                if active is None or len(active) == 0:
                    db.add_status(active=1)
                else:
                    db.update_active(active=active[0] + 1)
            except Exception as e:
                print(f"Error updating active users: {e}")
        else:
            bot.send_message(
                chat_id=message.chat.id,
                text=(
                    f"<b>Assalomu alaykum {user.first_name}</b>\n"
                    "Konvertor botga xush kelibsiz\n"
                    "Ushbu bot orqali rasmlarni PDF formatiga aylantira olasiz hamda barcha"
                    " turdagi fayllarni ZIP formatiga aylantirishingiz mumkin"
                ),
                parse_mode='HTML'
            )
    else:
        bot.send_message(
            chat_id=message.chat.id,
            text="Botdan foydalanish uchun ushbu kanalga obuna bo'ling!",
            reply_markup=show_channels()
        )

# Register the handler
@bot.message_handler(commands=['start'])
def handle_start(message):
    start(message)


# Function to create channel buttons

if __name__ == '__main__':
    bot.polling(none_stop=True)
