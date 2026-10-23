import io
import shutil
import os
import sys
import time
import asyncio
import logging
from datetime import datetime, timedelta
import pytz
import html
import warnings

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    InputFile,
    InputMediaPhoto,
    MenuButtonDefault,
    ChatMember
)
from telegram.error import RetryAfter, BadRequest, Forbidden
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
    ChatMemberHandler,
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
    create_custom_pdf,
    merge_pdf_files,
    compress_pdf,
    convert_text_to_pdf,
    convert_media_to_mp3,
    enhance_audio_voice,
    enhance_asmr_audio,
    convert_video_to_gif,
    convert_office_to_pdf,
    extract_text_ocr,
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
    get_admin_export_keyboard,
    get_user_manage_keyboard,
    get_maintenance_keyboard,
    get_sub_admins_keyboard,
    get_broadcast_confirm_keyboard,
    get_back_keyboard,
    get_channels_manage_keyboard,
    get_admin_vip_menu_keyboard,
    get_admin_vip_select_days_keyboard,
    get_admin_ads_keyboard,
    get_admin_ads_duration_keyboard,
    get_admin_ads_skip_link_keyboard,
    get_admin_cache_keyboard,
    get_server_health_keyboard,
    get_platform_stats_keyboard,
    get_cancel_keyboard
)
from session_manager import session_manager

# Parallel og'ir konvertatsiyalar chegarasi (VPS xotirasini tejash uchun)
CONVERT_SEMAPHORE = asyncio.Semaphore(3)

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
TASHKENT_TZ = pytz.timezone('Asia/Tashkent')

# Bot ishga tushgan vaqti (Uptime uchun)
BOT_START_TIME = time.time()

# Admin holatlari (States)
ADMIN_BROADCAST_STATE = 1
ADMIN_ADD_CH_NAME = 2
ADMIN_ADD_CH_ID = 3
ADMIN_ADD_CH_LINK = 4
ADMIN_SEARCH_STATE = 5
ADMIN_DM_STATE = 6
ADMIN_ADD_SUBADMIN_STATE = 7
ADMIN_BROADCAST_CONFIRM = 8
ADMIN_AD_TEXT = 9
ADMIN_AD_LINK = 10
ADMIN_AD_DURATION_CUSTOM = 11
ADMIN_WAITING_VIP_USER = 12
ADMIN_WAITING_VIP_CUSTOM_DAYS = 13


