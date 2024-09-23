import os
import zipfile
from PIL import Image
from telegram import Update, File, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, CallbackContext, \
    CallbackQueryHandler
from config import *
from data import Database
import pytz
import asyncio
import logging
from datetime import datetime
import xlsxwriter as xl

os.makedirs('documents', exist_ok=True)

# Dictionary to keep track of user sessions and images
user_images = {}
user_documents = {}
last_sent_message_id = {}

db = Database(path_to_db="database.db")


# CHECK SUBSCRIBE

async def check_sub_channels(db: Database, user_id, context, channel_index=None):
    channels = db.get_channels_from_db()  # Fetch channels from the database
    subscribed_channels = []
    not_subscribed_channels = []

    if channel_index is not None:
        # Check only the specific channel by index
        if channel_index < len(channels):  # Ensure the index is valid
            channel = channels[channel_index]
            chat_id = channel[1]
            try:
                member = await context.bot.get_chat_member(chat_id, user_id)
                if member.status in ['member', 'administrator', 'creator']:
                    return True
                else:
                    return False
            except Exception as e:
                logging.error(f"Failed to check subscription for channel {chat_id}: {e}")
                return False
        else:
            logging.error("Invalid channel index provided.")
            return False

    # Check all channels if no specific index is provided
    for index, channel in enumerate(channels):
        chat_id = channel[1]
        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            if member.status in ['member', 'administrator', 'creator']:
                subscribed_channels.append((index, channel))
            else:
                not_subscribed_channels.append((index, channel))
        except Exception as e:
            logging.error(f"Failed to check subscription for channel {chat_id}: {e}")
            not_subscribed_channels.append((index, channel))

    # If subscribed to all channels
    if not not_subscribed_channels:
        await context.bot.send_message(
            chat_id=user_id,
            text=(
                "<b>Assalomu alaykum</b>\n"
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
    await context.bot.send_message(
        chat_id=user_id,
        text="Siz hali kanallarga obuna bo'lmadingiz!",
        reply_markup=reply_markup
    )
    return False


async def handle_callback_query(update: Update, context: CallbackContext):
    query = update.callback_query
    data = query.data
    chat_id = query.from_user.id

    if data == "subchanneldone":
        subscribed = await check_sub_channels(db, chat_id, context)
        await query.answer("Tekshirish amalga oshirildi.")
        if subscribed:
            await query.message.delete()

    elif data.startswith("subchanneldone"):
        await check_sub_channels(db, chat_id, context)
        await query.answer("Tekshirish amalga oshirildi.")
        await query.message.delete()
    else:
        channel_index = int(data.split("_")[1])
        channel = db.get_channels_from_db()[channel_index]  # Fetch the channel from the database
        chat_id = channel[1]

        try:
            member = await context.bot.get_chat_member(chat_id, query.from_user.id)
            if member.status in ['member', 'administrator', 'creator']:
                button_text = f"{channel[0]} ✅"
            else:
                button_text = f"{channel[0]} ❌"
        except Exception as e:
            logging.error(f"Failed to check subscription for channel {chat_id}: {e}")
            button_text = f"{channel[0]} ❌"

        await query.edit_message_text(
            text="Siz hali kanallarga obuna bo'lmadingiz!",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(button_text, url=channel[2])]]
            )
        )


