import os
import sys
import time
import zipfile
import tarfile
import shutil
import logging
import subprocess
import gc
from typing import Optional, List, Dict, Tuple, Any
from PIL import Image, ImageOps
import pypdfium2 as pdfium
import pymupdf

# iPhone HEIC/HEIF rasmlarini avtomatik qo'llab-quvvatlash
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

# OCR (Matn tanish) Tesseract sozlamalari
try:
    import pytesseract
    _tess_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "/usr/bin/tesseract"
    ]
    for tp in _tess_paths:
        if os.path.exists(tp):
            pytesseract.pytesseract.tesseract_cmd = tp
            break
except ImportError:
    pytesseract = None

try:
    import pyzipper
except ImportError:
    pyzipper = None

try:
    import py7zr
except ImportError:
    py7zr = None

try:
    import rarfile
    # Unrar / 7z dasturini avtomatik aniqlash va sozlash
    _possible_unrar_paths = [
        r"C:\Program Files\WinRAR\UnRAR.exe",
        r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
        r"C:\Program Files\WinRAR\Rar.exe",
        r"C:\Program Files (x86)\WinRAR\Rar.exe",
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]
    for cmd in ["unrar", "rar", "7z"]:
        which_path = shutil.which(cmd)
        if which_path:
            _possible_unrar_paths.insert(0, which_path)

    for p in _possible_unrar_paths:
        if os.path.exists(p):
            rarfile.UNRAR_TOOL = p
            dir_name = os.path.dirname(p)
            if dir_name not in os.environ.get("PATH", ""):
                os.environ["PATH"] = dir_name + os.pathsep + os.environ.get("PATH", "")
            break
except ImportError:
    rarfile = None

try:
    import imageio_ffmpeg
    FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_PATH = shutil.which("ffmpeg")

def get_libreoffice_cmd() -> str:
    """LibreOffice / Soffice dasturini topish"""
    for cmd in ["libreoffice", "soffice"]:
        which_p = shutil.which(cmd)
        if which_p:
            return which_p
    win_paths = [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"
    ]
    for p in win_paths:
        if os.path.exists(p):
            return p
    return "libreoffice"

LIBREOFFICE_BIN = get_libreoffice_cmd()

logger = logging.getLogger(__name__)


class ArchivePasswordRequired(Exception):
    """Arxiv parol bilan himoyalangan va parol kiritilmagan"""
    pass


class ArchiveWrongPassword(Exception):
    """Kiritilgan parol noto'g'ri"""
    pass


def get_user_dir(base_dir: str, user_id: int) -> str:
    """Foydalanuvchi uchun alohida xavfsiz vaqtinchalik papka yo'lini yaratadi"""
    user_path = os.path.join(base_dir, str(user_id))
    os.makedirs(user_path, exist_ok=True)
    return user_path


def force_garbage_collection():
    """Xotirani majburiy bo'shatish"""
    try:
        gc.collect()
    except Exception:
        pass


def is_archive_encrypted(archive_path: str) -> bool:
    """Arxiv fayl parol bilan himoyalanganligini aniqlash"""
    if not os.path.exists(archive_path):
        return False
    lower_path = archive_path.lower()
    try:
        # 1. ZIP
        zip_module = pyzipper if pyzipper else zipfile
        if lower_path.endswith('.zip') or zip_module.is_zipfile(archive_path):
            if pyzipper:
                with pyzipper.AESZipFile(archive_path, 'r') as zipf:
                    return any(bool(m.flag_bits & 0x1) for m in zipf.infolist())
            else:
                with zipfile.ZipFile(archive_path, 'r') as zipf:
                    return any(bool(m.flag_bits & 0x1) for m in zipf.infolist())

        # 2. 7Z
        elif lower_path.endswith('.7z') and py7zr:
            if py7zr.is_7zfile(archive_path):
                with py7zr.SevenZipFile(archive_path, mode='r') as sz:
                    return sz.needs_password()

        # 3. RAR
        elif lower_path.endswith('.rar') and rarfile:
            if rarfile.is_rarfile(archive_path):
                try:
                    with rarfile.RarFile(archive_path, 'r') as rarf:
                        if rarf.needs_password():
                            return True
                        for member in rarf.infolist():
                            if member.needs_password():
                                return True
                except (rarfile.PasswordRequired, getattr(rarfile, 'RarWrongPassword', Exception)):
                    return True
    except Exception as e:
        logger.warning(f"Arxiv shifrlanganligini tekshirishda ogohlantirish: {e}")
    return False


