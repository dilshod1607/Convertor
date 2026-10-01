import io
import os
import sys
import time
import asyncio
import logging
from datetime import datetime
import pytz
import warnings

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile,
    InputMediaPhoto
)
from telegram.error import RetryAfter, BadRequest
from telegram.warnings import PTBUserWarning
from telegram.request import HTTPXRequest

# PTB ogohlantirishlarini tozalash
warnings.filterwarnings("ignore", category=PTBUserWarning)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters
)

from config import (
    API_TOKEN,
    ADMINS,
    LOG_CHANNEL_ID,
    DATABASE_PATH,
    DOCUMENTS_DIR,
    WELCOME_MESSAGE,
    NOT_SUB_MESSAGE
)
from data import Database
from converter import (
    get_user_dir,
    convert_images_to_pdf,
    convert_pdf_to_images,
    create_zip_archive,
    extract_archive,
    is_archive_encrypted,
    ArchivePasswordRequired,
    ArchiveWrongPassword,
    cleanup_user_files,
    cleanup_old_files
)
from keyboards import (
    get_subscription_keyboard,
    get_file_action_keyboard,
    get_admin_main_keyboard,
    get_back_keyboard,
    get_channels_manage_keyboard
)

# Logging sozlamalari
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Asosiy papkalar va Baza
os.makedirs(DOCUMENTS_DIR, exist_ok=True)
db = Database(path_to_db=DATABASE_PATH)

# Foydalanuvchi sessiyalari
user_sessions = {}

# Admin holatlari (States)
ADMIN_BROADCAST_STATE = 1
ADMIN_ADD_CH_NAME = 2
ADMIN_ADD_CH_ID = 3
ADMIN_ADD_CH_LINK = 4


def get_user_session(user_id: int) -> dict:
    """Foydalanuvchi sessiyasini olish yoki yangisini yaratish"""
    if user_id not in user_sessions:
        user_sessions[user_id] = {
            'items': [],  # [{'type': 'photo'|'pdf'|'archive'|'doc', 'file_id': ..., 'file_name': ..., 'file_size': ...}]
            'menu_msg_id': None,
            'chat_id': None,
            'update_task': None,
            'lock': asyncio.Lock(),
            'waiting_for_password': False,
            'archive_paths': None,
            'extract_dir': None,
            'user_dir': None,
            'progress_msg_id': None
        }
    elif 'lock' not in user_sessions[user_id]:
        user_sessions[user_id]['lock'] = asyncio.Lock()
    return user_sessions[user_id]


def reset_user_session(user_id: int):
    """Foydalanuvchi sessiyasini tozalash va rejalashtirilgan vazifalarni bekor qilish"""
    sess = get_user_session(user_id)
    if sess.get('update_task') and not sess['update_task'].done():
        sess['update_task'].cancel()
    sess['items'].clear()
    sess['menu_msg_id'] = None
    sess['update_task'] = None
    sess['waiting_for_password'] = False
    sess['archive_paths'] = None
    sess['extract_dir'] = None
    sess['user_dir'] = None
    sess['progress_msg_id'] = None


def format_file_size(size_bytes: int) -> str:
    """Fayl hajmini chiroyli formatda ko'rsatish (KB, MB)"""
    if not size_bytes or size_bytes <= 0:
        return ""
    if size_bytes < 1024 * 1024:
        return f"({size_bytes / 1024:.1f} KB)"
    return f"({size_bytes / (1024 * 1024):.1f} MB)"


def build_progress_bar(current: int, total: int, title: str, current_info: str = "", start_time: float = None) -> str:
    """Animatsiyali chiroyli progress bar matnini hosil qiladi"""
    if total <= 0:
        percent = 100
    else:
        percent = int((current / total) * 100)
    percent = min(max(percent, 0), 100)
    
    filled = int(percent / 10)
    bar = "█" * filled + "░" * (10 - filled)
    
    eta_str = ""
    if start_time and current > 0 and current < total:
        elapsed = time.time() - start_time
        speed = current / elapsed
        remaining = (total - current) / speed if speed > 0 else 0
        eta_str = f"\n⏱ <b>Taxminiy qolgan vaqt:</b> ~{int(remaining) + 1} soniya"
        
    info_str = f"\n📁 <b>Joriy:</b> <code>{current_info}</code>" if current_info else ""
    
    return (
        f"⚙️ <b>{title}</b>\n\n"
        f"[{bar}] <b>{percent}%</b> ({current}/{total})\n"
        f"{info_str}"
        f"{eta_str}"
    )


async def _check_single_channel(bot, user_id: int, ch: tuple):
    """Bitta kanalni parallel tekshirish"""
    ch_id = ch[1]
    name = ch[0]
    try:
        member = await bot.get_chat_member(chat_id=ch_id, user_id=user_id)
        is_sub = member.status in ['member', 'administrator', 'creator']
        if is_sub:
            return None
        return ch
    except Exception as e:
        logger.warning(f"Kanalga a'zolikni tekshirishda ogohlantirish ({ch_id}): {e}")
        return None


async def check_user_subscription(bot, user_id: int) -> tuple[bool, list]:
    """To'g'ridan-to'g'ri Ma'lumotlar bazasidan tekshirish"""
    channels = db.get_channels_from_db()
    if not channels:
        return True, []

    tasks = [_check_single_channel(bot, user_id, ch) for ch in channels]
    results = await asyncio.gather(*tasks)
    not_subscribed = [r for r in results if r is not None]

    if not_subscribed:
        return False, not_subscribed

    return True, []