async def sub_channel_done(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    user = query.from_user

    # Check subscription status
    is_subscribed = await check_sub_channels(db, user.id, context)

    # Determine the message text and reply markup
    message_text = (
        "Siz hali kanallarga obuna bo'lmadingiz!" if not is_subscribed
        else f"Xush kelibsiz, {user.mention_html()}! Botdan foydalanishingiz mumkin."
    )
    reply_markup = show_channels(db) if not is_subscribed else None

    # Edit the message if needed
    await query.message.delete()
    if query.message.text != message_text or query.message.reply_markup != reply_markup:
        await query.message.edit_text(
            text=message_text,
            reply_markup=reply_markup
        )


def show_channels(db: Database) -> InlineKeyboardMarkup:
    inline_keyboard = []
    channels = db.get_channels_from_db()  # Fetch channels from the database
    for channel in channels:
        btn = InlineKeyboardButton(text=channel[0], url=channel[2])
        inline_keyboard.append([btn])

    btn_done_sub = InlineKeyboardButton(text="Tekshirish", callback_data="subchanneldone")
    inline_keyboard.append([btn_done_sub])

    return InlineKeyboardMarkup(inline_keyboard)


# MAIN


async def start(update: Update, context: CallbackContext):
    user = update.message.from_user

    # Initialize user session for image collection if not already initialized
    if user.id not in user_images:
        user_images[user.id] = []
    if user.id not in user_documents:
        user_documents[user.id] = []

    # Send welcome message
    if await check_sub_channels(db, user.id, context, channel_index=0):
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
            await context.bot.send_message(
                chat_id='-1002028043816',
                text=f"<b>🆕 Yangi foydalanuvchi!</b>\n"
                     f"<b>🧑🏻 Ism:</b> {user.mention_html()}\n"
                     f"<b>🌐 Username:</b> @{user.username}\n"
                     f"<b>🆔 User ID:</b> [<code>{user.id}</code>]\n"
                     f"➖➖➖➖➖➖➖➖\n"
                     f"<b>⚙️ Umumiy:</b> {count} ta",
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
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"<b>Assalomu alaykum {user.mention_html()}</b>\n"
                     f"Konvertor botga xush kelibsiz\n"
                     f"Ushbu bot orqali rasmlarni PDF formatiga aylantira olasiz hamda barcha"
                     f" turdagi fayllarni ZIP formatiga aylantirishingiz mumkin",
                parse_mode='HTML'
            )

        # await context.bot.send_message(update.message.from_user.id,
        #                                text="Botdan foydalanish uchun ushbu kanalga obuna bo'ling!",
        #                                reply_markup=show_channels(db))

    else:
        await context.bot.send_message(update.message.from_user.id,
                                       text="Botdan foydalanish uchun ushbu kanalga obuna bo'ling!",
                                       reply_markup=show_channels(db))


async def collect_files(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id
    user = update.message.from_user
    # Check if the user is subscribed to the required channels
    if await check_sub_channels(db, user.id, context, channel_index=0):

        if user_id not in user_images:
            user_images[user_id] = []
        if user_id not in user_documents:
            user_documents[user_id] = []

        message_text = ""

        if update.message.photo:
            photo = update.message.photo[-1]  # Get the best quality photo
            file: File = await context.bot.get_file(photo.file_id)
            photo_path = f'documents/{file.file_unique_id}.jpg'
            await file.download_to_drive(photo_path)
            user_images[user_id].append(photo_path)
            message_text = f"Photo saved to {photo_path}"
        elif update.message.document:
            dokument = update.message.document
            file: File = await context.bot.get_file(dokument.file_id)
            document_path = f'documents/{file.file_unique_id}_{dokument.file_name}'
            await file.download_to_drive(document_path)
            user_documents[user_id].append(document_path)
            message_text = f"Document saved to {document_path}"
        else:
            await update.message.reply_text("Please send a photo or document.")
            return

        keyboard = [
            [InlineKeyboardButton("PDF yaratish", callback_data='create_pdf')],
            [InlineKeyboardButton("ZIP yaratish", callback_data='create_zip')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if user_id in last_sent_message_id:
            try:
                await context.bot.edit_message_text(
                    chat_id=update.effective_chat.id,
                    message_id=last_sent_message_id[user_id],
                    text="Fayllar qabul qilindi. Barcha fayllarni yuborib bo'lgach, "
                         f"quyidagi variantlardan birini tanlang.\n Hozircha saqlangan fayllar: "
                         f"{len(user_images[user_id])} ta foto, {len(user_documents[user_id])} ta hujjat.",
                    reply_markup=reply_markup
                )
            except Exception as e:
                print(f"Error editing message: {e}")
        else:
            sent_message = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Fayllar qabul qilindi. Barcha fayllarni yuborib bo'lgach, "
                     f"quyidagi variantlardan birini tanlang.\n Hozircha saqlangan fayllar: "
                     f"{len(user_images[user_id])} ta foto, {len(user_documents[user_id])} ta hujjat.",
                reply_markup=reply_markup
            )
            last_sent_message_id[user_id] = sent_message.message_id
    else:
        await update.message.reply_text("Siz kanallarga obuna bo'lmaganingiz sababli fayllar qabul qilinmaydi.",
                                        reply_markup=show_channels(db))


async def button(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()

    if query.data == 'create_pdf':
        await create_pdf_command(update, context, user_id)
    elif query.data == 'create_zip':
        await create_zip_command(update, context, user_id)
    elif query.data == 'subchanneldone':
        await sub_channel_done(update, context)
    # Edit the message to indicate the file is being processed


async def create_pdf_command(update: Update, context: CallbackContext, user_id: int) -> None:
    if user_id in user_images and user_images[user_id]:
        pdf_path = 'documents/images.pdf'
        images_to_convert = user_images[user_id]

        convert_to_pdf(images_to_convert, pdf_path)

        await context.bot.send_document(chat_id=update.effective_chat.id, document=open(pdf_path, 'rb'))

        user_images[user_id] = []
        for image_path in images_to_convert:
            os.remove(image_path)

        os.remove(pdf_path)
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Iltimos, avval rasmlarni yuboring.")

    if user_id in last_sent_message_id:
        try:
            del last_sent_message_id[user_id]
        except Exception as e:
            print(f"Error clearing last sent message: {e}")


async def create_zip_command(update: Update, context: CallbackContext, user_id: int) -> None:
    user_files = user_images.get(user_id, []) + user_documents.get(user_id, [])
    if not user_files:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Siz hech qanday fayl yubormadingiz.")
        return

    zip_filename = create_zip(user_id, user_files)

    user_images[user_id] = []
    user_documents[user_id] = []

    await context.bot.send_document(chat_id=update.effective_chat.id, document=open(zip_filename, 'rb'))
    os.remove(zip_filename)

    if user_id in last_sent_message_id:
        try:
            del last_sent_message_id[user_id]
        except Exception as e:
            print(f"Error clearing last sent message: {e}")


def convert_to_pdf(image_paths, output_file):
    images = [Image.open(image_path) for image_path in image_paths]
    images[0].save(output_file, save_all=True, append_images=images[1:])
    for image in images:
        image.close()


def create_zip(user_id, user_files):
    zip_filename = f"documents/ZipFile.zip"

    with zipfile.ZipFile(zip_filename, 'w') as zipf:
        for file_path in user_files:
            zipf.write(file_path, os.path.basename(file_path))

    for file_path in user_files:
        os.remove(file_path)

    return zip_filename


"""ADMIN"""

AdminPanel = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("📤 Xabar yuborish", callback_data='admin:send_message'),
        InlineKeyboardButton("📊 Bot statistikasi", callback_data='admin:bot_statics')
    ],
    [
        InlineKeyboardButton("🗄 Bazani yuklash", callback_data='admin:save_base'),
        InlineKeyboardButton("➕ Kanal qo'shish", callback_data='admin:add_channel')  # Yangi tugma
    ],
    [
        InlineKeyboardButton("📋 Kanallar", callback_data='admin:channels')  # Yangi tugma qo'shildi
    ]
])

GoBack = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("◀️ Ortga", callback_data='admin:go_back')
    ]
])