def get_uptime_str() -> str:
    """Botning uzluksiz ishlash vaqtini hisoblash"""
    seconds = int(time.time() - BOT_START_TIME)
    td = timedelta(seconds=seconds)
    days = td.days
    hours, remainder = divmod(td.seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if days > 0:
        return f"{days} kun, {hours} soat, {minutes} daq"
    elif hours > 0:
        return f"{hours} soat, {minutes} daq, {secs} soniya"
    else:
        return f"{minutes} daq, {secs} soniya"


def get_system_health(db_path: str = None) -> dict:
    """Server va tizim salomatligi ko'rsatkichlari (CPU, RAM, Disk, DB, Uptime)"""
    cpu_pct = 0.0
    ram_used_mb = 0
    ram_total_mb = 0
    ram_pct = 0.0
    try:
        import psutil
        cpu_pct = psutil.cpu_percent(interval=0.2)
        mem = psutil.virtual_memory()
        ram_used_mb = round(mem.used / (1024 * 1024), 1)
        ram_total_mb = round(mem.total / (1024 * 1024), 1)
        ram_pct = mem.percent
    except Exception:
        pass

    disk_total_gb = 0
    disk_free_gb = 0
    disk_pct = 0.0
    try:
        root_path = "/" if os.name != 'nt' else os.path.splitdrive(os.getcwd())[0] + "\\"
        total, used, free = shutil.disk_usage(root_path)
        disk_total_gb = round(total / (1024**3), 1)
        disk_free_gb = round(free / (1024**3), 1)
        disk_pct = round((used / total) * 100, 1)
    except Exception:
        pass

    db_size_mb = 0.0
    if db_path and os.path.exists(db_path):
        try:
            db_size_mb = round(os.path.getsize(db_path) / (1024 * 1024), 2)
        except Exception:
            pass

    return {
        'cpu_pct': cpu_pct,
        'ram_used_mb': ram_used_mb,
        'ram_total_mb': ram_total_mb,
        'ram_pct': ram_pct,
        'disk_total_gb': disk_total_gb,
        'disk_free_gb': disk_free_gb,
        'disk_pct': disk_pct,
        'db_size_mb': db_size_mb,
        'uptime': get_uptime_str()
    }


def with_caption_ad(caption: str) -> str:
    """Fayl captioniga faol izoh reklamasini qo'shish"""
    footer = db.get_caption_ad_footer()
    if footer:
        return f"{caption}{footer}"
    return caption

# Majburiy obunani 5 daqiqa (300s) xotirada saqlash keshi
_sub_cache = {}


def get_user_session(user_id: int) -> dict:
    """Foydalanuvchi sessiyasini olish (Redis va Memory bilan sinxron)"""
    if user_id not in session_manager._memory_cache:
        session_manager._memory_cache[user_id] = {
            'items': [],
            'menu_msg_id': None,
            'chat_id': None,
            'update_task': None,
            'lock': session_manager.get_lock(user_id),
            'waiting_for_password': False,
            'archive_paths': None,
            'extract_dir': None,
            'user_dir': None,
            'progress_msg_id': None,
            'ocr_lang': 'uzb+rus+eng'
        }
    elif 'lock' not in session_manager._memory_cache[user_id]:
        session_manager._memory_cache[user_id]['lock'] = session_manager.get_lock(user_id)
    return session_manager._memory_cache[user_id]


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
    if session_manager.redis_client:
        asyncio.create_task(session_manager.reset_session(user_id))


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
        is_sub = member.status in ['member', 'administrator', 'creator', 'restricted']
        if is_sub:
            return None
        return ch
    except Exception as e:
        logger.warning(f"Kanalga a'zolikni tekshirishda ogohlantirish ({ch_id}): {e}")
        return None


async def check_user_subscription(bot, user_id: int) -> tuple[bool, list]:
    """Majburiy kanallarga a'zolikni tekshirish (5 daqiqa kesh, VIP va Admin bypass)"""
    # 1. Adminlar majburiy obunadan ozod
    if db.is_admin(user_id, ADMINS):
        return True, []

    # 2. VIP a'zolar majburiy obunadan ozod
    u = db.select_user(user_id)
    if u and u.get('is_vip') == 1:
        return True, []

    # 3. 5 daqiqalik xotira keshi
    now = time.time()
    if user_id in _sub_cache:
        cache_time, is_sub = _sub_cache[user_id]
        if now - cache_time < 300 and is_sub:
            return True, []

    # 4. Kanallarni bazadan olish
    channels = db.get_channels_from_db()
    if not channels:
        return True, []

    tasks = [_check_single_channel(bot, user_id, ch) for ch in channels]
    results = await asyncio.gather(*tasks)
    not_subscribed = [r for r in results if r is not None]

    if not not_subscribed:
        _sub_cache[user_id] = (now, True)
        return True, []

    return False, not_subscribed


async def check_user_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Ban va Texnik profilaktika (Maintenance) tekshiruvi"""
    user = update.effective_user
    if not user:
        return False
    user_id = user.id

    # 1. Admin ban tekshiruvi
    u = db.select_user(user_id)
    if u:
        is_banned = u.get("is_banned", 0) if isinstance(u, dict) else (u[7] if len(u) > 7 else 0)
        if is_banned == 1:
            if update.callback_query:
                try:
                    await update.callback_query.answer("🚫 Siz bot ma'muriyati tomonidan bloklangansiz!", show_alert=True)
                except Exception:
                    pass
            elif update.effective_message:
                try:
                    await update.effective_message.reply_html("🚫 <b>Siz bot ma'muriyati tomonidan bloklangansiz.</b>")
                except Exception:
                    pass
            return False

    # 2. Texnik rejim tekshiruvi (faqat adminlarga ruxsat beriladi)
    if db.is_maintenance() and not db.is_admin(user_id, ADMINS):
        msg = (
            "🛠 <b>Texnik profilaktika!</b>\n\n"
            "Botda yangilanish va optimallashtirish ishlari olib borilmoqda. "
            "Birozdan so'ng qayta urinib ko'ring."
        )
        if update.callback_query:
            try:
                await update.callback_query.answer("🛠 Botda texnik ishlar olib borilmoqda!", show_alert=True)
            except Exception:
                pass
        elif update.effective_message:
            try:
                await update.effective_message.reply_html(msg)
            except Exception:
                pass
        return False

    return True


async def chat_member_update_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """my_chat_member: Foydalanuvchi botni bloklaganini (kicked) yoki qayta yoqqanini (member) ushlash"""
    result = update.my_chat_member
    if not result:
        return
    user_id = result.from_user.id
    new_status = result.new_chat_member.status
    if new_status in [ChatMember.BANNED, "kicked", "left"]:
        db.set_user_active(user_id, 0)
        logger.info(f"Foydalanuvchi {user_id} botni blokladi (is_active=0).")
    elif new_status in [ChatMember.MEMBER, ChatMember.ADMINISTRATOR, "member", "administrator"]:
        db.set_user_active(user_id, 1)
        logger.info(f"Foydalanuvchi {user_id} botni qayta yoqdi (is_active=1).")


# =========================================================================
# GLOBAL ERROR HANDLER
# =========================================================================

async def global_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Kutilmagan xatoliklarni ushlash"""
    err = context.error
    if isinstance(err, Forbidden):
        if isinstance(update, Update) and update.effective_user:
            db.set_user_active(update.effective_user.id, 0)
            logger.info(f"Foydalanuvchi {update.effective_user.id} botni bloklaganligi (Forbidden) aniqlandi.")
        return

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
        db.add_user(user_id=user.id, full_name=user.full_name, username=user.username, language=user.language_code or 'uz')
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
    if not await check_user_access(update, context):
        return

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
        offices = [it for it in sess['items'] if it['type'] == 'office']
        archives = [it for it in sess['items'] if it['type'] == 'archive']
        medias = [it for it in sess['items'] if it['type'] in ('media', 'video', 'audio')]
        videos = [it for it in sess['items'] if it['type'] == 'video']
        texts = [it for it in sess['items'] if it['type'] == 'text']
        docs = [it for it in sess['items'] if it['type'] == 'doc']

        photo_count = len(photos)
        pdf_count = len(pdfs)
        office_count = len(offices)
        archive_count = len(archives)
        media_count = len(medias)
        video_count = len(videos)
        text_count = len(texts)
        doc_count = len(docs)

        status_lines = ["📥 <b>Fayllar qabul qilindi:</b>"]
        if photo_count > 0:
            status_lines.append(f"• 🖼 <b>Rasmlar (JPG/PNG/HEIC):</b> {photo_count} ta")
        if office_count > 0:
            for o in offices:
                status_lines.append(f"• 📄 <b>Office hujjati:</b> <code>{o['file_name']}</code> {format_file_size(o['file_size'])}")
        if pdf_count > 0:
            for p in pdfs:
                status_lines.append(f"• 📑 <b>PDF:</b> <code>{p['file_name']}</code> {format_file_size(p['file_size'])}")
        if media_count > 0:
            for m in medias:
                icon = "🎞" if m['type'] == 'video' else "🎵"
                status_lines.append(f"• {icon} <b>Media:</b> <code>{m['file_name']}</code> {format_file_size(m['file_size'])}")
        if text_count > 0:
            for t in texts:
                status_lines.append(f"• 📝 <b>Matn/Kod fayli:</b> <code>{t['file_name']}</code> {format_file_size(t['file_size'])}")
        if archive_count > 0:
            for a in archives:
                status_lines.append(f"• 📦 <b>Arxiv:</b> <code>{a['file_name']}</code> {format_file_size(a['file_size'])}")
        if doc_count > 0:
            status_lines.append(f"• 📁 <b>Boshqa hujjatlar:</b> {doc_count} ta")

        status_lines.append("\n<i>Quyidagi amallardan birini tanlang:</i>")
        status_text = "\n".join(status_lines)

        reply_markup = get_file_action_keyboard(
            photo_count=photo_count,
            doc_count=doc_count,
            pdf_count=pdf_count,
            archive_count=archive_count,
            media_count=media_count,
            text_count=text_count,
            office_count=office_count,
            video_count=video_count
        )

        # Yangi fayllar kelganda eski menyu xabari tepada qolib ketmasligi uchun
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
            if session_manager.redis_client:
                asyncio.create_task(session_manager.save_session(user_id))
        except Exception as e:
            logger.error(f"Menyu xabarini yuborishda xatolik: {e}")


async def handle_user_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Foydalanuvchi yuborgan rasm, PDF, Word, Excel, PowerPoint, ZIP, RAR, 7Z, Audio, Video, Matn fayllarini qabul qilish.
    """
    if not await check_user_access(update, context):
        return

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

    # 2. Video yoki Animatsiya yuborilgan bo'lsa
    elif message.video or message.animation:
        vid = message.video or message.animation
        if not any(it['file_unique_id'] == vid.file_unique_id for it in sess['items']):
            safe_name = getattr(vid, 'file_name', None) or f"video_{vid.file_unique_id}.mp4"
            sess['items'].append({
                'type': 'video',
                'file_id': vid.file_id,
                'file_unique_id': vid.file_unique_id,
                'file_name': safe_name,
                'file_size': vid.file_size or 0
            })

    # 3. Audio / Ovozli xabar yuborilgan bo'lsa
    elif message.audio or message.voice:
        aud = message.audio or message.voice
        if not any(it['file_unique_id'] == aud.file_unique_id for it in sess['items']):
            safe_name = getattr(aud, 'file_name', None) or f"audio_{aud.file_unique_id}.mp3"
            sess['items'].append({
                'type': 'audio',
                'file_id': aud.file_id,
                'file_unique_id': aud.file_unique_id,
                'file_name': safe_name,
                'file_size': aud.file_size or 0
            })

    # 4. Hujjat / Fayl yuborilgan bo'lsa
    elif message.document:
        doc = message.document
        if not any(it['file_unique_id'] == doc.file_unique_id for it in sess['items']):
            safe_name = "".join(c for c in doc.file_name if c.isalnum() or c in "._- ") if doc.file_name else "file"
            lower_name = safe_name.lower()
            mime = (doc.mime_type or "").lower()

            archive_exts = ('.zip', '.rar', '.7z', '.tar', '.gz', '.tgz', '.bz2', '.xz')
            office_exts = ('.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt', '.rtf', '.odt', '.ods', '.odp', '.csv')
            text_exts = ('.txt', '.md', '.py', '.js', '.json', '.log', '.html', '.css', '.c', '.cpp', '.java', '.sql', '.xml', '.sh', '.bat', '.env', '.yaml', '.yml')
            video_exts = ('.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv', '.webm')
            audio_exts = ('.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac', '.wma', '.opus')

            if lower_name.endswith(archive_exts) or mime in ['application/zip', 'application/x-zip-compressed', 'application/x-rar-compressed', 'application/vnd.rar', 'application/x-7z-compressed']:
                f_type = 'archive'
            elif lower_name.endswith(office_exts) or 'officedocument' in mime or 'msword' in mime or 'ms-excel' in mime or 'ms-powerpoint' in mime:
                f_type = 'office'
            elif lower_name.endswith('.pdf') or mime == 'application/pdf':
                f_type = 'pdf'
            elif lower_name.endswith(video_exts) or mime.startswith('video/'):
                f_type = 'video'
            elif lower_name.endswith(audio_exts) or mime.startswith('audio/'):
                f_type = 'audio'
            elif lower_name.endswith(text_exts) or mime.startswith('text/'):
                f_type = 'text'
            elif mime.startswith('image/') or lower_name.endswith(('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.heic', '.heif', '.tiff')):
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

    if session_manager.redis_client:
        asyncio.create_task(session_manager.save_session(user_id))

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
    async with CONVERT_SEMAPHORE:
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
                caption=with_caption_ad(f"✅ <b>Sizning PDF hujjatingiz tayyor bo'ldi!</b>\n\n📄 <b>Jami sahifalar:</b> {len(image_paths)} ta\n🤖 @convertoral_bot"),
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
    async with CONVERT_SEMAPHORE:
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


async def merge_pdf_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bir nechta PDF larni bitta PDF ga birlashtirish"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    sess = get_user_session(user_id)

    pdf_items = [it for it in sess['items'] if it['type'] == 'pdf']
    if len(pdf_items) < 2:
        await query.message.reply_text("❌ Birlashtirish uchun kamida 2 ta PDF fayl kerak!")
        return

    sess['menu_msg_id'] = None
    initial_progress = build_progress_bar(
        current=0,
        total=len(pdf_items),
        title="PDF fayllar yuklab olinmoqda...",
        current_info="Tayyorlanmoqda..."
    )
    try:
        await query.message.edit_text(initial_progress, parse_mode='HTML')
        progress_msg = query.message
    except Exception:
        progress_msg = await query.message.reply_html(initial_progress)
    user_dir = get_user_dir(DOCUMENTS_DIR, user_id)

    # 1. Yuklab olish
    pdf_paths = await _download_session_items(context.bot, pdf_items, user_dir, progress_msg)

    # 2. Birlashtirish
    start_time = time.time()
    try:
        await progress_msg.edit_text(
            build_progress_bar(
                current=len(pdf_paths),
                total=len(pdf_paths),
                title="PDF lar birlashtirilmoqda...",
                current_info="Sahifalar jamlanmoqda...",
                start_time=start_time
            ),
            parse_mode='HTML'
        )
    except Exception:
        pass

    output_pdf = os.path.join(user_dir, f"merged_{user_id}_{int(time.time())}.pdf")
    loop = asyncio.get_running_loop()
    async with CONVERT_SEMAPHORE:
        success = await loop.run_in_executor(None, merge_pdf_files, pdf_paths, output_pdf)

    if success and os.path.exists(output_pdf):
        file_size = os.path.getsize(output_pdf)
        with open(output_pdf, 'rb') as pdf_file:
            await context.bot.send_document(
                chat_id=user_id,
                document=pdf_file,
                filename=f"Birlashtirilgan_{len(pdf_paths)}_pdf.pdf",
                caption=with_caption_ad(
                    f"✅ <b>PDF fayllar muvaffaqiyatli birlashtirildi!</b>\n\n"
                    f"📑 <b>Birlashtirilgan hujjatlar:</b> {len(pdf_paths)} ta\n"
                    f"💾 <b>Hajmi:</b> {format_file_size(file_size)}\n"
                    f"🤖 @convertoral_bot"
                ),
                parse_mode='HTML',
                read_timeout=120.0,
                write_timeout=120.0
            )
        try:
            await progress_msg.delete()
        except Exception:
            pass
    else:
        await progress_msg.edit_text("❌ PDF fayllarni birlashtirishda xatolik yuz berdi.")

    cleanup_user_files(user_dir)
    reset_user_session(user_id)


async def compress_pdf_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """PDF fayllarni sifatini saqlab siqish (Compress)"""
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

    # 1. Yuklab olish
    pdf_paths = await _download_session_items(context.bot, pdf_items, user_dir, progress_msg)

    # 2. Siqish
    start_time = time.time()
    loop = asyncio.get_running_loop()

    for idx, p_path in enumerate(pdf_paths, 1):
        try:
            await progress_msg.edit_text(
                build_progress_bar(
                    current=idx,
                    total=len(pdf_paths),
                    title="PDF siqilmoqda (Compress)...",
                    current_info=os.path.basename(p_path),
                    start_time=start_time
                ),
                parse_mode='HTML'
            )
        except Exception:
            pass

        output_pdf = os.path.join(user_dir, f"compressed_{idx}_{int(time.time())}.pdf")
        async with CONVERT_SEMAPHORE:
            success, orig_sz, new_sz = await loop.run_in_executor(None, compress_pdf, p_path, output_pdf)

        if success and os.path.exists(output_pdf):
            saved_pct = int(((orig_sz - new_sz) / orig_sz) * 100) if orig_sz > 0 else 0
            if saved_pct < 0:
                saved_pct = 0
            
            clean_name = os.path.basename(p_path)
            if "_" in clean_name:
                clean_name = clean_name.split("_", 1)[1]
            if not clean_name.lower().endswith(".pdf"):
                clean_name += ".pdf"

            with open(output_pdf, 'rb') as pdf_file:
                await context.bot.send_document(
                    chat_id=user_id,
                    document=pdf_file,
                    filename=f"Compressed_{clean_name}",
                    caption=with_caption_ad(
                        f"✅ <b>PDF muvaffaqiyatli siqildi!</b>\n\n"
                        f"📊 <b>Oldingi hajm:</b> {format_file_size(orig_sz)}\n"
                        f"⚡️ <b>Yangi hajm:</b> {format_file_size(new_sz)}\n"
                        f"📉 <b>Tejaldi:</b> {saved_pct}%\n\n"
                        f"🤖 @convertoral_bot"
                    ),
                    parse_mode='HTML',
                    read_timeout=120.0,
                    write_timeout=120.0
                )
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⚠️ {os.path.basename(p_path)} PDF faylini siqib bo'lmadi."
            )
        await asyncio.sleep(0.3)

    try:
        await progress_msg.delete()
    except Exception:
        pass

    cleanup_user_files(user_dir)
    reset_user_session(user_id)


async def text_to_pdf_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Matn yoki kod fayllarini PDF ga o'girish"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    sess = get_user_session(user_id)

    text_items = [it for it in sess['items'] if it['type'] == 'text']
    if not text_items:
        await query.message.reply_text("❌ Matnli fayl topilmadi!")
        return

    sess['menu_msg_id'] = None
    initial_progress = build_progress_bar(
        current=0,
        total=len(text_items),
        title="Matnli fayl yuklanmoqda...",
        current_info="Tayyorlanmoqda..."
    )
    try:
        await query.message.edit_text(initial_progress, parse_mode='HTML')
        progress_msg = query.message
    except Exception:
        progress_msg = await query.message.reply_html(initial_progress)
    user_dir = get_user_dir(DOCUMENTS_DIR, user_id)

    # 1. Yuklab olish
    txt_paths = await _download_session_items(context.bot, text_items, user_dir, progress_msg)

    # 2. PDF ga aylantirish
    start_time = time.time()
    loop = asyncio.get_running_loop()

    for idx, t_path in enumerate(txt_paths, 1):
        try:
            await progress_msg.edit_text(
                build_progress_bar(
                    current=idx,
                    total=len(txt_paths),
                    title="Matn PDF ga aylantirilmoqda...",
                    current_info=os.path.basename(t_path),
                    start_time=start_time
                ),
                parse_mode='HTML'
            )
        except Exception:
            pass

        output_pdf = os.path.join(user_dir, f"text_{idx}_{int(time.time())}.pdf")
        async with CONVERT_SEMAPHORE:
            success = await loop.run_in_executor(None, convert_text_to_pdf, t_path, output_pdf)

        if success and os.path.exists(output_pdf):
            clean_name = os.path.basename(t_path)
            if "_" in clean_name:
                clean_name = clean_name.split("_", 1)[1]
            base_title = os.path.splitext(clean_name)[0]

            with open(output_pdf, 'rb') as pdf_file:
                await context.bot.send_document(
                    chat_id=user_id,
                    document=pdf_file,
                    filename=f"{base_title}.pdf",
                    caption=with_caption_ad(
                        f"✅ <b>Matn PDF formatiga o'tkazildi!</b>\n\n"
                        f"📄 <b>Fayl:</b> <code>{clean_name}</code>\n"
                        f"🤖 @convertoral_bot"
                    ),
                    parse_mode='HTML',
                    read_timeout=120.0,
                    write_timeout=120.0
                )
        await asyncio.sleep(0.3)

    try:
        await progress_msg.delete()
    except Exception:
        pass

    cleanup_user_files(user_dir)
    reset_user_session(user_id)


async def media_to_mp3_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Audio va Video fayllarni 192kbps sifatli MP3 ga o'tkazish"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    sess = get_user_session(user_id)

    media_items = [it for it in sess['items'] if it['type'] in ('media', 'video', 'audio')]
    if not media_items:
        await query.message.reply_text("❌ Audio yoki video fayl topilmadi!")
        return

    sess['menu_msg_id'] = None
    initial_progress = build_progress_bar(
        current=0,
        total=len(media_items),
        title="Media fayl yuklanmoqda...",
        current_info="Tayyorlanmoqda..."
    )
    try:
        await query.message.edit_text(initial_progress, parse_mode='HTML')
        progress_msg = query.message
    except Exception:
        progress_msg = await query.message.reply_html(initial_progress)
    user_dir = get_user_dir(DOCUMENTS_DIR, user_id)

    # 1. Yuklab olish
    media_paths = await _download_session_items(context.bot, media_items, user_dir, progress_msg)
    if not media_paths:
        await progress_msg.edit_text("❌ Fayllarni yuklab olishda xatolik yuz berdi.")
        cleanup_user_files(user_dir)
        reset_user_session(user_id)
        return

    # 2. MP3 ga o'tkazish
    start_time = time.time()
    loop = asyncio.get_running_loop()
    bot_username = context.bot.username or "convertoral_bot"

    for idx, (it, m_path) in enumerate(zip(media_items, media_paths), 1):
        orig_name = it.get('file_name') or os.path.basename(m_path)
        try:
            await progress_msg.edit_text(
                build_progress_bar(
                    current=idx,
                    total=len(media_paths),
                    title="Audio/Video MP3 ga o'girilmoqda...",
                    current_info=orig_name,
                    start_time=start_time
                ),
                parse_mode='HTML'
            )
        except Exception:
            pass

        output_mp3 = os.path.join(user_dir, f"audio_{idx}_{int(time.time())}.mp3")
        async with CONVERT_SEMAPHORE:
            success = await loop.run_in_executor(None, convert_media_to_mp3, m_path, output_mp3)

        if success and os.path.exists(output_mp3) and os.path.getsize(output_mp3) > 0:
            clean_name = orig_name
            if "_" in clean_name and not it.get('file_name'):
                clean_name = clean_name.split("_", 1)[1]
            base_title = os.path.splitext(clean_name)[0]
            if not base_title:
                base_title = f"audio_{idx}"

            with open(output_mp3, 'rb') as audio_file:
                await context.bot.send_audio(
                    chat_id=user_id,
                    audio=audio_file,
                    title=base_title,
                    performer=f"@{bot_username}",
                    filename=f"{base_title}.mp3",
                    caption=with_caption_ad(
                        f"✅ <b>MP3 audio muvaffaqiyatli tayyorlandi!</b>\n\n"
                        f"🎵 <b>Nomi:</b> <code>{base_title}.mp3</code>\n"
                        f"💾 <b>Hajmi:</b> {format_file_size(os.path.getsize(output_mp3))}\n"
                        f"🎧 <b>Sifat:</b> 192 kbps HD Audio\n"
                        f"🤖 @{bot_username}"
                    ),
                    parse_mode='HTML',
                    read_timeout=120.0,
                    write_timeout=120.0
                )
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⚠️ <b>{orig_name}</b> faylidan audio ajratib bo'lmadi (ehtimol videoda audio/ovoz yo'q yoki format qo'llab-quvvatlanmaydi).",
                parse_mode='HTML'
            )
        await asyncio.sleep(0.3)

    try:
        await progress_msg.delete()
    except Exception:
        pass

    cleanup_user_files(user_dir)
    reset_user_session(user_id)


async def enhance_audio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Audio va Videodagi ovozni neyron tarmoq orqali shovqin va shitirlashlardan tozalash (Adobe Enhance)"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    sess = get_user_session(user_id)

    media_items = [it for it in sess['items'] if it['type'] in ('media', 'video', 'audio')]
    if not media_items:
        await query.message.reply_text("❌ Audio yoki video fayl topilmadi!")
        return

    sess['menu_msg_id'] = None
    initial_progress = build_progress_bar(
        current=0,
        total=len(media_items),
        title="🎙 AI Ovoz tozalanmoqda...",
        current_info="Tayyorlanmoqda..."
    )
    try:
        await query.message.edit_text(initial_progress, parse_mode='HTML')
        progress_msg = query.message
    except Exception:
        progress_msg = await query.message.reply_html(initial_progress)
    user_dir = get_user_dir(DOCUMENTS_DIR, user_id)

    # 1. Yuklab olish
    media_paths = await _download_session_items(context.bot, media_items, user_dir, progress_msg)
    if not media_paths:
        await progress_msg.edit_text("❌ Fayllarni yuklab olishda xatolik yuz berdi.")
        cleanup_user_files(user_dir)
        reset_user_session(user_id)
        return

    # 2. AI Enhance qilish (Shovqin, shitirlash va shamol tovushlarini tozalash)
    start_time = time.time()
    loop = asyncio.get_running_loop()
    bot_username = context.bot.username or "convertoral_bot"

    for idx, (it, m_path) in enumerate(zip(media_items, media_paths), 1):
        orig_name = it.get('file_name') or os.path.basename(m_path)
        try:
            await progress_msg.edit_text(
                build_progress_bar(
                    current=idx,
                    total=len(media_paths),
                    title="🎙 AI Ovoz tozalanmoqda (Adobe Voice Clean)...",
                    current_info=orig_name,
                    start_time=start_time
                ),
                parse_mode='HTML'
            )
        except Exception:
            pass

        output_enhanced = os.path.join(user_dir, f"enhanced_{idx}_{int(time.time())}.mp3")
        async with CONVERT_SEMAPHORE:
            success = await loop.run_in_executor(None, enhance_audio_voice, m_path, output_enhanced)

        if success and os.path.exists(output_enhanced) and os.path.getsize(output_enhanced) > 0:
            clean_name = orig_name
            if "_" in clean_name and not it.get('file_name'):
                clean_name = clean_name.split("_", 1)[1]
            base_title = os.path.splitext(clean_name)[0]
            if not base_title:
                base_title = f"audio_{idx}"

            with open(output_enhanced, 'rb') as audio_file:
                await context.bot.send_audio(
                    chat_id=user_id,
                    audio=audio_file,
                    title=f"{base_title} (Toza ovoz)",
                    performer="🎙 AI Audio Enhancer",
                    filename=f"{base_title}_enhanced.mp3",
                    caption=with_caption_ad(
                        f"🎙 <b>AI Studio Voice Enhancer (Adobe Audio Clean)</b>\n\n"
                        f"✅ <b>Barcha shovqin, shitirlash va shamol tovushlari tozalandi!</b>\n\n"
                        f"🎵 <b>Asl fayl:</b> <code>{orig_name}</code>\n"
                        f"🎧 <b>Format:</b> 192 kbps HD Audio (Studiya sifati)\n"
                        f"💾 <b>Hajmi:</b> {format_file_size(os.path.getsize(output_enhanced))}\n"
                        f"⚡️ <b>Filtrlar:</b> RNNoise neyron tarmog'i + Spektral tozalash + Studio balansi\n\n"
                        f"🤖 @{bot_username}"
                    ),
                    parse_mode='HTML',
                    read_timeout=120.0,
                    write_timeout=120.0
                )
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⚠️ <b>{orig_name}</b> faylidan ovozni tozalab bo'lmadi (ehtimol faylda audio yo'q yoki format qo'llab-quvvatlanmaydi).",
                parse_mode='HTML'
            )
        await asyncio.sleep(0.3)

    try:
        await progress_msg.delete()
    except Exception:
        pass

    cleanup_user_files(user_dir)
    reset_user_session(user_id)


async def enhance_asmr_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ruchka yozuvi, qalam va ASMR tovushlarini kuchaytirib, tiniq ASMR audio yaratish"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    sess = get_user_session(user_id)

    media_items = [it for it in sess['items'] if it['type'] in ('media', 'video', 'audio')]
    if not media_items:
        await query.message.reply_text("❌ Audio yoki video fayl topilmadi!")
        return

    sess['menu_msg_id'] = None
    initial_progress = build_progress_bar(
        current=0,
        total=len(media_items),
        title="🎧 ASMR / Ruchka yozuvi tayyorlanmoqda...",
        current_info="Fayllar yuklanmoqda..."
    )
    try:
        await query.message.edit_text(initial_progress, parse_mode='HTML')
        progress_msg = query.message
    except Exception:
        progress_msg = await query.message.reply_html(initial_progress)
    user_dir = get_user_dir(DOCUMENTS_DIR, user_id)

    # 1. Yuklab olish
    media_paths = await _download_session_items(context.bot, media_items, user_dir, progress_msg)
    if not media_paths:
        await progress_msg.edit_text("❌ Fayllarni yuklab olishda xatolik yuz berdi.")
        cleanup_user_files(user_dir)
        reset_user_session(user_id)
        return

    # 2. ASMR Enhance qilish (Ruchka yozuvi, qalam shitirlashi, nozik teksturalarni chiqarish)
    start_time = time.time()
    loop = asyncio.get_running_loop()
    bot_username = context.bot.username or "convertoral_bot"

    for idx, (it, m_path) in enumerate(zip(media_items, media_paths), 1):
        orig_name = it.get('file_name') or os.path.basename(m_path)
        try:
            await progress_msg.edit_text(
                build_progress_bar(
                    current=idx,
                    total=len(media_paths),
                    title="🎧 ASMR yozuv tiniqlashtirilmoqda...",
                    current_info=orig_name,
                    start_time=start_time
                ),
                parse_mode='HTML'
            )
        except Exception:
            pass

        output_asmr = os.path.join(user_dir, f"asmr_{idx}_{int(time.time())}.mp3")
        async with CONVERT_SEMAPHORE:
            success = await loop.run_in_executor(None, enhance_asmr_audio, m_path, output_asmr)

        if success and os.path.exists(output_asmr) and os.path.getsize(output_asmr) > 0:
            clean_name = orig_name
            if "_" in clean_name and not it.get('file_name'):
                clean_name = clean_name.split("_", 1)[1]
            base_title = os.path.splitext(clean_name)[0]
            if not base_title:
                base_title = f"asmr_{idx}"

            with open(output_asmr, 'rb') as audio_file:
                await context.bot.send_audio(
                    chat_id=user_id,
                    audio=audio_file,
                    title=f"{base_title} (ASMR Ruchka)",
                    performer="🎧 ASMR Audio Enhancer",
                    filename=f"{base_title}_asmr.mp3",
                    caption=with_caption_ad(
                        f"🎧 <b>ASMR / Ruchka Yozuvi (Crisp Pen ASMR)</b>\n\n"
                        f"✨ <b>Ruchka va qalam shitirlashi maksimal tiniqlashtirildi!</b>\n\n"
                        f"📝 <b>Asl fayl:</b> <code>{orig_name}</code>\n"
                        f"🎧 <b>Format:</b> 192 kbps HD Audio (ASMR Studio)\n"
                        f"💾 <b>Hajmi:</b> {format_file_size(os.path.getsize(output_asmr))}\n"
                        f"⚡️ <b>Filtrlar:</b> Yozuv teksturasi + Transient Sharpener + Yumshoq fon tozalash\n\n"
                        f"🤖 @{bot_username}"
                    ),
                    parse_mode='HTML',
                    read_timeout=120.0,
                    write_timeout=120.0
                )
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⚠️ <b>{orig_name}</b> faylidan ASMR audio tayyorlab bo'lmadi.",
                parse_mode='HTML'
            )
        await asyncio.sleep(0.3)

    try:
        await progress_msg.delete()
    except Exception:
        pass

    cleanup_user_files(user_dir)
    reset_user_session(user_id)


async def office_to_pdf_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Word, Excel, PowerPoint hujjatlarini PDF ga aylantirish"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    sess = get_user_session(user_id)

    office_items = [it for it in sess['items'] if it['type'] == 'office']
    if not office_items:
        await query.message.reply_text("❌ Office hujjati topilmadi!")
        return

    sess['menu_msg_id'] = None
    initial_progress = build_progress_bar(
        current=0,
        total=len(office_items),
        title="Office hujjati yuklanmoqda...",
        current_info="Tayyorlanmoqda..."
    )
    try:
        await query.message.edit_text(initial_progress, parse_mode='HTML')
        progress_msg = query.message
    except Exception:
        progress_msg = await query.message.reply_html(initial_progress)
    user_dir = get_user_dir(DOCUMENTS_DIR, user_id)

    # 1. Yuklab olish
    office_paths = await _download_session_items(context.bot, office_items, user_dir, progress_msg)

    # 2. PDF ga o'tkazish
    start_time = time.time()
    loop = asyncio.get_running_loop()

    for idx, o_path in enumerate(office_paths, 1):
        try:
            await progress_msg.edit_text(
                build_progress_bar(
                    current=idx,
                    total=len(office_paths),
                    title="Office PDF ga aylantirilmoqda...",
                    current_info=os.path.basename(o_path),
                    start_time=start_time
                ),
                parse_mode='HTML'
            )
        except Exception:
            pass

        async with CONVERT_SEMAPHORE:
            res_pdf = await loop.run_in_executor(None, convert_office_to_pdf, o_path, user_dir)

        if res_pdf and os.path.exists(res_pdf):
            clean_name = os.path.basename(o_path)
            if "_" in clean_name:
                clean_name = clean_name.split("_", 1)[1]
            base_title = os.path.splitext(clean_name)[0]

            with open(res_pdf, 'rb') as pdf_file:
                await context.bot.send_document(
                    chat_id=user_id,
                    document=pdf_file,
                    filename=f"{base_title}.pdf",
                    caption=with_caption_ad(
                        f"✅ <b>Office hujjati PDF ga muvaffaqiyatli o'girildi!</b>\n\n"
                        f"📄 <b>Asl fayl:</b> <code>{clean_name}</code>\n"
                        f"💾 <b>Hajmi:</b> {format_file_size(os.path.getsize(res_pdf))}\n"
                        f"🤖 @convertoral_bot"
                    ),
                    parse_mode='HTML',
                    read_timeout=120.0,
                    write_timeout=120.0
                )
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⚠️ {os.path.basename(o_path)} faylini PDF ga o'tkazib bo'lmadi."
            )
        await asyncio.sleep(0.3)

    try:
        await progress_msg.delete()
    except Exception:
        pass

    cleanup_user_files(user_dir)
    reset_user_session(user_id)


async def ocr_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Rasm yoki PDF dagi matnni tanib ajratib olish (OCR)"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    sess = get_user_session(user_id)

    ocr_items = [it for it in sess['items'] if it['type'] in ('photo', 'pdf')]
    if not ocr_items:
        await query.message.reply_text("❌ Matn ajratish uchun rasm yoki PDF topilmadi!")
        return

    sess['menu_msg_id'] = None
    initial_progress = build_progress_bar(
        current=0,
        total=len(ocr_items),
        title="Fayl yuklanmoqda...",
        current_info="OCR dvigateli tayyorlanmoqda..."
    )
    try:
        await query.message.edit_text(initial_progress, parse_mode='HTML')
        progress_msg = query.message
    except Exception:
        progress_msg = await query.message.reply_html(initial_progress)
    user_dir = get_user_dir(DOCUMENTS_DIR, user_id)

    # 1. Yuklab olish
    file_paths = await _download_session_items(context.bot, ocr_items, user_dir, progress_msg)

    # 2. OCR Matn tanish
    start_time = time.time()
    loop = asyncio.get_running_loop()

    for idx, f_path in enumerate(file_paths, 1):
        try:
            await progress_msg.edit_text(
                build_progress_bar(
                    current=idx,
                    total=len(file_paths),
                    title="Matn tanilmoqda (OCR)...",
                    current_info=os.path.basename(f_path),
                    start_time=start_time
                ),
                parse_mode='HTML'
            )
        except Exception:
            pass

        async with CONVERT_SEMAPHORE:
            recognized_text = await loop.run_in_executor(None, extract_text_ocr, f_path, "uzb+rus+eng")

        clean_name = os.path.basename(f_path)
        if "_" in clean_name:
            clean_name = clean_name.split("_", 1)[1]

        # Agar matn ixcham bo'lsa xabar qilib, katta bo'lsa .txt qilib jo'natamiz
        if len(recognized_text) <= 3500:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"🔍 <b>OCR Natijasi:</b> <code>{clean_name}</code>\n\n{recognized_text}",
                parse_mode='HTML'
            )
        else:
            txt_path = os.path.join(user_dir, f"ocr_{idx}.txt")
            with open(txt_path, 'w', encoding='utf-8') as f:
                f.write(recognized_text)
            
            snippet = recognized_text[:500] + "...\n<i>(To'liq matn quyidagi faylda)</i>"
            with open(txt_path, 'rb') as f:
                await context.bot.send_document(
                    chat_id=user_id,
                    document=f,
                    filename=f"OCR_{os.path.splitext(clean_name)[0]}.txt",
                    caption=f"🔍 <b>OCR Natijasi (Katta hajm):</b>\n\n{snippet}",
                    parse_mode='HTML'
                )

        await asyncio.sleep(0.3)

    try:
        await progress_msg.delete()
    except Exception:
        pass

    cleanup_user_files(user_dir)
    reset_user_session(user_id)


async def video_to_gif_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Videoni animatsiyali GIF ga aylantirish"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    user_id = query.from_user.id
    sess = get_user_session(user_id)

    video_items = [it for it in sess['items'] if it['type'] == 'video']
    if not video_items:
        await query.message.reply_text("❌ GIF qilish uchun video topilmadi!")
        return

    sess['menu_msg_id'] = None
    initial_progress = build_progress_bar(
        current=0,
        total=len(video_items),
        title="Video yuklanmoqda...",
        current_info="Tayyorlanmoqda..."
    )
    try:
        await query.message.edit_text(initial_progress, parse_mode='HTML')
        progress_msg = query.message
    except Exception:
        progress_msg = await query.message.reply_html(initial_progress)
    user_dir = get_user_dir(DOCUMENTS_DIR, user_id)

    # 1. Yuklab olish
    vid_paths = await _download_session_items(context.bot, video_items, user_dir, progress_msg)
    if not vid_paths:
        await progress_msg.edit_text("❌ Videoni yuklab olishda xatolik yuz berdi.")
        cleanup_user_files(user_dir)
        reset_user_session(user_id)
        return

    # 2. GIF ga o'tkazish
    start_time = time.time()
    loop = asyncio.get_running_loop()
    bot_username = context.bot.username or "convertoral_bot"

    for idx, (it, v_path) in enumerate(zip(video_items, vid_paths), 1):
        orig_name = it.get('file_name') or os.path.basename(v_path)
        try:
            await progress_msg.edit_text(
                build_progress_bar(
                    current=idx,
                    total=len(vid_paths),
                    title="GIF animatsiya tayyorlanmoqda...",
                    current_info=orig_name,
                    start_time=start_time
                ),
                parse_mode='HTML'
            )
        except Exception:
            pass

        output_gif = os.path.join(user_dir, f"anim_{idx}_{int(time.time())}.gif")
        async with CONVERT_SEMAPHORE:
            success = await loop.run_in_executor(None, convert_video_to_gif, v_path, output_gif, 15)

        if success and os.path.exists(output_gif) and os.path.getsize(output_gif) > 0:
            with open(output_gif, 'rb') as gif_file:
                await context.bot.send_animation(
                    chat_id=user_id,
                    animation=gif_file,
                    caption=with_caption_ad(f"✅ <b>GIF animatsiya tayyor!</b>\n🤖 @{bot_username}"),
                    parse_mode='HTML',
                    read_timeout=120.0,
                    write_timeout=120.0
                )
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"⚠️ <b>{orig_name}</b> videoni GIF ga o'girib bo'lmadi.",
                parse_mode='HTML'
            )
        await asyncio.sleep(0.3)

    try:
        await progress_msg.delete()
    except Exception:
        pass

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
            async with CONVERT_SEMAPHORE:
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
    async with CONVERT_SEMAPHORE:
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
                caption=with_caption_ad(f"✅ <b>Barcha fayllar ZIP arxiviga muvaffaqiyatli jamlandi!</b>\n\n📦 <b>Jami fayllar:</b> {len(downloaded_paths)} ta\n🤖 @convertoral_bot"),
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
# MATNLI XABARLAR (FOYDALANUVCHI AMALLARI)
# =========================================================================

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
        "1. Rasmlar, PDF, Word/Excel yoki ZIP/RAR arxiv yuboring.\n"
        "2. Chiqqan menyudan kerakli amalni tanlang."
    )


# =========================================================================
# ADMIN BOSHQARUV PANELI VA HANDLERLAR
# =========================================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/admin buyrug'i (Target panel dizayni)"""
    user_id = update.effective_user.id
    if not db.is_admin(user_id, ADMINS):
        return

    is_maint = db.is_maintenance()
    await update.message.reply_html(
        text="👑 <b>Admin Boshqaruv Paneli:</b>\n\n"
             "Quyidagi bo'limlardan birini tanlang:",
        reply_markup=get_admin_main_keyboard(is_maint)
    )


async def admin_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin bosh menyusiga qaytish"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return ConversationHandler.END

    is_maint = db.is_maintenance()
    try:
        await query.edit_message_text(
            text="👑 <b>Admin Boshqaruv Paneli:</b>\n\n"
                 "Quyidagi bo'limlardan birini tanlang:",
            reply_markup=get_admin_main_keyboard(is_maint),
            parse_mode='HTML'
        )
    except Exception:
        pass
    return ConversationHandler.END


async def admin_close_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panelini yopish"""
    query = update.callback_query
    if not db.is_admin(query.from_user.id, ADMINS):
        return
    try:
        await query.answer()
        await query.message.delete()
    except Exception:
        pass


async def admin_server_health_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Server monitoring va salomatlik ko'rsatkichlari"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    h = get_system_health(DATABASE_PATH)
    text = (
        "🖥 <b>Server & Bot Salomatlik Monitoringi:</b>\n\n"
        f"⚡ <b>CPU yuklamasi:</b> <code>{h['cpu_pct']}%</code>\n"
        f"🧠 <b>RAM (Xotira):</b> <code>{h['ram_used_mb']} MB / {h['ram_total_mb']} MB ({h['ram_pct']}%)</code>\n"
        f"💾 <b>Disk (Bo'sh joy):</b> <code>{h['disk_free_gb']} GB / {h['disk_total_gb']} GB ({h['disk_pct']}% band)</code>\n"
        f"🗄 <b>SQLite DB hajmi:</b> <code>{h['db_size_mb']} MB (WAL faol)</code>\n"
        f"⏱ <b>Uptime (Ish vaqti):</b> <code>{h['uptime']}</code>\n"
        f"🚀 <b>Dvigatel:</b> 🟢 Faol (High-Performance Async)\n"
    )
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=get_server_health_keyboard(),
            parse_mode='HTML'
        )
    except Exception:
        pass


async def admin_platform_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Platformalar va formatlar bo'yicha konvertatsiya statistikasi"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    p_stats = db.get_platform_stats()
    lines = []
    for item in p_stats['breakdown']:
        lines.append(f"• <b>{item['platform']}:</b> <code>{item['count']} ta</code> ({item['pct']}%)")

    breakdown_text = "\n".join(lines) if lines else "<i>Hali konvertatsiyalar amalga oshirilmagan.</i>"
    text = (
        "📈 <b>Platformalar & Modullar Bo'yicha Statistika:</b>\n\n"
        f"📊 <b>Jami bajarilgan konvertatsiyalar:</b> <code>{p_stats['total']} ta</code>\n\n"
        f"{breakdown_text}"
    )
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=get_platform_stats_keyboard(),
            parse_mode='HTML'
        )
    except Exception:
        pass


async def admin_cache_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kesh va disk xotirasi boshqaruvi"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    doc_dir = os.path.abspath(DOCUMENTS_DIR)
    file_count = 0
    total_bytes = 0
    if os.path.exists(doc_dir):
        for root, _, files in os.walk(doc_dir):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    total_bytes += os.path.getsize(fp)
                    file_count += 1
                except Exception:
                    pass

    sz_str = format_file_size(total_bytes) if total_bytes > 0 else "0 KB"
    text = (
        "🧹 <b>Kesh Boshqaruvi:</b>\n\n"
        f"• Diskdagi vaqtinchalik konvertatsiya fayllari: <b>{file_count} ta</b>\n"
        f"• Egallangan umumiy hajm: <b>{sz_str}</b>\n\n"
        "<i>Keraksiz vaqtinchalik fayllarni tozalash orqali server xotirasini bo'shatishingiz mumkin.</i>"
    )
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=get_admin_cache_keyboard(),
            parse_mode='HTML'
        )
    except Exception:
        pass


async def admin_clean_disk_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Diskdagi eski fayllarni tozalash"""
    query = update.callback_query
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    doc_dir = os.path.abspath(DOCUMENTS_DIR)
    deleted = 0
    if os.path.exists(doc_dir):
        for root, dirs, files in os.walk(doc_dir):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    os.remove(fp)
                    deleted += 1
                except Exception:
                    pass
            for d in dirs:
                dp = os.path.join(root, d)
                try:
                    shutil.rmtree(dp, ignore_errors=True)
                except Exception:
                    pass

    try:
        await query.answer(f"✅ {deleted} ta vaqtinchalik fayl tozalandi!", show_alert=True)
    except Exception:
        pass
    await admin_cache_callback(update, context)


async def admin_vip_manage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """VIP obunachilar boshqaruv menyusi"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    vip_users = db.get_all_vip_users()
    text = (
        "⭐️ <b>VIP Obunachilar Boshqaruvi:</b>\n\n"
        f"📊 <b>Faol VIP a'zolar soni:</b> <code>{len(vip_users)} ta</code>\n\n"
        "Quyidagi tugmalar orqali istalgan foydalanuvchiga (ID yoki @username orqali) "
        "VIP obuna berishingiz yoki faol a'zolar ro'yxatini ko'rishingiz mumkin:"
    )
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=get_admin_vip_menu_keyboard(),
            parse_mode='HTML'
        )
    except Exception:
        pass


async def admin_list_vip_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Faol VIP foydalanuvchilar ro'yxati"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    vip_users = db.get_all_vip_users()
    if not vip_users:
        text = "📋 <b>VIP A'zolar Ro'yxati:</b>\n\n<i>Hozirda faol VIP foydalanuvchilar mavjud emas.</i>"
    else:
        lines = [f"📋 <b>Faol VIP A'zolar ({len(vip_users)} ta):</b>\n"]
        for i, u in enumerate(vip_users[:30], 1):
            name = html.escape(str(u.get('full_name') or 'Foydalanuvchi'))
            uname = f"@{u['username']}" if u.get('username') else f"ID: {u['user_id']}"
            exp = u.get('vip_expires_at') or 'Cheksiz'
            lines.append(f"{i}. <b>{name}</b> ({uname})\n   ⏳ Tugash muddati: <code>{exp}</code>")
        if len(vip_users) > 30:
            lines.append(f"\n<i>...va yana {len(vip_users) - 30} ta foydalanuvchi</i>")
        text = "\n".join(lines)

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ VIP berish", callback_data="admin_add_vip_user")],
        [InlineKeyboardButton("◀️ Orqaga", callback_data="admin_vip_manage")]
    ])
    try:
        await query.edit_message_text(text=text, reply_markup=markup, parse_mode='HTML')
    except Exception:
        pass


async def adm_pick_vip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi uchun VIP muddati tanlash"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    uid = int(query.data.split(":")[1])
    try:
        await query.edit_message_text(
            text=f"⭐️ <b>Foydalanuvchi (ID: <code>{uid}</code>) uchun VIP muddatini tanlang:</b>",
            reply_markup=get_admin_vip_select_days_keyboard(uid),
            parse_mode='HTML'
        )
    except Exception:
        pass


async def adm_give_vip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchiga VIP muddatini berish"""
    query = update.callback_query
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    parts = query.data.split(":")
    uid = int(parts[1])
    days = int(parts[2])

    db.set_user_vip_days(uid, days)
    dur_text = "Abadiy (Cheksiz)" if days >= 36500 else f"{days} kunlik"

    try:
        await context.bot.send_message(
            chat_id=uid,
            text=(
                f"🎉 <b>Tabriklaymiz!</b>\n\n"
                f"⭐️ Sizga admin tomonidan <b>{dur_text} VIP Premium</b> obunasi taqdim etildi!\n"
                f"Endi botdan barcha majburiy kanallarsiz va cheklovlarsiz foydalanishingiz mumkin 🚀"
            ),
            parse_mode='HTML'
        )
    except Exception:
        pass

    try:
        await query.answer(f"✅ VIP muvaffaqiyatli berildi ({dur_text})!", show_alert=True)
    except Exception:
        pass

    is_maint = db.is_maintenance()
    try:
        await query.edit_message_text(
            text=f"✅ <b>Foydalanuvchiga (ID: <code>{uid}</code>) {dur_text} VIP obuna berildi!</b>",
            reply_markup=get_admin_main_keyboard(is_maint),
            parse_mode='HTML'
        )
    except Exception:
        pass


async def adm_revoke_vip_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchining VIP maqomini bekor qilish"""
    query = update.callback_query
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    uid = int(query.data.split(":")[1])
    db.revoke_user_vip(uid)

    try:
        await context.bot.send_message(
            chat_id=uid,
            text="⚠️ <b>Sizning VIP Premium obunangiz admin tomonidan bekor qilindi.</b>",
            parse_mode='HTML'
        )
    except Exception:
        pass

    try:
        await query.answer("🚫 VIP obuna bekor qilindi!", show_alert=True)
    except Exception:
        pass

    is_maint = db.is_maintenance()
    try:
        await query.edit_message_text(
            text=f"🚫 <b>Foydalanuvchining (ID: <code>{uid}</code>) VIP obunasi bekor qilindi.</b>",
            reply_markup=get_admin_main_keyboard(is_maint),
            parse_mode='HTML'
        )
    except Exception:
        pass


async def start_add_vip_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchiga VIP berishni boshlash"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return ConversationHandler.END

    await query.edit_message_text(
        text="⭐️ <b>Foydalanuvchiga VIP berish:</b>\n\n"
             "Foydalanuvchining <b>Telegram ID</b> raqamini yoki <b>@username</b>ini yuboring:\n\n"
             "<i>Masalan:</i> <code>123456789</code> yoki <code>@username</code>",
        reply_markup=get_cancel_keyboard("admin_vip_manage"),
        parse_mode='HTML'
    )
    return ADMIN_WAITING_VIP_USER


async def receive_vip_user_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """VIP beriladigan foydalanuvchini izlash va muddat tanlash menyusini ochish"""
    admin_id = update.effective_user.id
    if not db.is_admin(admin_id, ADMINS):
        return ConversationHandler.END

    query_text = update.message.text.strip()
    u = db.search_user(query_text)
    if not u:
        if query_text.replace("@", "").isdigit():
            uid = int(query_text.replace("@", ""))
            u = {'user_id': uid, 'full_name': 'Foydalanuvchi', 'username': ''}
        else:
            try:
                chat = await context.bot.get_chat(query_text)
                uid = chat.id
                u = {'user_id': uid, 'full_name': chat.full_name or 'Foydalanuvchi', 'username': chat.username or ''}
            except Exception:
                await update.message.reply_html(
                    f"❌ <b>Foydalanuvchi topilmadi:</b> <code>{html.escape(query_text)}</code>\n"
                    "Iltimos, qaytadan ID yoki @username kiriting:",
                    reply_markup=get_cancel_keyboard("admin_vip_manage")
                )
                return ADMIN_WAITING_VIP_USER

    uid = u['user_id']
    name = html.escape(str(u.get('full_name') or 'Foydalanuvchi'))
    uname = f"@{u['username']}" if u.get('username') else f"ID: {uid}"
    vip_info = db.get_vip_info(uid)
    vip_status = f"🟢 Faol (Tugash: <code>{vip_info['expires_at']}</code>)" if vip_info.get('is_vip') else "🔴 Faol emas"

    text = (
        f"⭐️ <b>Foydalanuvchi:</b> <b>{name}</b> ({uname})\n"
        f"🆔 <b>ID:</b> <code>{uid}</code>\n"
        f"📊 <b>Hozirgi VIP holati:</b> {vip_status}\n\n"
        f"Qancha muddatga VIP obuna bermoqchisiz?"
    )
    await update.message.reply_html(text=text, reply_markup=get_admin_vip_select_days_keyboard(uid))
    return ConversationHandler.END


async def start_custom_vip_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Qo'lda kiritiladigan VIP kunlar so'rovi"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return ConversationHandler.END

    uid = int(query.data.split(":")[1])
    context.user_data['custom_vip_uid'] = uid
    await query.edit_message_text(
        text=f"✍️ <b>Foydalanuvchi (ID: <code>{uid}</code>) uchun VIP kunlar sonini kiriting:</b>\n\n"
             "<i>Masalan:</i> <code>15</code>, <code>45</code> yoki <code>180</code>",
        reply_markup=get_cancel_keyboard("admin_vip_manage"),
        parse_mode='HTML'
    )
    return ADMIN_WAITING_VIP_CUSTOM_DAYS


async def receive_custom_vip_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Qo'lda kiritilgan VIP kunlarni biriktirish"""
    admin_id = update.effective_user.id
    if not db.is_admin(admin_id, ADMINS):
        return ConversationHandler.END

    text = update.message.text.strip()
    if not text.isdigit() or int(text) <= 0:
        await update.message.reply_html(
            "❌ Iltimos, musbat butun son kiriting (masalan: 30):",
            reply_markup=get_cancel_keyboard("admin_vip_manage")
        )
        return ADMIN_WAITING_VIP_CUSTOM_DAYS

    days = int(text)
    uid = context.user_data.get('custom_vip_uid')
    if not uid:
        await update.message.reply_html("❌ Foydalanuvchi ID topilmadi.", reply_markup=get_admin_main_keyboard())
        return ConversationHandler.END

    db.set_user_vip_days(uid, days)
    dur_text = f"{days} kunlik"

    try:
        await context.bot.send_message(
            chat_id=uid,
            text=(
                f"🎉 <b>Tabriklaymiz!</b>\n\n"
                f"⭐️ Sizga admin tomonidan <b>{dur_text} VIP Premium</b> obunasi taqdim etildi!\n"
                f"Endi botdan barcha majburiy kanallarsiz va cheklovlarsiz foydalanishingiz mumkin 🚀"
            ),
            parse_mode='HTML'
        )
    except Exception:
        pass

    context.user_data.pop('custom_vip_uid', None)
    is_maint = db.is_maintenance()
    await update.message.reply_html(
        f"✅ <b>Foydalanuvchiga (ID: <code>{uid}</code>) {dur_text} VIP obuna berildi!</b>",
        reply_markup=get_admin_main_keyboard(is_maint)
    )
    return ConversationHandler.END


async def admin_ads_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Izoh reklamasi menyusi"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    active_ad = db.get_active_ad()
    if active_ad:
        exp_str = f"⏱ Amal qilish muddati: <b>{active_ad['expires_at']}</b>" if active_ad.get('expires_at') else "⏱ Amal qilish muddati: <b>Cheksiz</b>"
        link_str = f"🔗 Havola: <b>{active_ad['ad_link']}</b>" if active_ad.get('ad_link') else "🔗 Havola: <i>Mavjud emas</i>"
        text = (
            "🎯 <b>Izoh Reklamasi Boshqaruvi:</b>\n\n"
            "🟢 <b>Faol Reklama:</b>\n"
            f"📝 Matn: <code>{html.escape(active_ad['ad_text'])}</code>\n"
            f"{link_str}\n"
            f"{exp_str}\n"
        )
        has_active = True
    else:
        text = (
            "🎯 <b>Izoh Reklamasi Boshqaruvi:</b>\n\n"
            "⚪️ <i>Hozirda faol reklama mavjud emas.</i>\n\n"
            "Fayllar izohiga (tagiga) avtomatik ravishda homiy yoki kanalingiz havolasini qo'shishingiz mumkin."
        )
        has_active = False

    try:
        await query.edit_message_text(
            text=text,
            reply_markup=get_admin_ads_keyboard(has_active),
            parse_mode='HTML'
        )
    except Exception:
        pass


async def ad_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Faol izoh reklamasini o'chirish"""
    query = update.callback_query
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    db.disable_caption_ad()
    try:
        await query.answer("🗑 Reklama o'chirildi!", show_alert=True)
    except Exception:
        pass
    await admin_ads_callback(update, context)


async def start_create_ad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yangi izoh reklamasi yaratishni boshlash"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return ConversationHandler.END

    await query.edit_message_text(
        text="📝 <b>Reklama matnini kiriting:</b>\n\n"
             "<i>Masalan:</i> <code>Bizning rasmiy kanalimizga a'zo bo'ling!</code>",
        reply_markup=get_cancel_keyboard("admin_ads"),
        parse_mode='HTML'
    )
    return ADMIN_AD_TEXT


async def receive_ad_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reklama matnini qabul qilish"""
    admin_id = update.effective_user.id
    if not db.is_admin(admin_id, ADMINS):
        return ConversationHandler.END

    context.user_data['new_ad_text'] = update.message.text.strip()
    await update.message.reply_html(
        "🔗 <b>Reklama havolasini (link) yuboring:</b>\n\n"
        "<i>Masalan:</i> <code>https://t.me/mychannel</code>\n\n"
        "<i>Agar link kerak bo'lmasa, quyidagi tugmani bosing:</i>",
        reply_markup=get_admin_ads_skip_link_keyboard()
    )
    return ADMIN_AD_LINK


async def receive_ad_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reklama havolasini qabul qilish"""
    admin_id = update.effective_user.id
    if not db.is_admin(admin_id, ADMINS):
        return ConversationHandler.END

    link = update.message.text.strip()
    if not link.startswith(('http://', 'https://', 't.me/', 'tg://')):
        link = f"https://{link}"

    context.user_data['new_ad_link'] = link
    await update.message.reply_html(
        "⏱ <b>Reklama qancha vaqt amal qilsin?</b>",
        reply_markup=get_admin_ads_duration_keyboard()
    )
    return ADMIN_AD_DURATION_CUSTOM


async def ad_skip_link_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Linksiz reklama davom etish"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return ConversationHandler.END

    context.user_data['new_ad_link'] = None
    await query.edit_message_text(
        text="⏱ <b>Reklama qancha vaqt amal qilsin?</b>",
        reply_markup=get_admin_ads_duration_keyboard(),
        parse_mode='HTML'
    )
    return ADMIN_AD_DURATION_CUSTOM


async def ad_duration_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reklama muddatini tanlash callback"""
    query = update.callback_query
    if not db.is_admin(query.from_user.id, ADMINS):
        return ConversationHandler.END

    dur_val = query.data.split(":", 1)[1]
    if dur_val == "custom":
        await query.edit_message_text(
            text="✍️ <b>Reklama davomiyligini soatlarda kiriting:</b>\n\n<i>Masalan:</i> <code>48</code> (2 kun uchun)",
            reply_markup=get_cancel_keyboard("admin_ads"),
            parse_mode='HTML'
        )
        return ADMIN_AD_DURATION_CUSTOM

    duration_hours = float(dur_val) if dur_val != "none" else None
    ad_text = context.user_data.get('new_ad_text')
    ad_link = context.user_data.get('new_ad_link')

    if ad_text:
        db.set_caption_ad(ad_text=ad_text, ad_link=ad_link, duration_hours=duration_hours)
        try:
            await query.answer("✅ Reklama muvaffaqiyatli saqlandi va faollashtirildi!", show_alert=True)
        except Exception:
            pass

    context.user_data.pop('new_ad_text', None)
    context.user_data.pop('new_ad_link', None)
    await admin_ads_callback(update, context)
    return ConversationHandler.END


async def receive_ad_custom_hours(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Qo'lda kiritilgan reklama soatlarini saqlash"""
    admin_id = update.effective_user.id
    if not db.is_admin(admin_id, ADMINS):
        return ConversationHandler.END

    try:
        hours = float(update.message.text.strip().replace(",", "."))
        if hours <= 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_html(
            "❌ Iltimos, musbat son kiriting (masalan: 24 yoki 48):",
            reply_markup=get_cancel_keyboard("admin_ads")
        )
        return ADMIN_AD_DURATION_CUSTOM

    ad_text = context.user_data.get('new_ad_text')
    ad_link = context.user_data.get('new_ad_link')

    if ad_text:
        db.set_caption_ad(ad_text=ad_text, ad_link=ad_link, duration_hours=hours)

    context.user_data.pop('new_ad_text', None)
    context.user_data.pop('new_ad_link', None)
    is_maint = db.is_maintenance()
    await update.message.reply_html(
        f"✅ Reklama {hours} soatga muvaffaqiyatli o'rnatildi!",
        reply_markup=get_admin_main_keyboard(is_maint)
    )
    return ConversationHandler.END


async def admin_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot statistikasini to'liq ko'rsatish"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    stats = db.get_detailed_stats()

    db_size = "0 KB"
    if os.path.exists(DATABASE_PATH):
        sz = os.path.getsize(DATABASE_PATH)
        if sz >= 1024 * 1024:
            db_size = f"{sz / (1024 * 1024):.2f} MB"
        else:
            db_size = f"{sz / 1024:.1f} KB"

    text = (
        f"📊 <b>Convertor Bot — To'liq Statistika</b>\n\n"
        f"👥 <b>Jami foydalanuvchilar:</b> {stats['total']:,} ta\n"
        f"├ 🟢 <b>Faol a'zolar:</b> {stats['active']:,} ta\n"
        f"├ ⏸ <b>Botni bloklaganlar:</b> {stats['blocked']:,} ta\n"
        f"└ 🔴 <b>Admin bloklagan:</b> {stats['banned']:,} ta\n\n"
        f"✨ <b>Bugun qo'shilganlar:</b> +{stats['today_joined']:,} ta\n"
        f"⚡️ <b>24 soatda faol:</b> {stats['active_24h']:,} ta\n"
        f"📅 <b>7 kunda faol:</b> {stats['active_7d']:,} ta\n\n"
        f"⭐️ <b>VIP foydalanuvchilar:</b> {stats['vip']:,} ta\n"
        f"📢 <b>Majburiy kanallar soni:</b> {stats['channels_count']} ta\n"
        f"💾 <b>Baza fayli hajmi:</b> {db_size}\n"
        f"────────────────────────\n"
        f"🕒 <i>Vaqt: {datetime.now(TASHKENT_TZ).strftime('%d.%m.%Y %H:%M:%S')}</i>"
    )

    try:
        await query.edit_message_text(text=text, reply_markup=get_back_keyboard("admin_back"), parse_mode='HTML')
    except Exception:
        pass


def format_user_card(user: dict) -> str:
    """Foydalanuvchi ma'lumotlarini profil kartasi ko'rinishida formatlash"""
    uid = user.get('user_id')
    raw_name = user.get('full_name') or "Noma'lum"
    fname = html.escape(str(raw_name))
    uname = f"@{user.get('username')}" if user.get('username') else "Mavjud emas"
    lang = user.get('language') or 'uz'
    created = user.get('created_at') or "Noma'lum"
    last_act = user.get('last_active') or "Noma'lum"

    if user.get('is_banned'):
        status = "🔴 Bloklangan (Admin tomonidan)"
    elif user.get('is_active') == 0:
        status = "⏸ Botni bloklagan (Kicked)"
    else:
        status = "🟢 Faol a'zo"

    vip_status = "⭐ Faol VIP" if user.get('is_vip') else "Oddiy"

    return (
        f"👤 <b>Foydalanuvchi Profili</b>\n\n"
        f"🆔 <b>Telegram ID:</b> <code>{uid}</code>\n"
        f"👤 <b>Ismi:</b> {fname}\n"
        f"🌐 <b>Username:</b> {uname}\n"
        f"🇺🇿 <b>Tili:</b> {lang}\n"
        f"📅 <b>Ro'yxatdan o'tgan:</b> {created}\n"
        f"⚡ <b>Oxirgi faollik:</b> {last_act}\n"
        f"📊 <b>Holati:</b> {status}\n"
        f"⭐️ <b>VIP maqomi:</b> {vip_status}"
    )


async def start_search_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchi qidirishni boshlash"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return ConversationHandler.END

    await query.edit_message_text(
        text="🔍 <b>Foydalanuvchini qidirish:</b>\n\n"
             "Qidirilayotgan foydalanuvchining <b>Telegram ID</b> raqamini yoki <b>@username</b>ini kiriting:\n\n"
             "<i>(Bekor qilish uchun /cancel deb yozing)</i>",
        parse_mode='HTML'
    )
    return ADMIN_SEARCH_STATE


async def receive_search_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Qidiruv so'rovini qabul qilish va natijani chiqarish"""
    user_id = update.effective_user.id
    if not db.is_admin(user_id, ADMINS):
        return ConversationHandler.END

    search_text = update.message.text.strip()
    user_data = db.search_user(search_text)

    if not user_data:
        await update.message.reply_html(
            f"❌ <b>'{html.escape(search_text)}'</b> bo'yicha foydalanuvchi topilmadi.\n\n"
            f"ID yoki username to'g'ri ekanligini tekshirib qaytadan kiriting yoki /cancel yozing."
        )
        return ADMIN_SEARCH_STATE

    text = format_user_card(user_data)
    markup = get_user_manage_keyboard(
        user_id=user_data['user_id'],
        is_banned=bool(user_data.get('is_banned', 0)),
        is_vip=bool(user_data.get('is_vip', 0))
    )
    await update.message.reply_html(text=text, reply_markup=markup)
    return ConversationHandler.END


async def user_profile_by_id_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """ID orqali profilni ko'rish"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    target_id = int(query.data.replace("user_search_id_", ""))
    user_data = db.select_user(target_id)
    if not user_data:
        try:
            await query.answer("Foydalanuvchi topilmadi!", show_alert=True)
        except Exception:
            pass
        return

    text = format_user_card(user_data)
    markup = get_user_manage_keyboard(
        user_id=target_id,
        is_banned=bool(user_data.get('is_banned', 0)),
        is_vip=bool(user_data.get('is_vip', 0))
    )
    try:
        await query.edit_message_text(text=text, reply_markup=markup, parse_mode='HTML')
    except Exception:
        pass


async def ban_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchini bloklash (Ban)"""
    query = update.callback_query
    if not db.is_admin(query.from_user.id, ADMINS):
        return
    target_id = int(query.data.replace("ban_", ""))
    db.set_user_banned(target_id, 1)
    try:
        await query.answer("Foydalanuvchi bloklandi!", show_alert=True)
    except Exception:
        pass

    user_data = db.select_user(target_id)
    if user_data:
        text = format_user_card(user_data)
        markup = get_user_manage_keyboard(
            user_id=target_id,
            is_banned=True,
            is_vip=bool(user_data.get('is_vip', 0))
        )
        try:
            await query.edit_message_text(text=text, reply_markup=markup, parse_mode='HTML')
        except Exception:
            pass


async def unban_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchini blokdan chiqarish (Unban)"""
    query = update.callback_query
    if not db.is_admin(query.from_user.id, ADMINS):
        return
    target_id = int(query.data.replace("unban_", ""))
    db.set_user_banned(target_id, 0)
    try:
        await query.answer("Foydalanuvchi blokdan chiqarildi!", show_alert=True)
    except Exception:
        pass

    user_data = db.select_user(target_id)
    if user_data:
        text = format_user_card(user_data)
        markup = get_user_manage_keyboard(
            user_id=target_id,
            is_banned=False,
            is_vip=bool(user_data.get('is_vip', 0))
        )
        try:
            await query.edit_message_text(text=text, reply_markup=markup, parse_mode='HTML')
        except Exception:
            pass


async def vip_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """VIP maqomini berish"""
    query = update.callback_query
    if not db.is_admin(query.from_user.id, ADMINS):
        return
    target_id = int(query.data.replace("vip_", ""))
    db.set_user_vip(target_id, 1)
    try:
        await query.answer("VIP maqomi berildi!", show_alert=True)
    except Exception:
        pass

    user_data = db.select_user(target_id)
    if user_data:
        text = format_user_card(user_data)
        markup = get_user_manage_keyboard(
            user_id=target_id,
            is_banned=bool(user_data.get('is_banned', 0)),
            is_vip=True
        )
        try:
            await query.edit_message_text(text=text, reply_markup=markup, parse_mode='HTML')
        except Exception:
            pass


async def unvip_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """VIP maqomini olib tashlash"""
    query = update.callback_query
    if not db.is_admin(query.from_user.id, ADMINS):
        return
    target_id = int(query.data.replace("unvip_", ""))
    db.set_user_vip(target_id, 0)
    try:
        await query.answer("VIP maqomi olib tashlandi!", show_alert=True)
    except Exception:
        pass

    user_data = db.select_user(target_id)
    if user_data:
        text = format_user_card(user_data)
        markup = get_user_manage_keyboard(
            user_id=target_id,
            is_banned=bool(user_data.get('is_banned', 0)),
            is_vip=False
        )
        try:
            await query.edit_message_text(text=text, reply_markup=markup, parse_mode='HTML')
        except Exception:
            pass


async def start_dm_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Foydalanuvchiga to'g'ridan-to'g'ri xabar yuborishni boshlash"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return ConversationHandler.END

    target_id = int(query.data.replace("dm_", ""))
    context.user_data['dm_target_id'] = target_id

    await query.message.reply_html(
        f"✉️ <b>Foydalanuvchiga xabar yuborish:</b>\n\n"
        f"🆔 Qabul qiluvchi: <code>{target_id}</code>\n\n"
        f"Yuboriladigan xabarni kiriting (matn, rasm, video, fayl yoki ovozli xabar):\n"
        f"<i>(Bekor qilish uchun /cancel deb yozing)</i>"
    )
    return ADMIN_DM_STATE


async def send_dm_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xabarni foydalanuvchiga jo'natish"""
    admin_id = update.effective_user.id
    if not db.is_admin(admin_id, ADMINS):
        return ConversationHandler.END

    target_id = context.user_data.get('dm_target_id')
    if not target_id:
        await update.message.reply_html("❌ Foydalanuvchi IDsi topilmadi.")
        return ConversationHandler.END

    try:
        await context.bot.copy_message(
            chat_id=target_id,
            from_chat_id=update.effective_chat.id,
            message_id=update.message.message_id
        )
        await update.message.reply_html(
            f"✅ <b>Xabar muvaffaqiyatli yuborildi!</b>\n\n"
            f"🆔 Qabul qiluvchi: <code>{target_id}</code>",
            reply_markup=get_admin_main_keyboard()
        )
    except Forbidden:
        db.set_user_active(target_id, 0)
        await update.message.reply_html(
            f"❌ <b>Xabar yetkazilmadi!</b> Foydalanuvchi botni bloklagan (bazada <code>is_active=0</code> qilindi).",
            reply_markup=get_admin_main_keyboard()
        )
    except Exception as e:
        await update.message.reply_html(
            f"❌ <b>Xatolik yuz berdi:</b> {e}",
            reply_markup=get_admin_main_keyboard()
        )

    context.user_data.pop('dm_target_id', None)
    return ConversationHandler.END


async def admin_export_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Baza eksport formatlarini tanlash menyusi"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    await query.edit_message_text(
        text="📁 <b>Ma'lumotlar bazasini yuklab olish</b>\n\n"
             "O'zingizga kerakli eksport formatini tanlang:\n\n"
             "• 📊 <b>Excel (.xlsx):</b> Stillangan, rangli badgeli jadvallar\n"
             "• 💾 <b>SQLite (.sqlite):</b> Barcha jadvallarning to'liq zaxira nusxasi\n"
             "• 📄 <b>CSV (.csv):</b> UTF-8 BOM bilan universal jadval",
        reply_markup=get_admin_export_keyboard(),
        parse_mode='HTML'
    )