# =========================================================================
# GLOBAL ERROR HANDLER
# =========================================================================

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kutilmagan xatoliklarni ushlash"""
    err = context.error
    err_str = str(err)
    if "Query is too old" in err_str or "httpx.ReadError" in err_str or "ConnectError" in err_str:
        logger.warning(f"[Tarmoq / Vaqt o'tishi]: {type(err).__name__}: {err}")
        return

    logger.error(f"[XATOLIK YUZ BERDI] {type(err).__name__}: {err}", exc_info=err)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ <b>Kutilmagan xatolik yoki internet uzilishi yuz berdi. Iltimos, qaytadan urinib ko'ring.</b>",
                parse_mode='HTML'
            )
        except Exception:
            pass


# =========================================================================
# FOYDALANUVCHI KOMANDALARI VA ASOSIY MANTIQ
# =========================================================================

async def _bg_save_and_log_user(bot, user):
    """Yangi foydalanuvchini fonda bazaga yozish (0 ms kutish)"""
    try:
        is_new = db.select_user(user.id) is None
        db.add_user(user_id=user.id, full_name=user.full_name, username=user.username)
        if is_new and LOG_CHANNEL_ID and LOG_CHANNEL_ID.strip() != "":
            total_users = db.count_users()
            log_text = (
                f"<b>🆕 Yangi foydalanuvchi!</b>\n\n"
                f"👤 <b>Ism:</b> {user.mention_html()}\n"
                f"🌐 <b>Username:</b> @{user.username if user.username else 'yoq'}\n"
                f"🆔 <b>ID:</b> <code>{user.id}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━\n"
                f"👥 <b>Jami foydalanuvchilar:</b> {total_users} ta"
            )
            await bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_text, parse_mode='HTML')
    except Exception as e:
        logger.warning(f"Background user save error: {e}")


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start buyrug'i - LAHZADA (<20ms) javob qaytaradi"""
    user = update.effective_user
    user_id = user.id

    sess = get_user_session(user_id)
    if sess.get('user_dir'):
        cleanup_user_files(sess['user_dir'])
    reset_user_session(user_id)

    # DARHOL javob qaytarish (obuna tekshiruvi kutib turilmaydi)
    await update.message.reply_html(text=WELCOME_MESSAGE)

    # Bazaga yozish fonda ishlaydi
    asyncio.create_task(_bg_save_and_log_user(context.bot, user))


async def check_subscription_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """'A'zolikni tekshirish' inline tugmasi bosilganda"""
    query = update.callback_query
    try:
        await query.answer("Tekshirilmoqda...")
    except Exception:
        pass

    user_id = query.from_user.id
    is_subbed, unsubs = await check_user_subscription(context.bot, user_id)

    if is_subbed:
        try:
            await query.message.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ <b>Tabriklaymiz, obuna tasdiqlandi!</b>\n\n{WELCOME_MESSAGE}",
            parse_mode='HTML'
        )
    else:
        channels = db.get_channels_from_db()
        try:
            await query.edit_message_text(
                text="❌ <b>Siz hali barcha kanallarga a'zo bo'lmadingiz!</b>\n\nIltimos, quyidagi kanallarga obuna bo'lib, qayta tekshiring:",
                reply_markup=get_subscription_keyboard(channels),
                parse_mode='HTML'
            )
        except Exception:
            pass