BaseType = InlineKeyboardMarkup([
    [InlineKeyboardButton("⚙️ Databse | .db", callback_data='base:db')],
    [InlineKeyboardButton("📑 Excel | .xlsx", callback_data='base:xlsx')],
    [
        InlineKeyboardButton(text="◀️ Ortga", callback_data='admin:go_back')
    ]
])


async def admin_panel(update: Update, context: CallbackContext) -> int:
    user = update.message.from_user
    if user.id in ADMINS:
        await update.message.reply_text(
            text=f"<b>Assalomu alaykum xurmatli {user.full_name}</b>\n\n"
                 f"😊 Bugun nimalarni o'zgartiramiz?",
            reply_markup=AdminPanel,
            parse_mode='HTML'
        )
    # return SEND_MESSAGE


async def buttonadmin(update: Update, context: CallbackContext) -> None:
    query = update.callback_query

    if query.data == "admin:send_message":
        await query.edit_message_text(
            text="<b>😉 Ajoyib!, kerakli xabarni yuboring</b>",
            reply_markup=GoBack,
            parse_mode='HTML'
        )
        context.user_data['awaiting_message'] = True
    await query.answer()


async def gobackbutton(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    if query.data == "admin:go_back":
        await query.edit_message_text(
            "<b>😉 Ajoyib!, kerakli xabarni yuboring</b>",
            reply_markup=AdminPanel,
            parse_mode='HTML'
        )


async def handle_channels(update, context):
    query = update.callback_query
    admin_id = query.from_user.id

    # Retrieve the list of channels from the database
    channels = db.select_all_channel()
    print(f"Retrieved channels in handle_channels: {channels}")  # Debug statement

    # Check if there are channels in the database
    if not channels:
        await query.message.reply_text("Hozirda kanallar mavjud emas.")
        return

    # Format the channels into a message
    channels_message = "Kanallar ro'yxati:\n"
    for channel in channels:
        id, name, channel_id, link = channel  # Adjust based on your database schema
        channels_message += f"Name: {name}\nID: {channel_id}\nLink: {link}\n\n"

    # Send the message with the list of channels
    await query.message.reply_text(channels_message)

    # Prompt the admin to enter the name of the channel to be deleted
    await query.message.reply_text("Kanalni o'chirish uchun kanal nomini yuboring.")

    # Set up a handler for the next message from the admin
    context.user_data['deletion_request'] = True


async def handle_channel_deletion(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id

    # Check if this message is related to the deletion request
    if context.user_data.get('deletion_request'):
        # Remove the flag
        message_text = update.message
        # Check if the user is an admin
        if user_id not in ADMINS:  # Replace with your list of admin IDs
            await update.message.reply_text("Sizda bu amalni bajarish huquqi yo'q.")
            return

        # Delete the channel from the database if it exists
        if db.delete_channel_by_name(message_text.text):
            await update.message.reply_text(f"{message_text} nomli kanal muvaffaqiyatli o'chirildi.")
        else:
            await update.message.reply_text(f"{message_text} nomli kanal topilmadi.")
        context.user_data['deletion_request'] = False


async def handle_admin_buttons(update, context):
    query = update.callback_query
    callback_data = query.data

    if callback_data == 'admin:channels':
        await handle_channels(update, context)


# Message Handler
async def receive_message(update: Update, context: CallbackContext) -> None:
    if context.user_data.get('awaiting_message'):
        message = update.message
        users = db.select_all_users()
        count = db.count_users()[0]
        x = 0
        y = 0

        start_ads = datetime.now(pytz.timezone('Asia/Tashkent'))
        text = await message.reply_text(
            f"<b>📨 Xabar qabul qilindi</b>\n\n"
            f"<b>📤 Yuborilishi kerak:</b> {count} ta\n"
            f"<b>⏰ Boshlandi:</b> {start_ads.strftime('%d/%m/%Y  %H:%M:%S')}",
            parse_mode='HTML'
        )

        for user in users:
            try:
                if message.text:
                    await context.bot.send_message(chat_id=user[0], text=message.text)
                elif message.photo:
                    await context.bot.send_photo(chat_id=user[0], photo=message.photo[-1].file_id,
                                                 caption=message.caption)
                elif message.video:
                    await context.bot.send_video(chat_id=user[0], video=message.video.file_id, caption=message.caption)
                elif message.document:
                    await context.bot.send_document(chat_id=user[0], document=message.document.file_id,
                                                    caption=message.caption)
                elif message.audio:
                    await context.bot.send_audio(chat_id=user[0], audio=message.audio.file_id, caption=message.caption)
                elif message.voice:
                    await context.bot.send_voice(chat_id=user[0], voice=message.voice.file_id, caption=message.caption)
                elif message.sticker:
                    await context.bot.send_sticker(chat_id=user[0], sticker=message.sticker.file_id)
                else:
                    await context.bot.copy_message(chat_id=user[0], from_chat_id=message.chat_id,
                                                   message_id=message.message_id)

                x += 1
            except Exception as e:
                y += 1
                print(f"Failed to send message to {user[0]}: {e}")

            await asyncio.sleep(0.05)

        finish_ads = datetime.now(pytz.timezone('Asia/Tashkent'))
        db.update_active(active=x)
        db.update_block(block=y)
        await text.edit_text(
            text=f"<b>📨 Xabar yuborilishi yakunlandi</b>\n\n"
                 f"<b>📤 Yuborildi:</b> {x}/{x + y} ta\n"
                 f"<b>⏰ Boshlandi:</b> {start_ads.strftime('%d/%m/%Y  %H:%M:%S')}\n"
                 f"<b>⏰ Yakunlandi:</b> {finish_ads.strftime('%d/%m/%Y  %H:%M:%S')}\n"
                 f"<b>🕓 Umumiy ketgan vaqt:</b> {(finish_ads - start_ads).seconds} soniya",
            parse_mode='HTML',
            reply_markup=GoBack
        )
        context.user_data['awaiting_message'] = False


async def handle_admin_message(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    if query.data == "admin:add_channel":
        await query.edit_message_text(
            text="Yangi kanal nomini kiriting:",
            reply_markup=None,
            parse_mode='HTML'
        )
        context.user_data['step'] = 'awaiting_channel_name'


async def handle_channel_info(update: Update, context: CallbackContext) -> None:
    message = update.message
    step = context.user_data.get('step')

    if step == 'awaiting_channel_name':
        channel_name = message.text
        context.user_data['channel_name'] = channel_name
        context.user_data['step'] = 'awaiting_channel_id'
        await message.reply_text("Kanal ID'sini kiriting:")

    elif step == 'awaiting_channel_id':
        channel_id = message.text
        context.user_data['channel_id'] = channel_id
        context.user_data['step'] = 'awaiting_channel_link'
        await message.reply_text("Kanal linkini kiriting:")

    elif step == 'awaiting_channel_link':
        channel_link = message.text
        channel_name = context.user_data.get('channel_name')
        channel_id = context.user_data.get('channel_id')

        if channel_name and channel_id and channel_link:
            # Print the data to debug
            print(f"Adding channel: {channel_name}, {channel_id}, {channel_link}")

            success = db.add_channel(channel_name, channel_id, channel_link)

            # Print success status
            print(f"Channel added successfully: {success}")

            if success:
                await message.reply_text("Kanal muvaffaqiyatli qo'shildi.")
            else:
                await message.reply_text("Kanal qo'shishda xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.")

            # Clear user data and reset step
            context.user_data.pop('channel_name', None)
            context.user_data.pop('channel_id', None)
            context.user_data['step'] = None
        else:
            await message.reply_text("Kanal ma'lumotlari noto'g'ri. Qaytadan urinib ko'ring.")
            context.user_data['step'] = 'awaiting_channel_name'  # Restart the process


async def admin_bot_statics(update: Update, context: CallbackContext) -> None:
    text = await update.callback_query.message.edit_text("<b>📊 Bot statistikasi yuklanmoqda...</b>")
    try:
        active = db.select_active()[0]
    except:
        active = 0
    try:
        block = db.select_block()[0]
    except:
        block = 0

    start_bot = datetime(year=2024, month=8, day=12)
    today_bot = datetime.now().date()
    today_bot_datetime = datetime.combine(today_bot, datetime.min.time())
    await text.edit_text(
        text=f"<b>📊 Bot statistikasi</b>\n\n"
             f"<b>✅ Aktiv:</b> {active} ta\n"
             f"<b>❌ Blok:</b> {block} ta\n"
             f"<b>🔰 Umumiy:</b> {active + block} ta\n"
             f"➖➖➖➖➖➖➖➖\n"
             f"<b>⏸ Bot ishga tushgan:</b> {start_bot.strftime('%d/%m/%Y')}\n"
             f"<b>📆 Bugun:</b> {today_bot.strftime('%d/%m/%Y')}\n"
             f"<b>📆 Bot ishga tushganiga:</b> {(today_bot_datetime - start_bot).days} kun bo'ldi",
        parse_mode='HTML',
        reply_markup=GoBack
    )


async def save_base(update: Update, context: CallbackContext) -> None:
    await update.callback_query.message.edit_text(
        text="<b>🗂 Bazani qaysi turda yuklab olishingizni tanlang</b>",
        reply_markup=BaseType
    )


async def dot_db(update: Update, context: CallbackContext) -> None:
    await update.callback_query.message.delete()
    input_file = 'database.db'
    with open(input_file, 'rb') as file:
        await context.bot.send_document(
            chat_id=update.callback_query.message.chat_id,
            document=file,
            caption="<b>main.db</b>\n\nDataBase baza yuklandi",
            parse_mode='HTML',
        )


async def dot_xlsx(update: Update, context: CallbackContext) -> None:
    await update.callback_query.message.delete()
    users = db.select_all_users()

    workbook = xl.Workbook("users.xlsx")
    bold_format = workbook.add_format({'bold': True})
    worksheet = workbook.add_worksheet("Users")
    worksheet.write('A1', 'User ID', bold_format)
    worksheet.write('B1', 'Fullname', bold_format)
    worksheet.write('C1', 'Username', bold_format)

    rowIndex = 2
    for user in users:
        user_id, fullname, username, = user
        worksheet.write(f'A{rowIndex}', user_id)
        worksheet.write(f'B{rowIndex}', fullname)
        worksheet.write(f'C{rowIndex}', f"@{username}")
        rowIndex += 1

    workbook.close()
    file = "users.xlsx"
    with open(file, 'rb') as fileaa:
        await context.bot.send_document(
            chat_id=update.callback_query.message.chat_id,
            document=fileaa,
            caption="<b>users.xlsx</b>\n\nExcel formatida baza yuklandi",
            parse_mode='HTML'
        )
    os.remove("users.xlsx")


async def is_user_admin(user_id: int) -> bool:
    return user_id in ADMINS


async def handle_message(update: Update, context: CallbackContext) -> None:
    user_id = update.message.from_user.id

    if await is_user_admin(user_id):
        await receive_message(update, context)
    else:
        await collect_files(update, context)


def main():
    # Replace 'YOUR_ACTUAL_BOT_TOKEN' with your actual bot token
    application = ApplicationBuilder().token(API_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler('start', start))
    # application.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, collect_files))
    application.add_handler(CommandHandler("admin", admin_panel))

    application.add_handler(MessageHandler(
        filters.TEXT | filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.VOICE,
        handle_message))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_channel_deletion))

    application.add_handler(CallbackQueryHandler(buttonadmin, pattern="admin:send_message"))
    application.add_handler(CallbackQueryHandler(admin_bot_statics, pattern='admin:bot_statics'))
    application.add_handler(CallbackQueryHandler(save_base, pattern='admin:save_base'))
    application.add_handler(CallbackQueryHandler(gobackbutton, pattern='admin:go_back'))
    application.add_handler(CallbackQueryHandler(handle_admin_buttons, pattern='admin:channels'))
    application.add_handler(CallbackQueryHandler(dot_db, pattern='base:db'))
    application.add_handler(CallbackQueryHandler(dot_xlsx, pattern='base:xlsx'))
    application.add_handler(CallbackQueryHandler(handle_admin_message, pattern='admin:add_channel'))
    application.add_handler(CallbackQueryHandler(button))
    application.add_handler(CallbackQueryHandler(handle_callback_query))

    # Start the bot
    application.run_polling()


if __name__ == '__main__':
    main()