def create_custom_pdf(
    ordered_items: list[dict],
    output_pdf_path: str,
    settings: Optional[dict] = None,
    progress_callback=None
) -> bool:
    """
    Mini App (WebApp) dan kelgan buyurtmaga binoan sahifalarni tartiblab,
    aylantirib (Rotation) va maxsus sozlamalar bilan PDF yaratadi.
    ordered_items: [{'path': ..., 'rotation': 0|90|180|270}]
    """
    if not ordered_items:
        return False

    opened_images = []
    total = len(ordered_items)
    settings = settings or {}
    try:
        for idx, item in enumerate(ordered_items, 1):
            img_path = item.get('path')
            rotation = item.get('rotation', 0)
            if not img_path or not os.path.exists(img_path):
                continue

            with Image.open(img_path) as img:
                img = ImageOps.exif_transpose(img)
                if rotation:
                    # Pillow da rotate soat strelkasiga teskari, shuning uchun -rotation
                    img = img.rotate(-rotation, expand=True)

                if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                    bg = Image.new('RGB', img.size, (255, 255, 255))
                    alpha = img.convert('RGBA').split()[-1]
                    bg.paste(img, mask=alpha)
                    opened_images.append(bg)
                else:
                    opened_images.append(img.convert('RGB'))

            if progress_callback:
                try:
                    progress_callback(idx, total, os.path.basename(img_path))
                except Exception:
                    pass

        if not opened_images:
            return False

        first_img = opened_images[0]
        other_imgs = opened_images[1:] if len(opened_images) > 1 else []

        quality = 85 if settings.get('compress', True) else 95
        first_img.save(
            output_pdf_path,
            "PDF",
            resolution=100.0,
            save_all=True,
            append_images=other_imgs,
            quality=quality
        )
        return True
    except Exception as e:
        logger.error(f"Custom PDF creation failed: {e}", exc_info=True)
        return False
    finally:
        for im in opened_images:
            try:
                im.close()
            except Exception:
                pass
        force_garbage_collection()


def convert_images_to_pdf(image_paths: list[str], output_pdf_path: str, progress_callback=None) -> bool:
    """
    Rasmlar ro'yxatini sifatli va siqilgan bitta PDF fayliga aylantiradi.
    PNG (RGBA), Palette (P), CMYK va boshqa barcha formatlarni to'g'ri RGB ga o'tkazadi.
    """
    if not image_paths:
        return False

    opened_images = []
    total = len(image_paths)
    try:
        for idx, img_path in enumerate(image_paths, 1):
            if not os.path.exists(img_path):
                continue
            with Image.open(img_path) as img:
                img = ImageOps.exif_transpose(img)
                
                if img.mode in ('RGBA', 'LA') or (img.mode == 'P' and 'transparency' in img.info):
                    bg = Image.new('RGB', img.size, (255, 255, 255))
                    alpha = img.convert('RGBA').split()[-1]
                    bg.paste(img, mask=alpha)
                    opened_images.append(bg)
                else:
                    opened_images.append(img.convert('RGB'))

            if progress_callback:
                try:
                    progress_callback(idx, total, os.path.basename(img_path))
                except Exception:
                    pass

        if not opened_images:
            return False

        first_img = opened_images[0]
        other_imgs = opened_images[1:] if len(opened_images) > 1 else []

        first_img.save(
            output_pdf_path,
            "PDF",
            resolution=100.0,
            save_all=True,
            append_images=other_imgs,
            quality=92
        )
        return True
    except Exception as e:
        logger.error(f"PDF creation failed: {e}", exc_info=True)
        return False
    finally:
        for im in opened_images:
            try:
                im.close()
            except Exception:
                pass
        force_garbage_collection()