async def admin_export_xlsx_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Professional Excel faylini jo'natish"""
    query = update.callback_query
    try:
        await query.answer("Excel tayyorlanmoqda...")
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    excel_file = f"users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    path = db.export_users_to_excel(excel_file)

    if os.path.exists(path):
        with open(path, 'rb') as f:
            await context.bot.send_document(
                chat_id=query.from_user.id,
                document=f,
                filename=f"Convertor_Users_{datetime.now().strftime('%Y_%m_%d')}.xlsx",
                caption="📊 <b>Convertor Bot — Foydalanuvchilar Ro'yxati (Excel)</b>\n\n"
                        "<i>Header: Navy Blue | Freeze Panes | Rangli Holat Badgelari</i>",
                parse_mode='HTML',
                read_timeout=120.0,
                write_timeout=120.0
            )
        try:
            os.remove(path)
        except Exception:
            pass
    else:
        await query.message.reply_html("❌ Excel faylini yaratishda xatolik yuz berdi!")


async def admin_backup_db_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """SQLite zaxirasini jo'natish"""
    query = update.callback_query
    try:
        await query.answer("Zaxira nusxa olinmoqda...")
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    backup_path = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite"
    path = db.backup_database(backup_path)

    if path and os.path.exists(path):
        with open(path, 'rb') as f:
            await context.bot.send_document(
                chat_id=query.from_user.id,
                document=f,
                filename=f"Convertor_DB_{datetime.now().strftime('%Y_%m_%d')}.sqlite",
                caption="💾 <b>Convertor Bot — SQLite Ma'lumotlar Bazasi Zaxira Nusxasi</b>",
                parse_mode='HTML',
                read_timeout=120.0,
                write_timeout=120.0
            )
        try:
            os.remove(path)
        except Exception:
            pass
    else:
        await query.message.reply_html("❌ Zaxira nusxa fayli yaratilmadi!")