async def _delayed_update_menu(bot, user_id: int, chat_id: int):
    """Media group (albom) va ketma-ket yuborilgan fayllarni jamlab bitta xabarda ko'rsatish (Debounce)"""
    try:
        await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        return

    sess = get_user_session(user_id)
    async with sess['lock']:
        if not sess['items']:
            return

        photos = [it for it in sess['items'] if it['type'] == 'photo']
        pdfs = [it for it in sess['items'] if it['type'] == 'pdf']
        archives = [it for it in sess['items'] if it['type'] == 'archive']
        docs = [it for it in sess['items'] if it['type'] == 'doc']

        photo_count = len(photos)
        pdf_count = len(pdfs)
        archive_count = len(archives)
        doc_count = len(docs)

        status_lines = ["📥 <b>Fayllar qabul qilindi:</b>"]
        if photo_count > 0:
            status_lines.append(f"• 🖼 <b>Rasmlar:</b> {photo_count} ta")
        if pdf_count > 0:
            for p in pdfs:
                status_lines.append(f"• 📄 <b>PDF:</b> <code>{p['file_name']}</code> {format_file_size(p['file_size'])}")
        if archive_count > 0:
            for a in archives:
                status_lines.append(f"• 📦 <b>Arxiv:</b> <code>{a['file_name']}</code> {format_file_size(a['file_size'])}")
        if doc_count > 0:
            status_lines.append(f"• 📁 <b>Boshqa hujjatlar:</b> {doc_count} ta")

        status_lines.append("\n<i>Quyidagi amallardan birini tanlang:</i>")
        status_text = "\n".join(status_lines)

        reply_markup = get_file_action_keyboard(photo_count, doc_count, pdf_count, archive_count)

        # Yangi fayllar kelganda eski menyu xabari tepada qolib ketmasligi (va scroll qilish talab etilmasligi) uchun
        # eski menyuni o'chiramiz va yangi menyuni doim chatning ENG PASTIGA (yangi fayllar ostiga) yuboramiz.
        if sess.get('menu_msg_id'):
            try:
                await bot.delete_message(chat_id=chat_id, message_id=sess['menu_msg_id'])
            except Exception:
                pass
            sess['menu_msg_id'] = None

        try:
            sent = await bot.send_message(
                chat_id=chat_id,
                text=status_text,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
            sess['menu_msg_id'] = sent.message_id
            sess['chat_id'] = chat_id
        except Exception as e:
            logger.error(f"Menyu xabarini yuborishda xatolik: {e}")


async def handle_user_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Foydalanuvchi yuborgan rasm, PDF, ZIP, RAR va boshqa fayllarni qabul qilish.
    Media group (albom) va ketma-ket yuborilgan fayllarni bitta xabarda to'plab ko'rsatadi.
    """
    message = update.message
    if not message:
        return

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    sess = get_user_session(user_id)
    sess['chat_id'] = chat_id

    # 1. Rasm yuborilgan bo'lsa
    if message.photo:
        photo = message.photo[-1]
        if not any(it['file_unique_id'] == photo.file_unique_id for it in sess['items']):
            sess['items'].append({
                'type': 'photo',
                'file_id': photo.file_id,
                'file_unique_id': photo.file_unique_id,
                'file_name': f"photo_{photo.file_unique_id}.jpg",
                'file_size': photo.file_size or 0
            })

    # 2. Hujjat / Fayl yuborilgan bo'lsa
    elif message.document:
        doc = message.document
        if not any(it['file_unique_id'] == doc.file_unique_id for it in sess['items']):
            safe_name = "".join(c for c in doc.file_name if c.isalnum() or c in "._- ") if doc.file_name else "file"
            lower_name = safe_name.lower()
            mime = (doc.mime_type or "").lower()

            # Formatni aniqlash
            if lower_name.endswith(('.zip', '.rar', '.tar', '.gz')) or mime in ['application/zip', 'application/x-zip-compressed', 'application/x-rar-compressed', 'application/vnd.rar']:
                f_type = 'archive'
            elif lower_name.endswith('.pdf') or mime == 'application/pdf':
                f_type = 'pdf'
            elif mime.startswith('image/'):
                f_type = 'photo'
            else:
                f_type = 'doc'

            sess['items'].append({
                'type': f_type,
                'file_id': doc.file_id,
                'file_unique_id': doc.file_unique_id,
                'file_name': safe_name,
                'file_size': doc.file_size or 0
            })

    # Agar oldingi yangilanish vazifasi bo'lsa, bekor qilib yangisini boshlaymiz (Debounce)
    if sess.get('update_task') and not sess['update_task'].done():
        sess['update_task'].cancel()

    sess['update_task'] = asyncio.create_task(_delayed_update_menu(context.bot, user_id, chat_id))


# =========================================================================
# FAYLLARNI YUKLAB OLISH VA KONVERTATSIYA HARAKATLARI
# =========================================================================

async def _download_session_items(bot, items: list, user_dir: str, status_msg=None) -> list[str]:
    """Kerakli fayllarni Telegramdan vaqtinchalik papkaga yuklab oladi (Progress Bar bilan)"""
    downloaded_paths = []
    total = len(items)
    dl_start_time = time.time()
    for idx, it in enumerate(items, 1):
        if status_msg:
            prog_text = build_progress_bar(
                current=idx,
                total=total,
                title="Fayllar serverga yuklanmoqda...",
                current_info=it.get('file_name', 'Fayl'),
                start_time=dl_start_time
            )
            try:
                await status_msg.edit_text(prog_text, parse_mode='HTML')
            except Exception:
                pass

        file_obj = await bot.get_file(it['file_id'])
        target_path = os.path.join(user_dir, f"{it['file_unique_id']}_{it['file_name']}")
        await file_obj.download_to_drive(target_path)
        downloaded_paths.append(target_path)
    return downloaded_paths


async def make_pdf_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rasmlarni PDF ga aylantirish (Progress Bar bilan)"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    sess = get_user_session(user_id)

    photo_items = [it for it in sess['items'] if it['type'] == 'photo']
    if not photo_items:
        await query.message.reply_text("❌ Siz hali birorta ham rasm yubormadingiz!")
        return

    sess['menu_msg_id'] = None
    initial_progress = build_progress_bar(
        current=0,
        total=len(photo_items),
        title="Rasmlar yuklab olinmoqda...",
        current_info="Tayyorlanmoqda..."
    )
    try:
        await query.message.edit_text(initial_progress, parse_mode='HTML')
        progress_msg = query.message
    except Exception:
        progress_msg = await query.message.reply_html(initial_progress)
    user_dir = get_user_dir(DOCUMENTS_DIR, user_id)

    # 1. Rasmlarni yuklab olish
    image_paths = await _download_session_items(context.bot, photo_items, user_dir, progress_msg)

    # 2. PDF yaratish
    start_time = time.time()
    try:
        await progress_msg.edit_text(
            build_progress_bar(
                current=len(image_paths),
                total=len(image_paths),
                title="PDF hujjati yaratilmoqda...",
                current_info="Sifatli siqilmoqda...",
                start_time=start_time
            ),
            parse_mode='HTML'
        )
    except Exception:
        pass

    output_pdf = os.path.join(user_dir, f"converted_{user_id}_{int(time.time())}.pdf")
    loop = asyncio.get_running_loop()
    success = await loop.run_in_executor(None, convert_images_to_pdf, image_paths, output_pdf)

    if success and os.path.exists(output_pdf):
        try:
            await progress_msg.edit_text(
                build_progress_bar(
                    current=len(image_paths),
                    total=len(image_paths),
                    title="PDF hujjati yuborilmoqda...",
                    current_info="Telegramga yuklanmoqda...",
                    start_time=start_time
                ),
                parse_mode='HTML'
            )
        except Exception:
            pass

        with open(output_pdf, 'rb') as pdf_file:
            await context.bot.send_document(
                chat_id=user_id,
                document=pdf_file,
                filename=f"Convertor_{len(image_paths)}_rasm.pdf",
                caption=f"✅ <b>Sizning PDF hujjatingiz tayyor bo'ldi!</b>\n\n📄 <b>Jami sahifalar:</b> {len(image_paths)} ta\n🤖 @convertorai_bot",
                parse_mode='HTML',
                read_timeout=120.0,
                write_timeout=120.0
            )
        try:
            await progress_msg.delete()
        except Exception:
            pass
    else:
        await progress_msg.edit_text("❌ PDF yaratishda xatolik yuz berdi.")

    cleanup_user_files(user_dir)
    reset_user_session(user_id)


async def make_pdf_to_images_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """PDF ni rasmlarga ajratish va FOTO-ALBOM (group) qilib yuborish (Progress Bar bilan)"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    sess = get_user_session(user_id)

    pdf_items = [it for it in sess['items'] if it['type'] == 'pdf']
    if not pdf_items:
        await query.message.reply_text("❌ Siz hali birorta ham PDF fayl yubormadingiz!")
        return

    sess['menu_msg_id'] = None
    initial_progress = build_progress_bar(
        current=0,
        total=len(pdf_items),
        title="PDF yuklab olinmoqda...",
        current_info="Tayyorlanmoqda..."
    )
    try:
        await query.message.edit_text(initial_progress, parse_mode='HTML')
        progress_msg = query.message
    except Exception:
        progress_msg = await query.message.reply_html(initial_progress)
    user_dir = get_user_dir(DOCUMENTS_DIR, user_id)

    # 1. PDF fayllarni yuklab olish
    pdf_paths = await _download_session_items(context.bot, pdf_items, user_dir, progress_msg)

    # 2. Sahifalarni JPEG ga aylantirish
    start_time = time.time()
    try:
        await progress_msg.edit_text(
            build_progress_bar(
                current=len(pdf_paths),
                total=len(pdf_paths),
                title="PDF sahifalari ajratilmoqda...",
                current_info="Varaqlar ochilmoqda...",
                start_time=start_time
            ),
            parse_mode='HTML'
        )
    except Exception:
        pass

    loop = asyncio.get_running_loop()
    image_paths = await loop.run_in_executor(None, convert_pdf_to_images, pdf_paths, user_dir)

    if image_paths:
        total_imgs = len(image_paths)
        logger.info(f"[PDF to Image Album] User {user_id} uchun {total_imgs} ta rasm yaratildi.")

        # 3. Rasmlarni 10 talik FOTO-ALBOM guruhlariga bo'lib yuborish
        for i in range(0, total_imgs, 10):
            chunk = image_paths[i:i+10]
            
            # Progress barni yangilash
            prog_text = build_progress_bar(
                current=min(i + 10, total_imgs),
                total=total_imgs,
                title="Rasmlar albomi yuborilmoqda...",
                current_info=f"{i+1}-{min(i+10, total_imgs)}-sahifalar",
                start_time=start_time
            )
            try:
                await progress_msg.edit_text(prog_text, parse_mode='HTML')
            except Exception:
                pass

            if len(chunk) == 1:
                with open(chunk[0], 'rb') as f:
                    await context.bot.send_photo(
                        chat_id=user_id,
                        photo=f,
                        caption=f"📄 {i+1}-sahifa"
                    )
            else:
                media = []
                opened_files = []
                for idx, p in enumerate(chunk):
                    f = open(p, 'rb')
                    opened_files.append(f)
                    caption = f"📄 {i+1}-{min(i+10, total_imgs)}-sahifalar" if idx == 0 else None
                    media.append(InputMediaPhoto(media=f, caption=caption))
                try:
                    await context.bot.send_media_group(
                        chat_id=user_id,
                        media=media,
                        read_timeout=120.0,
                        write_timeout=120.0
                    )
                finally:
                    for f in opened_files:
                        try:
                            f.close()
                        except Exception:
                            pass
            await asyncio.sleep(0.4)

        try:
            await progress_msg.delete()
        except Exception:
            pass
        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ <b>Muvaffaqiyatli yakunlandi!</b>\nJami: {total_imgs} ta sahifa sifatli rasm albomi ko'rinishida yuborildi.",
            parse_mode='HTML'
        )
    else:
        await progress_msg.edit_text("❌ PDF faylni rasmlarga ajratishda xatolik yuz berdi.")

    cleanup_user_files(user_dir)
    reset_user_session(user_id)


