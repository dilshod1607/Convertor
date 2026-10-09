import os
import sys
import time
import logging
import asyncio
from typing import Optional, Callable

try:
    from telethon import TelegramClient, events
    from telethon.tl.types import DocumentAttributeFilename
except ImportError:
    TelegramClient = None

logger = logging.getLogger(__name__)

API_ID = int(os.getenv("API_ID", "22541448"))
API_HASH = os.getenv("API_HASH", "8706b137c3cae93fa2e2629fee36addf")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

SESSION_NAME = "convertor_mtproto_session"


class MTProtoEngine:
    """
    2000 MB (2 GB) gacha fayllarni Telegram MTProto protokoli orqali
    to'g'ridan-to'g'ri, tezkor va cheklovlarsiz yuklab olish va yuborish dvigateli.
    """

    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self._is_started = False
        self._lock = asyncio.Lock()

    async def start(self):
        """MTProto mijozini xavfsiz ishga tushirish"""
        if self._is_started or not TelegramClient or not BOT_TOKEN:
            return
        async with self._lock:
            if self._is_started:
                return
            try:
                self.client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
                await self.client.start(bot_token=BOT_TOKEN)
                self._is_started = True
                logger.info("🟢 MTProto 2000 MB Dvigateli muvaffaqiyatli ishga tushdi.")
            except Exception as e:
                logger.warning(f"⚠️ MTProto Dvigatelini ishga tushirishda ogohlantirish: {e}")
                self.client = None

    async def download_media(
        self,
        message_id: int,
        chat_id: int,
        target_path: str,
        progress_callback: Optional[Callable] = None
    ) -> bool:
        """2000 MB gacha bo'lgan har qanday faylni yuklab olish"""
        await self.start()
        if not self.client or not self._is_started:
            return False
        try:
            msg = await self.client.get_messages(chat_id, ids=message_id)
            if not msg or not msg.media:
                return False

            last_edit = [0.0]

            def _telethon_progress(received_bytes, total_bytes):
                now = time.time()
                if progress_callback and (now - last_edit[0] > 1.2 or received_bytes == total_bytes):
                    last_edit[0] = now
                    try:
                        progress_callback(received_bytes, total_bytes)
                    except Exception:
                        pass

            await self.client.download_media(
                msg,
                file=target_path,
                progress_callback=_telethon_progress if progress_callback else None
            )
            return os.path.exists(target_path) and os.path.getsize(target_path) > 0
        except Exception as e:
            logger.error(f"MTProto download failed ({message_id}): {e}", exc_info=True)
            return False

    async def send_file(
        self,
        chat_id: int,
        file_path: str,
        caption: str = "",
        file_name: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
        as_audio: bool = False
    ) -> bool:
        """2000 MB gacha bo'lgan har qanday faylni yuborish"""
        await self.start()
        if not self.client or not self._is_started:
            return False
        if not os.path.exists(file_path):
            return False

        try:
            last_edit = [0.0]

            def _telethon_progress(sent_bytes, total_bytes):
                now = time.time()
                if progress_callback and (now - last_edit[0] > 1.2 or sent_bytes == total_bytes):
                    last_edit[0] = now
                    try:
                        progress_callback(sent_bytes, total_bytes)
                    except Exception:
                        pass

            fname = file_name or os.path.basename(file_path)
            attributes = []
            if fname:
                attributes.append(DocumentAttributeFilename(file_name=fname))

            await self.client.send_file(
                entity=chat_id,
                file=file_path,
                caption=caption,
                parse_mode='html',
                attributes=attributes,
                progress_callback=_telethon_progress if progress_callback else None,
                voice_note=False
            )
            return True
        except Exception as e:
            logger.error(f"MTProto send failed ({file_path}): {e}", exc_info=True)
            return False


mtproto_engine = MTProtoEngine()