async def admin_export_csv_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """CSV eksportini jo'natish"""
    query = update.callback_query
    try:
        await query.answer("CSV fayl tayyorlanmoqda...")
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    csv_path = f"users_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    path = db.export_users_to_csv(csv_path)

    if os.path.exists(path):
        with open(path, 'rb') as f:
            await context.bot.send_document(
                chat_id=query.from_user.id,
                document=f,
                filename=f"Convertor_Users_{datetime.now().strftime('%Y_%m_%d')}.csv",
                caption="📄 <b>Convertor Bot — Foydalanuvchilar Ro'yxati (CSV UTF-8)</b>",
                parse_mode='HTML',
                read_timeout=120.0,
                write_timeout=120.0
            )
        try:
            os.remove(path)
        except Exception:
            pass
    else:
        await query.message.reply_html("❌ CSV faylini yaratishda xatolik yuz berdi!")


async def start_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Broadcast jarayonini boshlash"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return ConversationHandler.END

    await query.edit_message_text(
        text="🚀 <b>Xabar tarqatish (Broadcast)</b>\n\n"
             "Barcha faol foydalanuvchilarga yuboriladigan xabarni yuboring:\n"
             "<i>(Matn, rasm, video, audio yoki forward xabar yuborishingiz mumkin. Bekor qilish uchun /cancel deb yozing)</i>",
        parse_mode='HTML'
    )
    return ADMIN_BROADCAST_STATE


