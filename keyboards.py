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

def get_file_action_keyboard(
    photo_count: int = 0,
    doc_count: int = 0,
    pdf_count: int = 0,
    archive_count: int = 0,
    media_count: int = 0,
    text_count: int = 0,
    office_count: int = 0,
    video_count: int = 0
) -> InlineKeyboardMarkup:
    """Fayllar yuklangandan keyingi aqlli moslashuvchan harakatlar menyusi"""
    buttons = []
    
    # 1. Rasmlar bo'lsa
    if photo_count > 0:
        buttons.append([InlineKeyboardButton(f"📄 Rasmlarni PDF qilish ({photo_count} ta)", callback_data="action_make_pdf")])
        buttons.append([InlineKeyboardButton("🔍 Matnni ajratib olish (OCR)", callback_data="action_ocr")])

    # 2. Office hujjatlari (Word, Excel, PowerPoint) bo'lsa
    if office_count > 0:
        buttons.append([InlineKeyboardButton(f"📄 Office ➡️ PDF qilish ({office_count} ta hujjat)", callback_data="action_office_to_pdf")])
        
    # 3. PDF fayllar bo'lsa
    if pdf_count >= 2:
        buttons.append([InlineKeyboardButton(f"📑 PDF larni birlashtirish ({pdf_count} ta)", callback_data="action_merge_pdf")])
        buttons.append([
            InlineKeyboardButton("🖼 Rasmlarga ajratish", callback_data="action_pdf_to_images"),
            InlineKeyboardButton("🗜 Siqish (Compress)", callback_data="action_compress_pdf")
        ])
    elif pdf_count == 1:
        buttons.append([
            InlineKeyboardButton("🖼 Rasmlarga ajratish", callback_data="action_pdf_to_images"),
            InlineKeyboardButton("🗜 Siqish (Compress)", callback_data="action_compress_pdf")
        ])
        buttons.append([InlineKeyboardButton("🔍 Matnni ajratib olish (OCR)", callback_data="action_ocr")])

    # 4. Audio / Video media bo'lsa
    if media_count > 0:
        media_btns = [InlineKeyboardButton(f"🎵 MP3 ga aylantirish ({media_count} ta)", callback_data="action_media_to_mp3")]
        if video_count > 0:
            media_btns.append(InlineKeyboardButton("🎞 GIF qilish", callback_data="action_video_to_gif"))
        buttons.append(media_btns)

    # 5. Matn / Kod fayllari bo'lsa
    if text_count > 0:
        buttons.append([InlineKeyboardButton(f"📄 Matnni PDF ga o'tkazish ({text_count} ta)", callback_data="action_text_to_pdf")])

    # 6. Arxiv bo'lsa
    if archive_count > 0:
        buttons.append([InlineKeyboardButton(f"📂 Arxivni ochish ({archive_count} ta)", callback_data="action_unzip")])

    # 7. Umumiy fayllar bo'lsa -> ZIP qilish va Tozalash
    total = photo_count + doc_count + pdf_count + archive_count + media_count + text_count + office_count
    if total > 0:
        if photo_count > 0 or doc_count > 0 or pdf_count > 0 or media_count > 0 or text_count > 0 or office_count > 0:
            buttons.append([InlineKeyboardButton(f"🗜 Barchasini ZIP qilish ({total} ta)", callback_data="action_make_zip")])
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
