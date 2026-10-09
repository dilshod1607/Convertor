import os
from dotenv import load_dotenv

# .env faylini yuklash
load_dotenv()

# Telegram Bot Token
API_TOKEN = os.getenv("BOT_TOKEN")
if not API_TOKEN:
    raise ValueError("BOT_TOKEN muhit o'zgaruvchisi topilmadi! Iltimos, .env faylida BOT_TOKEN ni belgilang.")

# Adminlar ro'yxati (Telegram user ID lari)
raw_admins = os.getenv("ADMINS", "5391341271")
ADMINS = [int(x.strip()) for x in raw_admins.split(",") if x.strip().isdigit()]

# Log/Statistika kanali (ixtiyoriy)
LOG_CHANNEL_ID = os.getenv("LOG_CHANNEL_ID", "")

# Papka va Baza yo'llari
DATABASE_PATH = os.getenv("DATABASE_PATH", "database.db")
DOCUMENTS_DIR = os.getenv("DOCUMENTS_DIR", "documents")

# Telegram Mini App (WebApp) URL
WEBAPP_URL = os.getenv("WEBAPP_URL", "https://16lyrics.duckdns.org/convertor/")

# Xabarlar
NOT_SUB_MESSAGE = "Botdan foydalanish uchun quyidagi kanallarga a'zo bo'ling:"
WELCOME_MESSAGE = (
    "<b>Assalomu alaykum!</b>\n\n"
    "📁 <b>Universal Fayl Konvertor Bot</b>ga xush kelibsiz.\n\n"
    "⚡️ <b>Botning barcha imkoniyatlari:</b>\n"
    "• 📄 <b>Rasmlar ➡️ PDF:</b> Rasmlarni sifatli bitta PDF hujjatga aylantirish;\n"
    "• 📑 <b>PDF Tools:</b> Bir nechta PDF larni birlashtirish, hajmini siqish (Compress) yoki sahifalarni foto-albom qilib ajratish;\n"
    "• 🎵 <b>Media ➡️ MP3:</b> Video va audio fayllarni 192kbps HD MP3 ga o'tkazish;\n"
    "• 📝 <b>Matn ➡️ PDF:</b> Matnli va dastur kod fayllarini formatlangan PDF ga aylantirish;\n"
    "• 📂 <b>Universal Arxivator:</b> ZIP, RAR, 7Z, TAR arxivlarni ochish (parolli arxivlarni ham ochadi);\n"
    "• 🗜 <b>Fayllarni ZIP qilish:</b> Istalgan fayllarni tezkor arxivlash.\n\n"
    "<i>Boshlash uchun menga istalgan fayl, rasm, video, audio yoki arxiv yuboring!</i>"
)
