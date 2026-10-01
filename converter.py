import os
import time
import zipfile
import shutil
import logging
from PIL import Image, ImageOps
import pypdfium2 as pdfium

try:
    import pyzipper
except ImportError:
    pyzipper = None

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


def is_archive_encrypted(archive_path: str) -> bool:
    """Arxiv fayl parol bilan himoyalanganligini aniqlash"""
    if not os.path.exists(archive_path):
        return False
    lower_path = archive_path.lower()
    try:
        zip_module = pyzipper if pyzipper else zipfile
        if lower_path.endswith('.zip') or zip_module.is_zipfile(archive_path):
            if pyzipper:
                with pyzipper.AESZipFile(archive_path, 'r') as zipf:
                    return any(bool(m.flag_bits & 0x1) for m in zipf.infolist())
            else:
                with zipfile.ZipFile(archive_path, 'r') as zipf:
                    return any(bool(m.flag_bits & 0x1) for m in zipf.infolist())
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
            quality=95
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


def extract_archive(archive_path: str, extract_to_dir: str, password: str = None) -> list[dict]:
    """
    ZIP yoki RAR arxivdagi barcha fayllarni papka tuzilmasini saqlagan holda xavfsiz chiqaradi.
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
        # 1. ZIP Fayl bo'lsa
        if lower_path.endswith('.zip') or zip_module.is_zipfile(archive_path):
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
                    # Xavfsiz nisbiy yo'l
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

        # 2. RAR Fayl bo'lsa
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

def cleanup_user_files(user_folder: str):
    """Foydalanuvchi papkasidagi barcha vaqtinchalik fayllarni tozalash"""
    try:
        if os.path.exists(user_folder):
            shutil.rmtree(user_folder, ignore_errors=True)
            os.makedirs(user_folder, exist_ok=True)
    except Exception as e:
        logger.error(f"Error cleaning user folder {user_folder}: {e}")

def cleanup_old_files(base_dir: str, max_age_seconds: int = 3600):
    """1 soatdan ortiq saqlanib qolgan eski fayllarni tozalash"""
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