async def _send_extracted_files(bot, user_id: int, extracted_items: list[dict], progress_msg, user_dir: str, sess: dict):
    """
    Chiqarilgan barcha fayllarni Telegram orqali sifatli albom va hujjatlar ko'rinishida yuborish
    """
    total = len(extracted_items)
    start_time = time.time()
    logger.info(f"[Unzip] User {user_id} uchun {total} ta fayl chiqarildi.")

    # Rasmlar va Hujjatlarni guruhlash
    image_exts = ('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif')
    raw_images = [it for it in extracted_items if it['file_name'].lower().endswith(image_exts)]
    other_files = [it for it in extracted_items if not it['file_name'].lower().endswith(image_exts)]

    # 0 baytli rasmlar bo'lsa, ularni Photo media group ga qo'shib bo'lmaydi, other_files ga o'tkazamiz
    images = []
    for img in raw_images:
        if os.path.exists(img['full_path']) and os.path.getsize(img['full_path']) > 0:
            images.append(img)
        else:
            other_files.append(img)

    current_sent = 0
    successfully_sent = 0

    # 1. Rasmlarni Photo Media Group qilib papkasi bilan yuboramiz
    if images:
        for i in range(0, len(images), 10):
            chunk = images[i:i+10]
            current_sent += len(chunk)

            # Jonli Progress Bar yangilanishi
            prog_text = build_progress_bar(
                current=current_sent,
                total=total,
                title="Fayllar yuborilmoqda (Rasmlar)...",
                current_info=f"Papka: {chunk[0]['rel_dir']}",
                start_time=start_time
            )
            try:
                await progress_msg.edit_text(prog_text, parse_mode='HTML')
            except Exception:
                pass

            try:
                if len(chunk) == 1:
                    item = chunk[0]
                    with open(item['full_path'], 'rb') as f:
                        caption = f"📁 <b>Papka:</b> <code>{item['rel_dir']}</code>\n🖼 <b>Rasm:</b> {item['file_name']}"
                        await bot.send_photo(
                            chat_id=user_id,
                            photo=f,
                            caption=caption,
                            parse_mode='HTML'
                        )
                    successfully_sent += 1
                else:
                    media = []
                    opened_files = []
                    for idx, item in enumerate(chunk):
                        f = open(item['full_path'], 'rb')
                        opened_files.append(f)
                        caption = f"📁 <b>Papka:</b> <code>{item['rel_dir']}</code> ({len(chunk)} ta rasm)" if idx == 0 else None
                        media.append(InputMediaPhoto(media=f, caption=caption, parse_mode='HTML'))
                    try:
                        await bot.send_media_group(
                            chat_id=user_id,
                            media=media,
                            read_timeout=120.0,
                            write_timeout=120.0
                        )
                        successfully_sent += len(chunk)
                    finally:
                        for f in opened_files:
                            try:
                                f.close()
                            except Exception:
                                pass
            except RetryAfter as ra:
                logger.warning(f"Telegram rate limit: {ra.retry_after}s kutish...")
                await asyncio.sleep(ra.retry_after + 1)
            except Exception as img_err:
                logger.error(f"Rasmlarni guruhlab yuborishda xatolik: {img_err}")
                for item in chunk:
                    try:
                        with open(item['full_path'], 'rb') as f:
                            await bot.send_document(
                                chat_id=user_id,
                                document=f,
                                filename=item['file_name'],
                                caption=f"📁 <b>Papka:</b> <code>{item['rel_dir']}</code>\n🖼 {item['file_name']}",
                                parse_mode='HTML'
                            )
                        successfully_sent += 1
                    except Exception as e_ind:
                        logger.error(f"Alohida rasm yuborish xatosi: {e_ind}")
            await asyncio.sleep(0.4)

    # 2. Boshqa fayllarni (hujjat, audio, video, kod fayllar) yuboramiz
    for item in other_files:
        current_sent += 1
        
        # Har 3 ta faylda progressni yangilash
        if current_sent % 3 == 0 or current_sent == total:
            prog_text = build_progress_bar(
                current=current_sent,
                total=total,
                title="Hujjat va fayllar yuborilmoqda...",
                current_info=f"{item['rel_dir']}/{item['file_name']}",
                start_time=start_time
            )
            try:
                await progress_msg.edit_text(prog_text, parse_mode='HTML')
            except Exception:
                pass

        file_path = item['full_path']
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        caption = f"📁 <b>Papka:</b> <code>{item['rel_dir']}</code>\n📄 <b>Fayl:</b> {item['file_name']}"

        # 50 MB dan katta fayllar Telegram bot orqali yuborilmaydi
        if file_size > 50 * 1024 * 1024:
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=f"⚠️ <b>Fayl hajmi 50 MB dan katta:</b>\n📁 <code>{item['rel_dir']}/{item['file_name']}</code> {format_file_size(file_size)}\n<i>Telegram cheklovi tufayli bot bu faylni yubora olmaydi.</i>",
                    parse_mode='HTML'
                )
            except Exception:
                pass
            continue

        try:
            if file_size == 0:
                # 0 baytli bo'sh faylni Telegram qabul qilmaydi (File must be non-empty).
                # Shuning uchun 1 baytli bo'shliq bilan xavfsiz jo'natamiz
                with io.BytesIO(b" ") as f:
                    await bot.send_document(
                        chat_id=user_id,
                        document=f,
                        filename=item['file_name'],
                        caption=f"{caption}\nℹ️ <i>(Bo'sh fayl, 0 bayt)</i>",
                        parse_mode='HTML',
                        read_timeout=120.0,
                        write_timeout=120.0
                    )
            else:
                with open(file_path, 'rb') as f:
                    await bot.send_document(
                        chat_id=user_id,
                        document=f,
                        filename=item['file_name'],
                        caption=caption,
                        parse_mode='HTML',
                        read_timeout=120.0,
                        write_timeout=120.0
                    )
            successfully_sent += 1
        except RetryAfter as ra:
            logger.warning(f"Telegram flood limit: {ra.retry_after}s kutamiz...")
            await asyncio.sleep(ra.retry_after + 1)
            try:
                if file_size == 0:
                    with io.BytesIO(b" ") as f:
                        await bot.send_document(
                            chat_id=user_id,
                            document=f,
                            filename=item['file_name'],
                            caption=f"{caption}\nℹ️ <i>(Bo'sh fayl, 0 bayt)</i>",
                            parse_mode='HTML',
                            read_timeout=120.0,
                            write_timeout=120.0
                        )
                else:
                    with open(file_path, 'rb') as f:
                        await bot.send_document(
                            chat_id=user_id,
                            document=f,
                            filename=item['file_name'],
                            caption=caption,
                            parse_mode='HTML',
                            read_timeout=120.0,
                            write_timeout=120.0
                        )
                successfully_sent += 1
            except Exception as retry_err:
                logger.error(f"Fayl qayta yuborilmadi ({item['file_name']}): {retry_err}")
        except Exception as file_err:
            logger.error(f"Fayl yuborishda xatolik ({item['file_name']}): {file_err}")
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=f"⚠️ <b>{item['file_name']}</b> faylini yuborib bo'lmadi: <i>{file_err}</i>",
                    parse_mode='HTML'
                )
            except Exception:
                pass
        
        await asyncio.sleep(0.35)

    try:
        await progress_msg.delete()
    except Exception:
        pass

    elapsed = int(time.time() - start_time)
    await bot.send_message(
        chat_id=user_id,
        text=(
            f"✅ <b>Arxiv muvaffaqiyatli ochildi!</b>\n\n"
            f"📊 <b>Jami chiqarilgan fayllar:</b> {total} ta\n"
            f"📤 <b>Yuborildi:</b> {successfully_sent} ta\n"
            f"• 🖼 <b>Rasmlar:</b> {len(images)} ta (Foto-albom)\n"
            f"• 📁 <b>Hujjat va fayllar:</b> {len(other_files)} ta\n"
            f"⏱ <b>Umumiy sarflangan vaqt:</b> {elapsed} soniya"
        ),
        parse_mode='HTML'
    )

    cleanup_user_files(user_dir)
    reset_user_session(user_id)