async def receive_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xabarni qabul qilish va tasdiqlash menyusini chiqarish"""
    user_id = update.effective_user.id
    if not db.is_admin(user_id, ADMINS):
        return ConversationHandler.END

    context.user_data['broadcast_msg_id'] = update.message.message_id
    context.user_data['broadcast_chat_id'] = update.message.chat_id
    context.user_data['broadcast_pin'] = False

    await update.message.reply_html(
        "📢 <b>Xabar qabul qilindi!</b>\n\n"
        "Xabarni foydalanuvchilarga pin (qadash) qilishni xohlaysizmi?\n"
        "Sozlab bo'lgach, <b>YUBORISHNI BOSHLASH</b> tugmasini bosing:",
        reply_markup=get_broadcast_confirm_keyboard(pin=False)
    )
    return ADMIN_BROADCAST_CONFIRM


async def toggle_broadcast_pin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Qadash holatini o'zgartirish (toggle)"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return ADMIN_BROADCAST_CONFIRM

    current_pin = context.user_data.get('broadcast_pin', False)
    new_pin = not current_pin
    context.user_data['broadcast_pin'] = new_pin

    try:
        await query.edit_message_reply_markup(reply_markup=get_broadcast_confirm_keyboard(pin=new_pin))
    except Exception:
        pass
    return ADMIN_BROADCAST_CONFIRM


async def execute_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xabarni barcha faol foydalanuvchilarga xavfsiz va jonli progress bilan tarqatish"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    admin_id = query.from_user.id
    if not db.is_admin(admin_id, ADMINS):
        return ConversationHandler.END

    broadcast_msg_id = context.user_data.get('broadcast_msg_id')
    broadcast_chat_id = context.user_data.get('broadcast_chat_id')
    should_pin = context.user_data.get('broadcast_pin', False)

    if not broadcast_msg_id or not broadcast_chat_id:
        await query.edit_message_text("❌ Xabar ma'lumotlari topilmadi. Qaytadan urinib ko'ring.")
        return ConversationHandler.END

    user_ids = db.select_active_user_ids()
    total_users = len(user_ids)

    if total_users == 0:
        await query.edit_message_text("❌ Bazada faol foydalanuvchilar topilmadi.")
        return ConversationHandler.END

    status_msg = await query.edit_message_text(
        f"🚀 <b>Xabar tarqatish boshlandi...</b>\n\n"
        f"👥 <b>Qabul qiluvchilar:</b> {total_users} ta\n"
        f"📌 <b>Qadash:</b> {'Ha' if should_pin else 'Yo\'q'}\n"
        f"⏳ <i>Jarayon davom etmoqda...</i>",
        parse_mode='HTML'
    )

    success = 0
    blocked = 0
    failed = 0
    start_time = time.time()
    last_update_time = time.time()

    for idx, target_id in enumerate(user_ids):
        try:
            sent_msg = await context.bot.copy_message(
                chat_id=target_id,
                from_chat_id=broadcast_chat_id,
                message_id=broadcast_msg_id
            )
            success += 1
            if should_pin and sent_msg:
                try:
                    await context.bot.pin_chat_message(
                        chat_id=target_id,
                        message_id=sent_msg.message_id,
                        disable_notification=True
                    )
                except Exception:
                    pass
        except Forbidden:
            blocked += 1
            db.set_user_active(target_id, 0)
        except RetryAfter as e:
            logger.warning(f"Broadcast flood limit. Waiting {e.retry_after + 1}s...")
            await asyncio.sleep(e.retry_after + 1)
            try:
                sent_msg = await context.bot.copy_message(
                    chat_id=target_id,
                    from_chat_id=broadcast_chat_id,
                    message_id=broadcast_msg_id
                )
                success += 1
                if should_pin and sent_msg:
                    try:
                        await context.bot.pin_chat_message(
                            chat_id=target_id,
                            message_id=sent_msg.message_id,
                            disable_notification=True
                        )
                    except Exception:
                        pass
            except Exception:
                failed += 1
        except Exception as e:
            failed += 1
            logger.debug(f"Broadcast error user {target_id}: {e}")

        await asyncio.sleep(0.04)

        now = time.time()
        if now - last_update_time >= 5.0 or (idx + 1) == total_users:
            last_update_time = now
            percent = int(((idx + 1) / total_users) * 100)
            bar = "█" * (percent // 10) + "░" * (10 - (percent // 10))
            elapsed = int(now - start_time)
            try:
                await status_msg.edit_text(
                    f"🚀 <b>Xabar tarqatilmoqda...</b>\n\n"
                    f"[{bar}] <b>{percent}%</b> ({idx + 1}/{total_users})\n\n"
                    f"✅ <b>Muvaffaqiyatli:</b> {success} ta\n"
                    f"⏸ <b>Botni bloklagan:</b> {blocked} ta\n"
                    f"⚠️ <b>Xatolik:</b> {failed} ta\n"
                    f"⏱ <b>O'tgan vaqt:</b> {elapsed} soniya",
                    parse_mode='HTML'
                )
            except Exception:
                pass

    elapsed = int(time.time() - start_time)
    db.update_status(active=success, block=blocked)

    report_text = (
        f"✅ <b>Xabar tarqatish yakunlandi!</b>\n\n"
        f"👥 <b>Jami rejalashtirilgan:</b> {total_users} ta\n"
        f"✅ <b>Muvaffaqiyatli yetkazildi:</b> {success} ta\n"
        f"⏸ <b>Botni bloklaganlar:</b> {blocked} ta <i>(bazada faolsizlantirildi)</i>\n"
        f"⚠️ <b>Yetib bormagan / Xatolik:</b> {failed} ta\n"
        f"⏱ <b>Sarflangan vaqt:</b> {elapsed} soniya"
    )

    try:
        await status_msg.edit_text(text=report_text, reply_markup=get_back_keyboard("admin_back"), parse_mode='HTML')
    except Exception:
        pass

    context.user_data.pop('broadcast_msg_id', None)
    context.user_data.pop('broadcast_chat_id', None)
    context.user_data.pop('broadcast_pin', None)
    return ConversationHandler.END


async def admin_subadmins_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sub-adminlar ro'yxatini ko'rsatish"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    sub_admins = db.get_sub_admins()
    text = (
        f"👑 <b>Sub-Adminlar Boshqaruvi</b>\n\n"
        f"Asosiy adminlar (config): <code>{ADMINS}</code>\n"
        f"Qo'shimcha sub-adminlar soni: <b>{len(sub_admins)} ta</b>\n\n"
        f"<i>Yordamchi admin qo'shish yoki o'chirish uchun quyidagi tugmalardan foydalaning:</i>"
    )
    await query.edit_message_text(
        text=text,
        reply_markup=get_sub_admins_keyboard(sub_admins),
        parse_mode='HTML'
    )


async def del_subadmin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sub-adminni o'chirish"""
    query = update.callback_query
    if query.from_user.id not in ADMINS:
        try:
            await query.answer("Faqat asosiy admin sub-adminlarni o'chira oladi!", show_alert=True)
        except Exception:
            pass
        return

    sub_id = int(query.data.replace("del_subadmin_", ""))
    db.delete_sub_admin(sub_id)
    try:
        await query.answer("Sub-admin o'chirildi!", show_alert=True)
    except Exception:
        pass

    sub_admins = db.get_sub_admins()
    text = (
        f"👑 <b>Sub-Adminlar Boshqaruvi</b>\n\n"
        f"Asosiy adminlar (config): <code>{ADMINS}</code>\n"
        f"Qo'shimcha sub-adminlar soni: <b>{len(sub_admins)} ta</b>"
    )
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=get_sub_admins_keyboard(sub_admins),
            parse_mode='HTML'
        )
    except Exception:
        pass