def convert_pdf_to_images(pdf_paths: list[str], output_dir: str, scale: float = 2.0, progress_callback=None) -> list[str]:
    """
    PDF fayllarni varaqma-varaq yuqori sifatli JPEG rasmlarga aylantiradi.
    """
    if not pdf_paths:
        return []

    created_images = []
    try:
        total_pages = 0
        for p in pdf_paths:
            if os.path.exists(p):
                try:
                    pdf_tmp = pdfium.PdfDocument(p)
                    total_pages += len(pdf_tmp)
                    pdf_tmp.close()
                except Exception:
                    pass

        img_counter = 1
        for pdf_path in pdf_paths:
            if not os.path.exists(pdf_path):
                continue
            pdf = pdfium.PdfDocument(pdf_path)
            for page_idx in range(len(pdf)):
                page = pdf[page_idx]
                image = page.render(scale=scale).to_pil()
                img_name = f"page_{img_counter}.jpg"
                img_path = os.path.join(output_dir, img_name)
                image.save(img_path, "JPEG", quality=95)
                created_images.append(img_path)
                
                if progress_callback and total_pages > 0:
                    try:
                        progress_callback(img_counter, total_pages)
                    except Exception:
                        pass

                img_counter += 1
            pdf.close()
        return created_images
    except Exception as e:
        logger.error(f"PDF to Images conversion failed: {e}", exc_info=True)
        return created_images
    finally:
        force_garbage_collection()


def merge_pdf_files(pdf_paths: list[str], output_pdf_path: str, progress_callback=None) -> bool:
    """
    Bir nechta PDF fayllarini bitta yaxlit PDF faylga birlashtiradi.
    """
    if not pdf_paths or len(pdf_paths) < 2:
        return False

    merged_doc = pymupdf.open()
    total = len(pdf_paths)
    try:
        for idx, p_path in enumerate(pdf_paths, 1):
            if os.path.exists(p_path):
                with pymupdf.open(p_path) as src_doc:
                    merged_doc.insert_pdf(src_doc)
            if progress_callback:
                try:
                    progress_callback(idx, total, os.path.basename(p_path))
                except Exception:
                    pass

        merged_doc.save(output_pdf_path, deflate=True, garbage=4)
        return True
    except Exception as e:
        logger.error(f"PDF merge failed: {e}", exc_info=True)
        return False
    finally:
        merged_doc.close()
        force_garbage_collection()


def compress_pdf(pdf_path: str, output_pdf_path: str) -> tuple[bool, int, int]:
    """
    PDF faylining hajmini sifatini saqlagan holda siqadi (Compress).
    Qaytaradi: (success: bool, original_size: int, compressed_size: int)
    """
    if not os.path.exists(pdf_path):
        return False, 0, 0

    orig_size = os.path.getsize(pdf_path)
    try:
        doc = pymupdf.open(pdf_path)
        doc.save(
            output_pdf_path,
            deflate=True,
            deflate_images=True,
            deflate_fonts=True,
            garbage=4,
            clean=True
        )
        doc.close()
        new_size = os.path.getsize(output_pdf_path)
        return True, orig_size, new_size
    except Exception as e:
        logger.error(f"PDF compression failed: {e}", exc_info=True)
        return False, orig_size, orig_size
    finally:
        force_garbage_collection()


def encrypt_pdf(pdf_path: str, output_pdf_path: str, password: str) -> bool:
    """
    PDF fayliga xavfsiz AES-256 parol qo'yadi.
    """
    if not os.path.exists(pdf_path) or not password:
        return False
    try:
        doc = pymupdf.open(pdf_path)
        doc.save(
            output_pdf_path,
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw=password,
            owner_pw=password,
            deflate=True
        )
        doc.close()
        return True
    except Exception as e:
        logger.error(f"PDF encryption failed: {e}", exc_info=True)
        return False
    finally:
        force_garbage_collection()