async def unzip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    ZIP / RAR arxivni ochish (Unzip), agar parol bo'lsa parolini so'rash va
    foizli jonli Progress Bar orqali yuborish.
    """
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    sess = get_user_session(user_id)

    archive_items = [it for it in sess['items'] if it['type'] == 'archive']
    if not archive_items:
        await query.message.reply_text("❌ Siz hali birorta ham ZIP yoki RAR arxiv yubormadingiz!")
        return

    sess['menu_msg_id'] = None
    initial_progress = build_progress_bar(
        current=0,
        total=len(archive_items),
        title="Arxiv yuklanmoqda...",
        current_info="Tayyorlanmoqda..."
    )
    try:
        await query.message.edit_text(initial_progress, parse_mode='HTML')
        progress_msg = query.message
    except Exception:
        progress_msg = await query.message.reply_html(initial_progress)
    user_dir = get_user_dir(DOCUMENTS_DIR, user_id)

    # 1. Arxiv faylni Telegramdan yuklab olish
    archive_paths = await _download_session_items(context.bot, archive_items, user_dir, progress_msg)

    # 2. Arxivni ochish (Unzip / Unrar)
    extract_start = time.time()
    try:
        await progress_msg.edit_text(
            build_progress_bar(
                current=len(archive_paths),
                total=len(archive_paths),
                title="Arxiv ochilmoqda (Unzip/Unrar)...",
                current_info="Fayllar ajratilmoqda...",
                start_time=extract_start
            ),
            parse_mode='HTML'
        )
    except Exception:
        pass

    extract_dir = os.path.join(user_dir, "extracted")
    os.makedirs(extract_dir, exist_ok=True)

    loop = asyncio.get_running_loop()
    extracted_items = []

    try:
        for arch_p in archive_paths:
            res = await loop.run_in_executor(None, extract_archive, arch_p, extract_dir, None)
            extracted_items.extend(res)
    except ArchivePasswordRequired:
        # Parol so'rash holatiga o'tkazish
        sess['waiting_for_password'] = True
        sess['archive_paths'] = archive_paths
        sess['extract_dir'] = extract_dir
        sess['user_dir'] = user_dir
        sess['progress_msg_id'] = progress_msg.message_id

        await progress_msg.edit_text(
            "🔐 <b>Ushbu arxiv parol bilan himoyalangan!</b>\n\n"
            "Iltimos, arxivni ochish uchun uning <b>parolini yozib yuboring</b>:\n\n"
            "<i>(Bekor qilish uchun /cancel deb yozing)</i>",
            parse_mode='HTML'
        )
        return
    except Exception as e:
        logger.error(f"Archive extract error: {e}", exc_info=True)
        await progress_msg.edit_text(f"❌ <b>Arxivni ochishda xatolik yuz berdi:</b> {e}", parse_mode='HTML')
        cleanup_user_files(user_dir)
        reset_user_session(user_id)
        return

    if extracted_items:
        await _send_extracted_files(context.bot, user_id, extracted_items, progress_msg, user_dir, sess)
    else:
        await progress_msg.edit_text("❌ Arxivni ochishda xatolik yuz berdi yoki arxiv bo'sh.")
        cleanup_user_files(user_dir)
        reset_user_session(user_id)


async def make_zip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Barcha fayllarni ZIP ga arxivlash (Progress Bar bilan)"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    sess = get_user_session(user_id)

    if not sess['items']:
        await query.message.reply_text("❌ Siz hech qanday fayl yubormadingiz!")
        return

    sess['menu_msg_id'] = None
    initial_progress = build_progress_bar(
        current=0,
        total=len(sess['items']),
        title="Fayllar yuklanmoqda...",
        current_info="Tayyorlanmoqda..."
    )
    try:
        await query.message.edit_text(initial_progress, parse_mode='HTML')
        progress_msg = query.message
    except Exception:
        progress_msg = await query.message.reply_html(initial_progress)
    user_dir = get_user_dir(DOCUMENTS_DIR, user_id)

    # 1. Fayllarni yuklab olish
    downloaded_paths = await _download_session_items(context.bot, sess['items'], user_dir, progress_msg)

    # 2. ZIP yaratish
    zip_start = time.time()
    try:
        await progress_msg.edit_text(
            build_progress_bar(
                current=len(downloaded_paths),
                total=len(downloaded_paths),
                title="ZIP arxiv tayyorlanmoqda...",
                current_info="Fayllar siqilmoqda...",
                start_time=zip_start
            ),
            parse_mode='HTML'
        )
    except Exception:
        pass

    output_zip = os.path.join(user_dir, f"archive_{user_id}_{int(time.time())}.zip")
    loop = asyncio.get_running_loop()
    success = await loop.run_in_executor(None, create_zip_archive, downloaded_paths, output_zip)

    if success and os.path.exists(output_zip):
        try:
            await progress_msg.edit_text(
                build_progress_bar(
                    current=len(downloaded_paths),
                    total=len(downloaded_paths),
                    title="ZIP arxiv yuborilmoqda...",
                    current_info="Telegramga yuklanmoqda...",
                    start_time=zip_start
                ),
                parse_mode='HTML'
            )
        except Exception:
            pass

        with open(output_zip, 'rb') as zip_file:
            await context.bot.send_document(
                chat_id=user_id,
                document=zip_file,
                filename=f"Archive_{int(time.time())}.zip",
                caption=f"✅ <b>Barcha fayllar ZIP arxiviga muvaffaqiyatli jamlandi!</b>\n\n📦 <b>Jami fayllar:</b> {len(downloaded_paths)} ta\n🤖 @convertorai_bot",
                parse_mode='HTML',
                read_timeout=120.0,
                write_timeout=120.0
            )
        try:
            await progress_msg.delete()
        except Exception:
            pass
    else:
        await progress_msg.edit_text("❌ ZIP arxiv yaratishda xatolik yuz berdi.")

    cleanup_user_files(user_dir)
    reset_user_session(user_id)


async def clear_files_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yuklangan fayllarni tozalash va bekor qilish"""
    query = update.callback_query
    try:
        await query.answer("Tozalandi")
    except Exception:
        pass
    user_id = query.from_user.id
    sess = get_user_session(user_id)

    user_dir = get_user_dir(DOCUMENTS_DIR, user_id)
    cleanup_user_files(user_dir)
    reset_user_session(user_id)

    try:
        await query.edit_message_text("🗑 <b>Barcha yuklangan fayllar tozalandi.</b>\n\nBoshlash uchun yangi fayllarni yuborishingiz mumkin.", parse_mode='HTML')
    except Exception:
        pass