async def start_add_subadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sub-admin qo'shishni boshlash"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if query.from_user.id not in ADMINS:
        try:
            await query.answer("Faqat asosiy admin yangi sub-admin qo'sha oladi!", show_alert=True)
        except Exception:
            pass
        return ConversationHandler.END

    await query.edit_message_text(
        text="➕ <b>Yangi sub-admin qo'shish:</b>\n\n"
             "Yordamchi admin bo'ladigan foydalanuvchining <b>Telegram ID</b> raqamini kiriting:\n\n"
             "<i>(Bekor qilish uchun /cancel deb yozing)</i>",
        parse_mode='HTML'
    )
    return ADMIN_ADD_SUBADMIN_STATE


async def receive_subadmin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sub-admin ID sini qabul qilish va saqlash"""
    admin_id = update.effective_user.id
    if admin_id not in ADMINS:
        return ConversationHandler.END

    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_html(
            "❌ <b>Noto'g'ri format!</b> Faqat raqamlardan iborat Telegram ID kiriting:\n"
            "<i>(Masalan: <code>123456789</code> yoki /cancel)</i>"
        )
        return ADMIN_ADD_SUBADMIN_STATE

    new_sub_id = int(text)
    success = db.add_sub_admin(new_sub_id, added_by=admin_id)

    if success:
        await update.message.reply_html(
            f"✅ <b>Yangi sub-admin muvaffaqiyatli qo'shildi!</b>\n\n"
            f"🆔 <b>Admin ID:</b> <code>{new_sub_id}</code>",
            reply_markup=get_admin_main_keyboard()
        )
    else:
        await update.message.reply_html(
            f"⚠️ Ushbu foydalanuvchi allaqachon sub-admin yoki xatolik yuz berdi.",
            reply_markup=get_admin_main_keyboard()
        )
    return ConversationHandler.END


async def admin_maintenance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Texnik profilaktika menyusi"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    is_maint = db.is_maintenance()
    status_text = "🔴 <b>YOQILGAN (Oddiy a'zolar uchun bot to'xtatilgan)</b>" if is_maint else "🟢 <b>O'CHIRILGAN (Bot barcha uchun faol)</b>"

    text = (
        f"🛠 <b>Texnik Profilaktika Rejimi (Maintenance Mode)</b>\n\n"
        f"Joriy holat: {status_text}\n\n"
        f"<i>Agar texnik rejim yoqilsa, oddiy foydalanuvchilar botdan vaqtinchalik foydalana olishmaydi. "
        f"Adminlar esa botdan to'siqsiz foydalanishda davom etishadi.</i>"
    )
    await query.edit_message_text(
        text=text,
        reply_markup=get_maintenance_keyboard(is_maint),
        parse_mode='HTML'
    )


async def toggle_maintenance_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Texnik rejimni yoqish / o'chirish va tugmani dinamik yangilash"""
    query = update.callback_query
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    cur = db.is_maintenance()
    new_state = not cur
    db.set_maintenance(new_state)

    status_msg = "yoqildi 🔴" if new_state else "o'chirildi 🟢"
    try:
        await query.answer(f"✅ Texnik rejim {status_msg}!", show_alert=True)
    except Exception:
        pass

    try:
        await query.edit_message_text(
            text="👑 <b>Admin Boshqaruv Paneli:</b>\n\n"
                 "Quyidagi bo'limlardan birini tanlang:",
            reply_markup=get_admin_main_keyboard(new_state),
            parse_mode='HTML'
        )
    except Exception:
        pass