def convert_text_to_pdf(text_file_path: str, output_pdf_path: str) -> bool:
    """
    Matnli fayl (.txt, .md, .py, .log, .json, .csv) ni formatlangan PDF ga aylantiradi.
    """
    if not os.path.exists(text_file_path):
        return False
    try:
        with open(text_file_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        doc = pymupdf.open()
        margin = 40
        page_width, page_height = 595, 842  # A4 o'lchami
        fontsize = 10
        line_height = 14
        lines_per_page = int((page_height - 2 * margin) / line_height)

        curr_line = 0
        while curr_line < len(lines):
            page = doc.new_page(width=page_width, height=page_height)
            y = margin
            chunk = lines[curr_line:curr_line + lines_per_page]
            for l in chunk:
                clean_l = l.rstrip('\r\n')
                # 90 belgidan oshsa qirqish
                if len(clean_l) > 95:
                    clean_l = clean_l[:92] + "..."
                page.insert_text((margin, y), clean_l, fontsize=fontsize)
                y += line_height
            curr_line += lines_per_page

        doc.save(output_pdf_path, deflate=True)
        doc.close()
        return True
    except Exception as e:
        logger.error(f"Text to PDF conversion failed: {e}", exc_info=True)
        return False
    finally:
        force_garbage_collection()


# =========================================================================
# 2. UNIVERSAL ARXIVLAR (ZIP, RAR, 7Z, TAR, GZ, BZ2, XZ)
# =========================================================================

def create_zip_archive(file_paths: list[str], output_zip_path: str, progress_callback=None) -> bool:
    """
    Berilgan fayllarni ZIP arxiviga aylantiradi.
    """
    if not file_paths:
        return False

    total = len(file_paths)
    try:
        with zipfile.ZipFile(output_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for idx, f_path in enumerate(file_paths, 1):
                if os.path.exists(f_path):
                    arcname = os.path.basename(f_path)
                    if "_" in arcname and not arcname.startswith("page_"):
                        arcname = arcname.split("_", 1)[1]
                    zipf.write(f_path, arcname=arcname)
                if progress_callback:
                    try:
                        progress_callback(idx, total, os.path.basename(f_path))
                    except Exception:
                        pass
        return True
    except Exception as e:
        logger.error(f"ZIP creation failed: {e}", exc_info=True)
        return False


def create_7z_archive(file_paths: list[str], output_7z_path: str, password: str = None) -> bool:
    """
    Berilgan fayllarni 7Z formatida arxivlaydi (ixtiyoriy AES-256 parol bilan).
    """
    if not file_paths or not py7zr:
        return False
    try:
        with py7zr.SevenZipFile(output_7z_path, 'w', password=password) as archive:
            for f_path in file_paths:
                if os.path.exists(f_path):
                    arcname = os.path.basename(f_path)
                    if "_" in arcname and not arcname.startswith("page_"):
                        arcname = arcname.split("_", 1)[1]
                    archive.write(f_path, arcname=arcname)
        return True
    except Exception as e:
        logger.error(f"7Z creation failed: {e}", exc_info=True)
        return False


def extract_archive(archive_path: str, extract_to_dir: str, password: str = None) -> list[dict]:
    """
    ZIP, RAR, 7Z, TAR, GZ, BZ2, XZ arxivdagi barcha fayllarni papka tuzilmasini saqlagan holda xavfsiz chiqaradi.
    Agar arxiv parol bilan himoyalangan bo'lsa va parol berilmagan bo'lsa ArchivePasswordRequired ko'taradi.
    Agar kiritilgan parol noto'g'ri bo'lsa ArchiveWrongPassword ko'taradi.
    Qaytaradi: [{'full_path': '...', 'rel_dir': '...', 'file_name': '...', 'rel_path': '...'}, ...]
    """
    if not os.path.exists(archive_path):
        return []

    pwd_bytes = password.encode('utf-8') if password else None
    extracted_items = []
    lower_path = archive_path.lower()
    zip_module = pyzipper if pyzipper else zipfile

    try:
        # 1. 7Z Arxiv bo'lsa
        if lower_path.endswith('.7z') and py7zr:
            try:
                with py7zr.SevenZipFile(archive_path, mode='r', password=password) as sz:
                    if sz.needs_password() and not password:
                        raise ArchivePasswordRequired("Ushbu 7Z arxiv parol bilan himoyalangan!")
                    
                    sz.extractall(path=extract_to_dir)
                    for root, dirs, files in os.walk(extract_to_dir):
                        for f in files:
                            full_p = os.path.join(root, f)
                            rel_d = os.path.relpath(root, extract_to_dir).replace('\\', '/')
                            if rel_d == ".":
                                rel_d = "Asosiy papka"
                            extracted_items.append({
                                'full_path': full_p,
                                'rel_dir': rel_d,
                                'file_name': f,
                                'rel_path': os.path.relpath(full_p, extract_to_dir).replace('\\', '/')
                            })
                return extracted_items
            except py7zr.exceptions.PasswordRequired:
                raise ArchivePasswordRequired("Parol talab qilinadi!")
            except py7zr.exceptions.Bad7zFile as b7:
                if password:
                    raise ArchiveWrongPassword("Kiritilgan parol noto'g'ri!")
                raise b7

        # 2. TAR, GZ, BZ2, XZ Arxiv bo'lsa
        elif lower_path.endswith(('.tar', '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz')):
            mode = "r:*"
            with tarfile.open(archive_path, mode) as tar:
                for member in tar.getmembers():
                    if member.isdir():
                        continue
                    # Path traversal xavfsizligi
                    clean_name = os.path.normpath(member.name).replace('\\', '/')
                    parts = [p for p in clean_name.split('/') if p and p not in ('.', '..')]
                    if not parts:
                        continue
                    
                    rel_dir = "/".join(parts[:-1]) if len(parts) > 1 else ""
                    file_name = parts[-1]
                    target_dir = os.path.join(extract_to_dir, *parts[:-1]) if rel_dir else extract_to_dir
                    os.makedirs(target_dir, exist_ok=True)
                    
                    target_path = os.path.join(target_dir, file_name)
                    tar.extract(member, path=extract_to_dir)
                    
                    if os.path.exists(target_path):
                        extracted_items.append({
                            'full_path': target_path,
                            'rel_dir': rel_dir if rel_dir else "Asosiy papka",
                            'file_name': file_name,
                            'rel_path': clean_name
                        })
            return extracted_items

        # 3. ZIP Fayl bo'lsa
        elif lower_path.endswith('.zip') or zip_module.is_zipfile(archive_path):
            ZipCls = pyzipper.AESZipFile if pyzipper else zipfile.ZipFile
            with ZipCls(archive_path, 'r') as zipf:
                if pwd_bytes:
                    zipf.setpassword(pwd_bytes)

                # Parol talab qilinishini tekshirish
                is_enc = any(bool(m.flag_bits & 0x1) for m in zipf.infolist())
                if is_enc and not pwd_bytes:
                    raise ArchivePasswordRequired("Ushbu ZIP fayl parol bilan himoyalangan!")

                for member in zipf.infolist():
                    if member.is_dir():
                        continue
                    raw_filename = member.filename.replace('\\', '/')
                    parts = [p for p in raw_filename.split('/') if p and p not in ('.', '..')]
                    if not parts:
                        continue
                    
                    rel_dir = "/".join(parts[:-1]) if len(parts) > 1 else ""
                    file_name = parts[-1]
                    
                    target_dir = os.path.join(extract_to_dir, *parts[:-1]) if rel_dir else extract_to_dir
                    os.makedirs(target_dir, exist_ok=True)
                    
                    target_path = os.path.join(target_dir, file_name)
                    try:
                        with zipf.open(member, pwd=pwd_bytes) as src, open(target_path, 'wb') as dst:
                            shutil.copyfileobj(src, dst)
                    except RuntimeError as re:
                        re_msg = str(re).lower()
                        if "password" in re_msg or "bad password" in re_msg or "requires a password" in re_msg:
                            if not pwd_bytes:
                                raise ArchivePasswordRequired("Parol talab qilinadi!")
                            else:
                                raise ArchiveWrongPassword("Kiritilgan parol noto'g'ri!")
                        raise re
                    except zipfile.BadZipFile as bz:
                        if "bad crc" in str(bz).lower() and pwd_bytes:
                            raise ArchiveWrongPassword("Kiritilgan parol noto'g'ri!")
                        raise bz
                        
                    extracted_items.append({
                        'full_path': target_path,
                        'rel_dir': rel_dir if rel_dir else "Asosiy papka",
                        'file_name': file_name,
                        'rel_path': raw_filename
                    })

        # 4. RAR Fayl bo'lsa
        elif lower_path.endswith('.rar') and rarfile:
            with rarfile.RarFile(archive_path, 'r') as rarf:
                if password:
                    rarf.setpassword(password)

                if rarf.needs_password() and not password:
                    raise ArchivePasswordRequired("Ushbu RAR fayl parol bilan himoyalangan!")

                for member in rarf.infolist():
                    if member.isdir():
                        continue
                    if member.needs_password() and not password:
                        raise ArchivePasswordRequired("Ushbu RAR fayl parol bilan himoyalangan!")

                    raw_filename = member.filename.replace('\\', '/')
                    parts = [p for p in raw_filename.split('/') if p and p not in ('.', '..')]
                    if not parts:
                        continue
                    
                    rel_dir = "/".join(parts[:-1]) if len(parts) > 1 else ""
                    file_name = parts[-1]
                    
                    target_dir = os.path.join(extract_to_dir, *parts[:-1]) if rel_dir else extract_to_dir
                    os.makedirs(target_dir, exist_ok=True)
                    
                    target_path = os.path.join(target_dir, file_name)
                    try:
                        with rarf.open(member, pwd=password) as src, open(target_path, 'wb') as dst:
                            shutil.copyfileobj(src, dst)
                    except Exception as re:
                        re_msg = str(re).lower()
                        re_type = type(re).__name__
                        if "password" in re_msg or "password" in re_type.lower() or "crc" in re_msg or "crc" in re_type.lower() or "bad" in re_type.lower():
                            if not password:
                                raise ArchivePasswordRequired("Parol talab qilinadi!")
                            else:
                                raise ArchiveWrongPassword("Kiritilgan parol noto'g'ri!")
                        raise re
                        
                    extracted_items.append({
                        'full_path': target_path,
                        'rel_dir': rel_dir if rel_dir else "Asosiy papka",
                        'file_name': file_name,
                        'rel_path': raw_filename
                    })

        return extracted_items
    except (ArchivePasswordRequired, ArchiveWrongPassword):
        raise
    except Exception as e:
        logger.error(f"Archive extraction failed ({archive_path}): {e}", exc_info=True)
        raise e
    finally:
        force_garbage_collection()


# =========================================================================
# 3. AUDIO, VIDEO VA GIF MEDIA KONVERTATSIYASI (FFMPEG)
# =========================================================================

def convert_media_to_mp3(media_path: str, output_mp3_path: str) -> bool:
    """
    Video yoki audio faylni yuqori sifatli 192kbps MP3 formatiga o'tkazadi.
    """
    if not os.path.exists(media_path) or not FFMPEG_PATH:
        return False
    try:
        cmd = [
            FFMPEG_PATH,
            "-y",
            "-i", media_path,
            "-vn",
            "-acodec", "libmp3lame",
            "-ab", "192k",
            "-ar", "44100",
            output_mp3_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        return res.returncode == 0 and os.path.exists(output_mp3_path)
    except Exception as e:
        logger.error(f"Media to MP3 conversion failed: {e}", exc_info=True)
        return False
    finally:
        force_garbage_collection()


def convert_video_to_gif(video_path: str, output_gif_path: str, max_duration: int = 15) -> bool:
    """
    Videodan sifatli va ixcham animatsiyali GIF tayyorlaydi.
    """
    if not os.path.exists(video_path) or not FFMPEG_PATH:
        return False
    try:
        # 2-bosqichli yuqori sifatli ranglar palitrasi filtri
        cmd = [
            FFMPEG_PATH,
            "-y",
            "-t", str(max_duration),
            "-i", video_path,
            "-vf", "fps=12,scale=480:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer",
            output_gif_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        return res.returncode == 0 and os.path.exists(output_gif_path)
    except Exception as e:
        logger.error(f"Video to GIF conversion failed: {e}", exc_info=True)
        return False
    finally:
        force_garbage_collection()


# =========================================================================
# 4. OFFICE HUJJATLARINI PDF GA AYLANTIRISH (LIBREOFFICE HEADLESS)
# =========================================================================

def convert_office_to_pdf(office_path: str, output_dir: str) -> Optional[str]:
    """
    Word (.docx, .doc), Excel (.xlsx, .xls), PowerPoint (.pptx, .ppt), RTF, ODT fayllarni
    LibreOffice orqali 100% original ko'rinishida PDF ga o'tkazadi.
    """
    if not os.path.exists(office_path):
        return None
    try:
        cmd = [
            LIBREOFFICE_BIN,
            "--headless",
            "--convert-to", "pdf",
            "--outdir", output_dir,
            office_path
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if res.returncode == 0:
            base_name = os.path.splitext(os.path.basename(office_path))[0]
            expected_pdf = os.path.join(output_dir, f"{base_name}.pdf")
            if os.path.exists(expected_pdf):
                return expected_pdf
            # Ba'zida LibreOffice kichik harfda nomlaydi
            for f in os.listdir(output_dir):
                if f.lower() == f"{base_name.lower()}.pdf":
                    return os.path.join(output_dir, f)
        logger.warning(f"LibreOffice convert warning: {res.stderr.decode('utf-8', errors='ignore')}")
        return None
    except Exception as e:
        logger.error(f"Office to PDF conversion failed ({office_path}): {e}", exc_info=True)
        return None
    finally:
        force_garbage_collection()


# =========================================================================
# 5. OCR MATN TANISH (TESSERACT OCR)
# =========================================================================

def extract_text_ocr(file_path: str, lang: str = "uzb+rus+eng") -> str:
    """
    Rasm yoki PDF faylidagi matnlarni OCR orqali tanib, matn ko'rinishida qaytaradi.
    Qo'llab-quvvatlaydi: O'zbek, Rus, Ingliz tillari.
    """
    if not os.path.exists(file_path) or not pytesseract:
        return "⚠️ OCR dvigateli o'rnatilmagan yoki fayl topilmadi."

    extracted_texts = []
    lower_path = file_path.lower()
    try:
        # 1. Rasm bo'lsa
        if lower_path.endswith(('.jpg', '.jpeg', '.png', '.webp', '.bmp', '.heic', '.heif', '.tiff')):
            with Image.open(file_path) as img:
                img = ImageOps.exif_transpose(img)
                # Tillar: mavjud tillarni tekshirish
                available_langs = pytesseract.get_languages(config='')
                target_langs = []
                for l in lang.split('+'):
                    if l in available_langs:
                        target_langs.append(l)
                lang_str = "+".join(target_langs) if target_langs else "eng"
                
                txt = pytesseract.image_to_string(img, lang=lang_str)
                if txt.strip():
                    extracted_texts.append(txt.strip())

        # 2. PDF bo'lsa
        elif lower_path.endswith('.pdf'):
            doc = pymupdf.open(file_path)
            total_pages = len(doc)
            available_langs = pytesseract.get_languages(config='')
            target_langs = [l for l in lang.split('+') if l in available_langs]
            lang_str = "+".join(target_langs) if target_langs else "eng"

            for page_num in range(total_pages):
                page = doc[page_num]
                # To'g'ridan-to'g'ri matn bo'lsa avval uni olamiz
                raw_txt = page.get_text()
                if raw_txt.strip():
                    extracted_texts.append(f"--- [ {page_num + 1}-sahifa ] ---\n{raw_txt.strip()}")
                else:
                    # Rasm sifatida render qilib OCR qilamiz
                    pix = page.get_pixmap(dpi=200)
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    txt = pytesseract.image_to_string(img, lang=lang_str)
                    if txt.strip():
                        extracted_texts.append(f"--- [ {page_num + 1}-sahifa (OCR) ] ---\n{txt.strip()}")
            doc.close()

        if not extracted_texts:
            return "🔍 Fayldan hech qanday matn topilmadi yoki rasm sifati past."
        return "\n\n".join(extracted_texts)
    except Exception as e:
        logger.error(f"OCR extraction failed ({file_path}): {e}", exc_info=True)
        return f"⚠️ Matnni ajratishda xatolik yuz berdi: {e}"
    finally:
        force_garbage_collection()


# =========================================================================
# 4. TOZALASH VA XOTIRA NAZORATI
# =========================================================================

def cleanup_user_files(user_folder: str):
    """Foydalanuvchi papkasidagi barcha vaqtinchalik fayllarni tozalash"""
    try:
        if os.path.exists(user_folder):
            shutil.rmtree(user_folder, ignore_errors=True)
            os.makedirs(user_folder, exist_ok=True)
    except Exception as e:
        logger.error(f"Error cleaning user folder {user_folder}: {e}")
    finally:
        force_garbage_collection()


def cleanup_old_files(base_dir: str, max_age_seconds: int = 1800):
    """30 daqiqadan ortiq saqlanib qolgan eski fayllarni tozalash"""
    if not os.path.exists(base_dir):
        return
    now = time.time()
    try:
        for root, dirs, files in os.walk(base_dir):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    if os.path.getmtime(file_path) < now - max_age_seconds:
                        os.remove(file_path)
                except Exception:
                    pass
    except Exception as e:
        logger.error(f"Error during scheduled cleanup: {e}")
    finally:
        force_garbage_collection()