# =========================================================================
# ADMIN BOSHQARUV PANELI
# =========================================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/admin buyrug'i"""
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        return

    await update.message.reply_html(
        text=f"👑 <b>Assalomu alaykum, Admin {update.effective_user.mention_html()}!</b>\n\nBoshqaruv paneliga xush kelibsiz:",
        reply_markup=get_admin_main_keyboard()
    )


async def admin_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin bosh menyusiga qaytish"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if query.from_user.id not in ADMINS:
        return

    try:
        await query.edit_message_text(
            text="👑 <b>Admin Boshqaruv Paneli:</b>",
            reply_markup=get_admin_main_keyboard(),
            parse_mode='HTML'
        )
    except Exception:
        pass


async def admin_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot statistikasini ko'rsatish"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if query.from_user.id not in ADMINS:
        return

    total_users = db.count_users()
    active, block = db.get_status()

    db_size = "0 KB"
    if os.path.exists(DATABASE_PATH):
        db_size = f"{os.path.getsize(DATABASE_PATH) / 1024:.1f} KB"

    text = (
        f"📊 <b>Bot Statistikasi</b>\n\n"
        f"👥 <b>Jami foydalanuvchilar:</b> {total_users} ta\n"
        f"✅ <b>Aktiv foydalanuvchilar:</b> {active} ta\n"
        f"🚫 <b>Bloklaganlar:</b> {block} ta\n"
        f"💾 <b>Ma'lumotlar bazasi hajmi:</b> {db_size}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ <b>Hozirgi vaqt:</b> {datetime.now(pytz.timezone('Asia/Tashkent')).strftime('%d/%m/%Y %H:%M:%S')}"
    )

    await query.edit_message_text(text=text, reply_markup=get_back_keyboard(), parse_mode='HTML')


async def admin_list_channels_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Majburiy a'zolik kanallari ro'yxatini ko'rsatish"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if query.from_user.id not in ADMINS:
        return

    channels = db.get_channels_from_db()
    if not channels:
        await query.edit_message_text(
            text="📢 <b>Hozirda hech qanday majburiy obuna kanali qo'shilmagan.</b>",
            reply_markup=get_back_keyboard(),
            parse_mode='HTML'
        )
        return

    text = "📋 <b>Majburiy a'zolik kanallari ro'yxati:</b>\n<i>O'chirish uchun kanal yonidagi tugmani bosing:</i>"
    await query.edit_message_text(
        text=text,
        reply_markup=get_channels_manage_keyboard(channels),
        parse_mode='HTML'
    )


async def admin_delete_channel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kanalni o'chirish callback"""
    query = update.callback_query
    try:
        await query.answer("Kanal o'chirildi")
    except Exception:
        pass
    if query.from_user.id not in ADMINS:
        return

    channel_db_id = int(query.data.replace("del_channel_", ""))
    db.delete_channel_by_id(channel_db_id)

    channels = db.get_channels_from_db()
    if not channels:
        await query.edit_message_text(
            text="✅ <b>Kanal o'chirildi. Hozirda kanallar qolmadi.</b>",
            reply_markup=get_back_keyboard(),
            parse_mode='HTML'
        )
    else:
        await query.edit_message_text(
            text="✅ <b>Kanal muvaffaqiyatli o'chirildi!</b>\n\nQolgan kanallar ro'yxati:",
            reply_markup=get_channels_manage_keyboard(channels),
            parse_mode='HTML'
        )