async def admin_list_channels_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Majburiy a'zolik kanallari ro'yxatini ko'rsatish"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    channels = db.get_channels_from_db()
    if not channels:
        await query.edit_message_text(
            text="📢 <b>Hozirda hech qanday majburiy obuna kanali qo'shilmagan.</b>",
            reply_markup=get_channels_manage_keyboard([]),
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
    if not db.is_admin(query.from_user.id, ADMINS):
        return

    channel_db_id = int(query.data.replace("del_channel_", ""))
    db.delete_channel_by_id(channel_db_id)
    try:
        await query.answer("Kanal o'chirildi")
    except Exception:
        pass

    _sub_cache.clear()

    channels = db.get_channels_from_db()
    if not channels:
        await query.edit_message_text(
            text="✅ <b>Kanal o'chirildi. Hozirda kanallar qolmadi.</b>",
            reply_markup=get_channels_manage_keyboard([]),
            parse_mode='HTML'
        )
    else:
        await query.edit_message_text(
            text="✅ <b>Kanal muvaffaqiyatli o'chirildi!</b>\n\nQolgan kanallar ro'yxati:",
            reply_markup=get_channels_manage_keyboard(channels),
            parse_mode='HTML'
        )


async def start_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kanal qo'shishni boshlash"""
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    if not db.is_admin(query.from_user.id, ADMINS):
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
        _sub_cache.clear()
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

    context.user_data.clear()
    is_adm = db.is_admin(user_id, ADMINS)
    await update.message.reply_html(
        "❌ <b>Amal bekor qilindi.</b>",
        reply_markup=get_admin_main_keyboard(db.is_maintenance()) if is_adm else None
    )
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

    async def post_init(app):
        try:
            await app.bot.set_chat_menu_button(menu_button=MenuButtonDefault())
            logger.info("✅ Telegram Menu Button standart holatga qaytarildi.")
        except Exception as e:
            logger.warning(f"Menu button set error: {e}")

    application = (
        ApplicationBuilder()
        .token(API_TOKEN)
        .request(req)
        .concurrent_updates(True)
        .post_init(post_init)
        .build()
    )

    # Xatoliklarni global ushlash
    application.add_error_handler(global_error_handler)

    # 0. Telegram bloklaganlarni / qayta yoqqanlarni kuzatish (my_chat_member)
    application.add_handler(ChatMemberHandler(chat_member_update_handler, ChatMemberHandler.MY_CHAT_MEMBER))

    # 1. Admin Broadcast Conversation
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_broadcast, pattern="^admin_broadcast$")],
        states={
            ADMIN_BROADCAST_STATE: [
                MessageHandler(filters.ALL & ~filters.COMMAND, receive_broadcast_message)
            ],
            ADMIN_BROADCAST_CONFIRM: [
                CallbackQueryHandler(toggle_broadcast_pin, pattern="^toggle_broadcast_pin$"),
                CallbackQueryHandler(execute_broadcast, pattern="^start_broadcast_now$")
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CallbackQueryHandler(admin_back_callback, pattern="^admin_back$")
        ],
        per_message=False
    )
    application.add_handler(broadcast_conv)

    # 2. Admin User Search Conversation
    search_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_search_user, pattern="^admin_search_user$")],
        states={
            ADMIN_SEARCH_STATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_search_query)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CallbackQueryHandler(admin_back_callback, pattern="^admin_back$")
        ],
        per_message=False
    )
    application.add_handler(search_conv)

    # 3. Admin DM Conversation
    dm_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_dm_user, pattern="^dm_(\\d+)$")],
        states={
            ADMIN_DM_STATE: [
                MessageHandler(filters.ALL & ~filters.COMMAND, send_dm_message)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CallbackQueryHandler(admin_back_callback, pattern="^admin_back$")
        ],
        per_message=False
    )
    application.add_handler(dm_conv)

    # 4. Admin Add Sub-Admin Conversation
    add_subadmin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_add_subadmin, pattern="^admin_add_subadmin$")],
        states={
            ADMIN_ADD_SUBADMIN_STATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_subadmin_id)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CallbackQueryHandler(admin_back_callback, pattern="^admin_back$")
        ],
        per_message=False
    )
    application.add_handler(add_subadmin_conv)

    # 5. Admin Add Channel Conversation
    add_channel_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_add_channel, pattern="^admin_add_channel$")],
        states={
            ADMIN_ADD_CH_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel_name)],
            ADMIN_ADD_CH_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel_id)],
            ADMIN_ADD_CH_LINK: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_channel_link)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CallbackQueryHandler(admin_back_callback, pattern="^admin_back$")
        ],
        per_message=False
    )
    application.add_handler(add_channel_conv)

    # 6. Admin Add VIP User Conversation
    add_vip_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_add_vip_user, pattern="^admin_add_vip_user$"),
            CallbackQueryHandler(start_custom_vip_days, pattern="^adm_give_vip_custom:(\\d+)$")
        ],
        states={
            ADMIN_WAITING_VIP_USER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_vip_user_input)
            ],
            ADMIN_WAITING_VIP_CUSTOM_DAYS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_custom_vip_days)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CallbackQueryHandler(admin_vip_manage_callback, pattern="^admin_vip_manage$"),
            CallbackQueryHandler(admin_back_callback, pattern="^admin_back$")
        ],
        per_message=False
    )
    application.add_handler(add_vip_conv)

    # 7. Admin Caption Ads Conversation
    ads_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_create_ad, pattern="^ad_create$")],
        states={
            ADMIN_AD_TEXT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_text)
            ],
            ADMIN_AD_LINK: [
                CallbackQueryHandler(ad_skip_link_callback, pattern="^ad_skip_link$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_link)
            ],
            ADMIN_AD_DURATION_CUSTOM: [
                CallbackQueryHandler(ad_duration_callback, pattern="^ad_dur:(.+)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ad_custom_hours)
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CallbackQueryHandler(admin_ads_callback, pattern="^admin_ads$"),
            CallbackQueryHandler(admin_back_callback, pattern="^admin_back$")
        ],
        per_message=False
    )
    application.add_handler(ads_conv)

    # Buyruqlar
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("admin", admin_command))
    application.add_handler(CommandHandler("cancel", cancel_command))

    # Foydalanuvchi fayllarini qabul qilish (Rasm, Hujjat, Video, Audio, Voice, Animatsiya)
    application.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.ANIMATION, handle_user_files))

    # Matnli xabarlarni qabul qilish (Arxiv paroli va boshqalar)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))

    # Callback Query Handlers - Asosiy Foydalanuvchi Amallari
    application.add_handler(CallbackQueryHandler(check_subscription_callback, pattern="^check_subscription$"))
    application.add_handler(CallbackQueryHandler(make_pdf_callback, pattern="^action_make_pdf$"))
    application.add_handler(CallbackQueryHandler(make_pdf_to_images_callback, pattern="^action_pdf_to_images$"))
    application.add_handler(CallbackQueryHandler(merge_pdf_callback, pattern="^action_merge_pdf$"))
    application.add_handler(CallbackQueryHandler(compress_pdf_callback, pattern="^action_compress_pdf$"))
    application.add_handler(CallbackQueryHandler(office_to_pdf_callback, pattern="^action_office_to_pdf$"))
    application.add_handler(CallbackQueryHandler(ocr_callback, pattern="^action_ocr$"))
    application.add_handler(CallbackQueryHandler(text_to_pdf_callback, pattern="^action_text_to_pdf$"))
    application.add_handler(CallbackQueryHandler(media_to_mp3_callback, pattern="^action_media_to_mp3$"))
    application.add_handler(CallbackQueryHandler(enhance_audio_callback, pattern="^action_enhance_audio$"))
    application.add_handler(CallbackQueryHandler(enhance_asmr_callback, pattern="^action_enhance_asmr$"))
    application.add_handler(CallbackQueryHandler(video_to_gif_callback, pattern="^action_video_to_gif$"))
    application.add_handler(CallbackQueryHandler(unzip_callback, pattern="^action_unzip$"))
    application.add_handler(CallbackQueryHandler(make_zip_callback, pattern="^action_make_zip$"))
    application.add_handler(CallbackQueryHandler(clear_files_callback, pattern="^action_clear_files$"))

    # Admin Callback Handlers - 1:1 Target Standart
    application.add_handler(CallbackQueryHandler(admin_back_callback, pattern="^admin_back$"))
    application.add_handler(CallbackQueryHandler(admin_close_callback, pattern="^admin_close$"))
    application.add_handler(CallbackQueryHandler(admin_stats_callback, pattern="^admin_stats$"))
    application.add_handler(CallbackQueryHandler(admin_platform_stats_callback, pattern="^admin_platform_stats$"))
    application.add_handler(CallbackQueryHandler(admin_server_health_callback, pattern="^admin_server_health$"))
    application.add_handler(CallbackQueryHandler(admin_cache_callback, pattern="^admin_cache$"))
    application.add_handler(CallbackQueryHandler(admin_clean_disk_callback, pattern="^admin_clean_disk$"))
    application.add_handler(CallbackQueryHandler(admin_vip_manage_callback, pattern="^admin_vip_manage$"))
    application.add_handler(CallbackQueryHandler(admin_list_vip_users_callback, pattern="^admin_list_vip_users$"))
    application.add_handler(CallbackQueryHandler(adm_pick_vip_callback, pattern="^adm_pick_vip:(\\d+)$"))
    application.add_handler(CallbackQueryHandler(adm_give_vip_callback, pattern="^adm_give_vip:(\\d+):(\\d+)$"))
    application.add_handler(CallbackQueryHandler(adm_revoke_vip_callback, pattern="^adm_revoke_vip:(\\d+)$"))
    application.add_handler(CallbackQueryHandler(admin_ads_callback, pattern="^admin_ads$"))
    application.add_handler(CallbackQueryHandler(ad_delete_callback, pattern="^ad_delete$"))
    application.add_handler(CallbackQueryHandler(ad_duration_callback, pattern="^ad_dur:(.+)$"))
    application.add_handler(CallbackQueryHandler(admin_export_menu_callback, pattern="^admin_export$"))
    application.add_handler(CallbackQueryHandler(admin_export_xlsx_callback, pattern="^admin_export_xlsx$"))
    application.add_handler(CallbackQueryHandler(admin_backup_db_callback, pattern="^admin_backup_db$"))
    application.add_handler(CallbackQueryHandler(admin_export_csv_callback, pattern="^admin_export_csv$"))
    application.add_handler(CallbackQueryHandler(admin_maintenance_callback, pattern="^admin_maintenance$"))
    application.add_handler(CallbackQueryHandler(toggle_maintenance_callback, pattern="^toggle_maintenance$"))
    application.add_handler(CallbackQueryHandler(admin_subadmins_callback, pattern="^admin_subadmins$"))
    application.add_handler(CallbackQueryHandler(del_subadmin_callback, pattern="^del_subadmin_(\\d+)$"))
    application.add_handler(CallbackQueryHandler(user_profile_by_id_callback, pattern="^user_search_id_(\\d+)$"))
    application.add_handler(CallbackQueryHandler(ban_user_callback, pattern="^ban_(\\d+)$"))
    application.add_handler(CallbackQueryHandler(unban_user_callback, pattern="^unban_(\\d+)$"))
    application.add_handler(CallbackQueryHandler(vip_user_callback, pattern="^vip_(\\d+)$"))
    application.add_handler(CallbackQueryHandler(unvip_user_callback, pattern="^unvip_(\\d+)$"))
    application.add_handler(CallbackQueryHandler(admin_list_channels_callback, pattern="^admin_channels$"))
    application.add_handler(CallbackQueryHandler(admin_delete_channel_callback, pattern="^del_channel_(\\d+)$"))

    logger.info("Bot muvaffaqiyatli ishga tushdi va xabarlarni kutmoqda.")
    application.run_polling(
        drop_pending_updates=True,
        bootstrap_retries=-1,
        poll_interval=1.0,
        timeout=30
    )


if __name__ == '__main__':
    main()
