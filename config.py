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

# Xabarlar
NOT_SUB_MESSAGE = "Botdan foydalanish uchun quyidagi kanallarga a'zo bo'ling:"
WELCOME_MESSAGE = (
    "<b>Assalomu alaykum!</b>\n\n"
    "📁 <b>Universal Fayl Konvertor Bot</b>ga xush kelibsiz.\n\n"
    "⚡ <b>Bot imkoniyatlari:</b>\n"
    "• 🖼 <b>Rasmlarni PDF qilish</b> — Rasmlarni bitta PDF hujjatga aylantirish;\n"
    "• 📄 <b>PDF ni Rasmlarga ajratish</b> — Sahifalarni sifatli foto-albom (group) qilib olish;\n"
    "• 📂 <b>ZIP arxivni ochish (Unzip)</b> — Arxiv ichidagi barcha fayl va rasmlarni chiqarish;\n"
    "• 🗜 <b>Fayllarni ZIP qilish</b> — Istalgan fayllarni arxivlash.\n\n"
    "<i>Boshlash uchun menga rasm, PDF yoki ZIP fayllaringizni yuboring!</i>"
)