async def admin_backup_db_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """SQLite bazasini yuklab olish"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if query.from_user.id not in ADMINS:
        return

    if os.path.exists(DATABASE_PATH):
        with open(DATABASE_PATH, 'rb') as f:
            await context.bot.send_document(
                chat_id=query.from_user.id,
                document=f,
                filename="database_backup.db",
                caption="💾 <b>SQLite ma'lumotlar bazasi nusxasi</b>",
                parse_mode='HTML',
                read_timeout=120.0,
                write_timeout=120.0
            )
    else:
        await query.message.reply_text("❌ Baza fayli topilmadi!")


async def admin_backup_xlsx_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Excel formatida foydalanuvchilar bazasini yuklash"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if query.from_user.id not in ADMINS:
        return

    excel_file = "users_export.xlsx"
    db.export_users_to_excel(excel_file)

    if os.path.exists(excel_file):
        with open(excel_file, 'rb') as f:
            await context.bot.send_document(
                chat_id=query.from_user.id,
                document=f,
                filename=f"Users_{datetime.now().strftime('%Y_%m_%d')}.xlsx",
                caption="📑 <b>Foydalanuvchilar ro'yxati (Excel jadval)</b>",
                parse_mode='HTML',
                read_timeout=120.0,
                write_timeout=120.0
            )
        try:
            os.remove(excel_file)
        except Exception:
            pass


# =========================================================================
# ADMIN XABAR YUBORISH (BROADCAST) CONVERSATION
# =========================================================================

async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast jarayonini boshlash"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if query.from_user.id not in ADMINS:
        return ConversationHandler.END

    await query.edit_message_text(
        text="📨 <b>Barcha foydalanuvchilarga yuboriladigan xabarni yuboring:</b>\n\n<i>(Matn, rasm, video, audio yoki forward xabar yuborishingiz mumkin. Bekor qilish uchun /cancel deb yozing)</i>",
        parse_mode='HTML'
    )
    return ADMIN_BROADCAST_STATE


async def execute_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xabarni barcha foydalanuvchilarga tarqatish"""
    user_id = update.effective_user.id
    if user_id not in ADMINS:
        return ConversationHandler.END

    message = update.message
    users = db.select_all_users()
    total_users = len(users)

    if total_users == 0:
        await message.reply_text("❌ Bazada birorta ham foydalanuvchi topilmadi.")
        return ConversationHandler.END

    status_msg = await message.reply_html(
        f"🚀 <b>Xabar yuborish boshlandi...</b>\n\n"
        f"👥 Jami qabul qiluvchilar: {total_users} ta"
    )

    success = 0
    blocked = 0
    start_time = time.time()

    for idx, u in enumerate(users):
        target_id = u[0]
        try:
            await context.bot.copy_message(
                chat_id=target_id,
                from_chat_id=message.chat_id,
                message_id=message.message_id
            )
            success += 1
        except Exception as e:
            blocked += 1
            logger.debug(f"User {target_id} xabar olmadi: {e}")

        await asyncio.sleep(0.04)

        if (idx + 1) % 100 == 0:
            try:
                await status_msg.edit_text(
                    f"🚀 <b>Xabar yuborilmoqda...</b>\n\n"
                    f"📤 Yuborildi: {success}\n"
                    f"🚫 Yetib bormadi: {blocked}\n"
                    f"📊 Progress: {idx + 1}/{total_users}",
                    parse_mode='HTML'
                )
            except Exception:
                pass

    elapsed = int(time.time() - start_time)
    db.update_status(active=success, block=blocked)

    report_text = (
        f"✅ <b>Xabar tarqatish yakunlandi!</b>\n\n"
        f"👥 <b>Jami foydalanuvchilar:</b> {total_users} ta\n"
        f"📤 <b>Muvaffaqiyatli yetkazildi:</b> {success} ta\n"
        f"🚫 <b>Bloklagan / Yetib bormagan:</b> {blocked} ta\n"
        f"⏱ <b>Sarflangan vaqt:</b> {elapsed} soniya"
    )

    await status_msg.edit_text(text=report_text, reply_markup=get_back_keyboard(), parse_mode='HTML')
    return ConversationHandler.END


async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Amalni bekor qilish"""
    user_id = update.effective_user.id
    sess = get_user_session(user_id)
    if sess.get('waiting_for_password') or sess.get('items'):
        user_dir = sess.get('user_dir') or get_user_dir(DOCUMENTS_DIR, user_id)
        cleanup_user_files(user_dir)
        reset_user_session(user_id)
        await update.message.reply_html("❌ <b>Amal bekor qilindi va fayllar tozalandi.</b>")
        return ConversationHandler.END

    await update.message.reply_html(
        "❌ <b>Amal bekor qilindi.</b>",
        reply_markup=get_admin_main_keyboard() if user_id in ADMINS else None
    )
    return ConversationHandler.END


async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Oddiy matnli xabarlarni qayta ishlash (masalan, arxiv paroli kiritilganda)"""
    user_id = update.effective_user.id
    sess = get_user_session(user_id)

    # Agar foydalanuvchidan arxiv paroli kutilayotgan bo'lsa
    if sess.get('waiting_for_password'):
        pwd = update.message.text.strip()
        archive_paths = sess.get('archive_paths', [])
        extract_dir = sess.get('extract_dir')
        user_dir = sess.get('user_dir') or get_user_dir(DOCUMENTS_DIR, user_id)

        if not archive_paths or not extract_dir:
            reset_user_session(user_id)
            await update.message.reply_html("❌ <b>Arxiv fayllari topilmadi.</b> Iltimos, arxivni qaytadan yuboring.")
            return

        progress_msg = await update.message.reply_html(
            build_progress_bar(
                current=0,
                total=len(archive_paths),
                title="Parol tekshirilmoqda...",
                current_info="Arxiv ochilmoqda..."
            )
        )
        loop = asyncio.get_running_loop()
        extracted_items = []

        try:
            for arch_p in archive_paths:
                res = await loop.run_in_executor(None, extract_archive, arch_p, extract_dir, pwd)
                extracted_items.extend(res)
        except ArchiveWrongPassword:
            try:
                await progress_msg.delete()
            except Exception:
                pass
            await update.message.reply_html(
                "❌ <b>Noto'g'ri parol kiritildi!</b>\n\n"
                "Iltimos, to'g'ri parolni qaytadan kiriting:\n"
                "<i>(Bekor qilish uchun /cancel deb yozing)</i>"
            )
            return
        except Exception as e:
            try:
                await progress_msg.delete()
            except Exception:
                pass
            await update.message.reply_html(f"❌ <b>Arxivni ochishda xatolik yuz berdi:</b> {e}")
            cleanup_user_files(user_dir)
            reset_user_session(user_id)
            return

        if extracted_items:
            await _send_extracted_files(context.bot, user_id, extracted_items, progress_msg, user_dir, sess)
        else:
            await progress_msg.edit_text("❌ Arxivni ochishda xatolik yuz berdi yoki arxiv bo'sh.")
            cleanup_user_files(user_dir)
            reset_user_session(user_id)
        return

    # Boshqa hollarda foydalanuvchiga yo'riqnoma
    await update.message.reply_html(
        "ℹ️ <b>Faylni konvertatsiya qilish yoki arxivni ochish uchun:</b>\n\n"
        "1. Rasmlar, PDF yoki ZIP/RAR arxiv yuboring.\n"
        "2. Chiqqan menyudan kerakli amalni tanlang."
    )


