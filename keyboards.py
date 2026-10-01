from telegram import InlineKeyboardMarkup, InlineKeyboardButton

def get_subscription_keyboard(channels: list) -> InlineKeyboardMarkup:
    """Majburiy a'zolik kanallari tugmalari"""
    keyboard = []
    for ch in channels:
        name = ch[0]
        link = ch[2]
        keyboard.append([InlineKeyboardButton(f"📢 {name}", url=link)])

    keyboard.append([InlineKeyboardButton("🔄 A'zolikni tekshirish", callback_data="check_subscription")])
    return InlineKeyboardMarkup(keyboard)

def get_file_action_keyboard(photo_count: int, doc_count: int, pdf_count: int = 0, zip_count: int = 0) -> InlineKeyboardMarkup:
    """Fayllar yuklangandan keyingi harakatlar menyusi"""
    buttons = []
    
    # 1. Rasmlar bo'lsa -> PDF qilish tugmasi
    if photo_count > 0:
        buttons.append([InlineKeyboardButton(f"📄 Rasmlarni PDF qilish ({photo_count} ta rasm)", callback_data="action_make_pdf")])
        
    # 2. PDF fayl bo'lsa -> Rasmlarga ajratish (PDF to Image) tugmasi
    if pdf_count > 0:
        buttons.append([InlineKeyboardButton(f"🖼 PDFni Rasmlarga ajratish ({pdf_count} ta PDF)", callback_data="action_pdf_to_images")])

    # 3. ZIP fayl bo'lsa -> Arxivdan chiqarish (Unzip) tugmasi
    if zip_count > 0:
        buttons.append([InlineKeyboardButton(f"📂 ZIP arxivni ochish ({zip_count} ta ZIP)", callback_data="action_unzip")])

    # 4. Fayllar mavjud bo'lsa -> ZIP arxivlash va tozalash
    total = photo_count + doc_count + pdf_count + zip_count
    if total > 0:
        if photo_count > 0 or doc_count > 0 or pdf_count > 0:
            buttons.append([InlineKeyboardButton(f"🗜 Barchasini ZIP qilish ({total} ta fayl)", callback_data="action_make_zip")])
        buttons.append([InlineKeyboardButton("🗑 Barchasini tozalash", callback_data="action_clear_files")])

    return InlineKeyboardMarkup(buttons)

def get_admin_main_keyboard() -> InlineKeyboardMarkup:
    """Admin boshqaruv paneli menyusi"""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Bot statistikasi", callback_data="admin_stats"),
            InlineKeyboardButton("📤 Xabar yuborish", callback_data="admin_broadcast")
        ],
        [
            InlineKeyboardButton("➕ Kanal qo'shish", callback_data="admin_add_channel"),
            InlineKeyboardButton("📋 Kanallar ro'yxati", callback_data="admin_list_channels")
        ],
        [
            InlineKeyboardButton("💾 SQLite (.db)", callback_data="admin_backup_db"),
            InlineKeyboardButton("📑 Excel (.xlsx)", callback_data="admin_backup_xlsx")
        ]
    ])

def get_back_keyboard(callback_data: str = "admin_back") -> InlineKeyboardMarkup:
    """Ortga qaytish tugmasi"""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Bosh menyuga qaytish", callback_data=callback_data)]
    ])

def get_channels_manage_keyboard(channels: list) -> InlineKeyboardMarkup:
    """Kanallarni ko'rish va o'chirish tugmalari"""
    buttons = []
    for ch in channels:
        name = ch[0]
        db_id = ch[3] if len(ch) > 3 else ch[1]
        buttons.append([
            InlineKeyboardButton(f"📢 {name}", url=ch[2]),
            InlineKeyboardButton("❌ O'chirish", callback_data=f"del_channel_{db_id}")
        ])
    
    buttons.append([InlineKeyboardButton("◀️ Ortga", callback_data="admin_back")])
    return InlineKeyboardMarkup(buttons)
