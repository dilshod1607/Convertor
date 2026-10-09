import os
import json
import logging
import asyncio
from typing import Dict, Any, Optional

try:
    import redis.asyncio as redis
except ImportError:
    redis = None

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
SESSION_TTL_SECONDS = 86400  # 24 soat


class SessionManager:
    """
    Yuqori yuklamali (High-Load) Redis sessiya boshqaruvchisi.
    Redis mavjud bo'lsa barcha holatlarni Redis da saqlaydi (server qayta yuklanganda ham saqlanadi).
    Agar Redis mavjud bo'lmasa, xotirada xavfsiz ishlayveradi.
    """

    def __init__(self, redis_url: str = REDIS_URL):
        self.redis_url = redis_url
        self.redis_client: Optional[redis.Redis] = None
        self._memory_cache: Dict[int, Dict[str, Any]] = {}
        self._locks: Dict[int, asyncio.Lock] = {}
        self._init_done = False

    async def init(self):
        """Redis ulanishini ishga tushirish"""
        if self._init_done:
            return
        if redis:
            try:
                self.redis_client = redis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                    socket_connect_timeout=2.0
                )
                await self.redis_client.ping()
                logger.info("✅ Redis sessiya tizimi muvaffaqiyatli ulandi.")
            except Exception as e:
                logger.warning(f"⚠️ Redis ga ulanib bo'lmadi ({e}). Xotira rejimida davom etilmoqda.")
                self.redis_client = None
        self._init_done = True

    def get_lock(self, user_id: int) -> asyncio.Lock:
        """Foydalanuvchi uchun asinxron lock olish"""
        if user_id not in self._locks:
            self._locks[user_id] = asyncio.Lock()
        return self._locks[user_id]

    async def get_session(self, user_id: int) -> Dict[str, Any]:
        """Foydalanuvchi sessiyasini olish"""
        await self.init()

        if user_id not in self._memory_cache:
            loaded_from_redis = None
            if self.redis_client:
                try:
                    data = await self.redis_client.get(f"convertor_sess:{user_id}")
                    if data:
                        loaded_from_redis = json.loads(data)
                except Exception as e:
                    logger.warning(f"Redis sessiya o'qishda xatolik ({user_id}): {e}")

            if loaded_from_redis:
                self._memory_cache[user_id] = loaded_from_redis
            else:
                self._memory_cache[user_id] = {
                    'items': [],
                    'menu_msg_id': None,
                    'chat_id': None,
                    'waiting_for_password': False,
                    'archive_paths': None,
                    'extract_dir': None,
                    'user_dir': None,
                    'progress_msg_id': None,
                    'ocr_lang': 'uzb+rus+eng'
                }

        # Runtime o'zgaruvchilarni qo'shish
        sess = self._memory_cache[user_id]
        sess['lock'] = self.get_lock(user_id)
        if 'update_task' not in sess:
            sess['update_task'] = None
        return sess

    async def save_session(self, user_id: int):
        """Sessiyani Redis ga saqlash"""
        if not self.redis_client or user_id not in self._memory_cache:
            return

        sess = self._memory_cache[user_id]
        # JSON serialize qilib bo'lmaydigan o'zgaruvchilarni ajratish
        serializable = {
            'items': sess.get('items', []),
            'menu_msg_id': sess.get('menu_msg_id'),
            'chat_id': sess.get('chat_id'),
            'waiting_for_password': sess.get('waiting_for_password', False),
            'archive_paths': sess.get('archive_paths'),
            'extract_dir': sess.get('extract_dir'),
            'user_dir': sess.get('user_dir'),
            'progress_msg_id': sess.get('progress_msg_id'),
            'ocr_lang': sess.get('ocr_lang', 'uzb+rus+eng')
        }
        try:
            await self.redis_client.setex(
                f"convertor_sess:{user_id}",
                SESSION_TTL_SECONDS,
                json.dumps(serializable)
            )
        except Exception as e:
            logger.warning(f"Redis sessiya yozishda xatolik ({user_id}): {e}")

    async def reset_session(self, user_id: int):
        """Sessiyani tozalash"""
        if user_id in self._memory_cache:
            sess = self._memory_cache[user_id]
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

        if self.redis_client:
            try:
                await self.redis_client.delete(f"convertor_sess:{user_id}")
            except Exception as e:
                logger.warning(f"Redis sessiya o'chirishda xatolik ({user_id}): {e}")


session_manager = SessionManager()