# =========================================================================
# ADMIN KANAL QO'SHISH CONVERSATION
# =========================================================================

async def start_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kanal qo'shishni boshlash"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if query.from_user.id not in ADMINS:
        return ConversationHandler.END

    await query.edit_message_text(
        text="➕ <b>1-Qadam:</b> Kanal nomini kiriting (masalan: <i>Rasmiy Kanal</i>):\n\n<i>Bekor qilish uchun /cancel deb yozing</i>",
        parse_mode='HTML'
    )
    return ADMIN_ADD_CH_NAME


async def receive_channel_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_ch_name'] = update.message.text.strip()
    await update.message.reply_html(
        "🆔 <b>2-Qadam:</b> Kanal ID yoki Usernamesini kiriting (masalan: <code>-1001234567890</code> yoki <code>@kanal_nomi</code>):\n\n"
        "<i>Eslatma: Bot ushbu kanalda administrator bo'lishi shart!</i>"
    )
    return ADMIN_ADD_CH_ID


async def receive_channel_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ch_id = update.message.text.strip()
    context.user_data['new_ch_id'] = ch_id
    await update.message.reply_html(
        "🔗 <b>3-Qadam:</b> Kanalga kirish havolasini (link) kiriting (masalan: <code>https://t.me/kanal_nomi</code>):"
    )
    return ADMIN_ADD_CH_LINK


async def receive_channel_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    name = context.user_data.get('new_ch_name')
    ch_id = context.user_data.get('new_ch_id')

    if name and ch_id and link:
        success = db.add_channel(name, ch_id, link)
        if success:
            await update.message.reply_html(
                f"✅ <b>Kanal muvaffaqiyatli qo'shildi!</b>\n\n"
                f"📢 <b>Nomi:</b> {name}\n"
                f"🆔 <b>ID:</b> <code>{ch_id}</code>\n"
                f"🔗 <b>Havola:</b> {link}",
                reply_markup=get_admin_main_keyboard()
            )
        else:
            await update.message.reply_html(
                "❌ Kanalni qo'shishda xatolik yuz berdi.",
                reply_markup=get_admin_main_keyboard()
            )
    context.user_data.clear()
    return ConversationHandler.END


# =========================================================================
# ASOSIY ILOVANI ISHGA TUSHIRISH
# =========================================================================

def main():
    """Botni boshlash funksiyasi"""
    logger.info("Bot ishga tushirilmoqda...")
    
    # Startupda eski fayllarni tozalash
    cleanup_old_files(DOCUMENTS_DIR, max_age_seconds=1800)

    # Katta hajmdagi fayllar va tezkor tarmoq uchun HTTPX Request
    req = HTTPXRequest(
        connection_pool_size=32,
        read_timeout=120.0,
        write_timeout=120.0,
        connect_timeout=30.0,
        pool_timeout=5.0
    )

    application = (
        ApplicationBuilder()
        .token(API_TOKEN)
        .request(req)
        .concurrent_updates(True)
        .build()
    )

    # Xatoliklarni global ushlash
    application.add_error_handler(global_error_handler)

    # 1. Admin Broadcast Conversation
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_broadcast, pattern="^admin_broadcast$")],
        states={
            ADMIN_BROADCAST_STATE: [
                MessageHandler(filters.ALL & ~filters.COMMAND, execute_broadcast)
            ]
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        per_message=False
    )
    application.add_handler(broadcast_conv)

    # 2. Admin Add Channel Conversation
    add_channel_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_add_channel, pattern="^admin_add_channel$")],
        states={
            ADMIN_ADD_CH_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel_name)],
            ADMIN_ADD_CH_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel_id)],
            ADMIN_ADD_CH_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel_link)],
        },
        fallbacks=[CommandHandler("cancel", cancel_command)],
        per_message=False
    )
    application.add_handler(add_channel_conv)

    # Buyruqlar
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("cancel", cancel_command))

    # Foydalanuvchi fayllarini qabul qilish
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_user_files))

    # Matnli xabarlarni qabul qilish (Arxiv paroli va boshqalar)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    # Callback Query Handlers
    application.add_handler(CallbackQueryHandler(check_subscription_callback, pattern="^check_subscription$"))
    application.add_handler(CallbackQueryHandler(make_pdf_callback, pattern="^action_make_pdf$"))
    application.add_handler(CallbackQueryHandler(make_pdf_to_images_callback, pattern="^action_pdf_to_images$"))
    application.add_handler(CallbackQueryHandler(unzip_callback, pattern="^action_unzip$"))
    application.add_handler(CallbackQueryHandler(make_zip_callback, pattern="^action_make_zip$"))
    application.add_handler(CallbackQueryHandler(clear_files_callback, pattern="^action_clear_files$"))

    # Admin Callback Handlers
    application.add_handler(CallbackQueryHandler(admin_back_callback, pattern="^admin_back$"))
    application.add_handler(CallbackQueryHandler(admin_stats_callback, pattern="^admin_stats$"))
    application.add_handler(CallbackQueryHandler(admin_list_channels_callback, pattern="^admin_list_channels$"))
    application.add_handler(CallbackQueryHandler(admin_delete_channel_callback, pattern="^del_channel_"))
    application.add_handler(CallbackQueryHandler(admin_backup_db_callback, pattern="^admin_backup_db$"))
    application.add_handler(CallbackQueryHandler(admin_backup_xlsx_callback, pattern="^admin_backup_xlsx$"))

    logger.info("Bot muvaffaqiyatli ishga tushdi va xabarlarni kutmoqda.")
    application.run_polling(
        drop_pending_updates=True,
        bootstrap_retries=-1,
        poll_interval=1.0,
        timeout=30
    )


if __name__ == '__main__':
    main()
