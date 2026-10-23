import os
import csv
import sqlite3
import logging
from datetime import datetime, timedelta
import pytz
import xlsxwriter as xl

logger = logging.getLogger(__name__)
TASHKENT_TZ = pytz.timezone('Asia/Tashkent')


class Database:
    """
    Universal SQLite ma'lumotlar bazasi boshqaruvi.
    WAL rejimi, 30 soniyalik busy_timeout va xavfsiz avtomatik migratsiya (Auto-migration)
    bilan jihozlangan. 'database is locked' xatolarini to'liq oldini oladi.
    """

    def __init__(self, path_to_db: str = "database.db"):
        self.path_to_db = path_to_db
        db_dir = os.path.dirname(self.path_to_db)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.path_to_db, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self):
        """Baza jadvallarini avtomatik yaratish va mavjud bazani yangilash (Auto-migration)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                # 1. Foydalanuvchilar jadvali
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    full_name TEXT,
                    username TEXT,
                    language TEXT DEFAULT 'uz',
                    created_at TEXT,
                    last_active TEXT,
                    is_active INTEGER DEFAULT 1,
                    is_banned INTEGER DEFAULT 0,
                    is_vip INTEGER DEFAULT 0,
                    vip_expires_at TEXT
                );
                """)

                # Avtomatik migratsiya: agar eski ustunlar yetishmasa qo'shamiz
                cursor.execute("PRAGMA table_info(users)")
                existing_cols = [c[1] for c in cursor.fetchall()]
                new_columns = {
                    'language': "TEXT DEFAULT 'uz'",
                    'created_at': "TEXT",
                    'last_active': "TEXT",
                    'is_active': "INTEGER DEFAULT 1",
                    'is_banned': "INTEGER DEFAULT 0",
                    'is_vip': "INTEGER DEFAULT 0",
                    'vip_expires_at': "TEXT"
                }
                for col_name, col_type in new_columns.items():
                    if col_name not in existing_cols:
                        try:
                            cursor.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type};")
                            logger.info(f"[Migration] 'users' jadvaliga '{col_name}' ustuni qo'shildi.")
                        except Exception as e:
                            logger.warning(f"Migratsiya ogohlantirish ({col_name}): {e}")

                # 2. Homiylik kanallari jadvali
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    channel_id TEXT NOT NULL UNIQUE,
                    link TEXT NOT NULL,
                    created_at TEXT
                );
                """)

                # 3. Yordamchi adminlar (Sub-Admins) jadvali
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS sub_admins (
                    user_id INTEGER PRIMARY KEY,
                    added_by INTEGER,
                    created_at TEXT
                );
                """)

                # 4. Tizim sozlamalari (Texnik rejim va boshqalar)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
                """)

                # 5. Eski status jadvali (moslashuvchanlik uchun)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS status (
                    active INTEGER DEFAULT 0,
                    block INTEGER DEFAULT 0
                );
                """)
                cursor.execute("SELECT COUNT(*) FROM status")
                if cursor.fetchone()[0] == 0:
                    cursor.execute("INSERT INTO status (active, block) VALUES (0, 0)")

                # 6. Izoh reklamalari jadvali (Caption Ads)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS caption_ads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ad_text TEXT NOT NULL,
                    ad_link TEXT,
                    expires_at TEXT,
                    is_active INTEGER DEFAULT 1,
                    created_at TEXT
                );
                """)

                # 7. Konvertatsiya statistikasi jadvali (Conversion Stats)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS conversion_stats (
                    module_name TEXT PRIMARY KEY,
                    count INTEGER DEFAULT 0
                );
                """)

                conn.commit()
                logger.info("Ma'lumotlar bazasi jadvallari muvaffaqiyatli tekshirildi.")
        except sqlite3.Error as e:
            logger.error(f"Database initialization error: {e}", exc_info=True)

    # =========================================================================
    # FOYDALANUVCHILAR BILAN ISHLASH
    # =========================================================================

    def add_user(self, user_id: int, full_name: str, username: str = "", language: str = 'uz') -> bool:
        """Yangi foydalanuvchini qo'shish yoki oxirgi faolligini yangilash"""
        now = datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d %H:%M:%S")
        sql = """
        INSERT INTO users (user_id, full_name, username, language, created_at, last_active, is_active)
        VALUES (?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(user_id) DO UPDATE SET
            full_name = excluded.full_name,
            username = excluded.username,
            last_active = excluded.last_active,
            is_active = 1;
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, (user_id, full_name or "", username or "", language or 'uz', now, now))
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"Error adding user {user_id}: {e}")
            return False

    def set_user_active(self, user_id: int, is_active: int):
        """Foydalanuvchi botni bloklaganida (0) yoki qayta yoqqanida (1) yangilash"""
        now = datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET is_active = ?, last_active = ? WHERE user_id = ?;", (is_active, now, user_id))
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error setting user active {user_id}: {e}")

    def set_user_banned(self, user_id: int, is_banned: int):
        """Admin tomonidan foydalanuvchini bloklash (1) yoki blokdan chiqarish (0)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET is_banned = ? WHERE user_id = ?;", (is_banned, user_id))
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error setting user banned {user_id}: {e}")

    def set_user_vip(self, user_id: int, is_vip: int, expires_at: str = None):
        """Foydalanuvchiga VIP maqomini berish yoki bekor qilish"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE users SET is_vip = ?, vip_expires_at = ? WHERE user_id = ?;", (is_vip, expires_at, user_id))
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error setting user VIP {user_id}: {e}")

    def set_user_vip_days(self, user_id: int, days: int):
        """Foydalanuvchiga kunlar bo'yicha VIP berish"""
        if days >= 36500:
            exp_str = None
        else:
            exp_str = (datetime.now(TASHKENT_TZ) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        self.set_user_vip(user_id=user_id, is_vip=1, expires_at=exp_str)

    def revoke_user_vip(self, user_id: int):
        """Foydalanuvchining VIP maqomini bekor qilish"""
        self.set_user_vip(user_id=user_id, is_vip=0, expires_at=None)

    def get_all_vip_users(self) -> list:
        """Barcha faol VIP foydalanuvchilar ro'yxatini olish"""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM users 
                    WHERE is_vip = 1 AND (vip_expires_at IS NULL OR vip_expires_at > datetime('now'))
                    ORDER BY user_id DESC;
                """)
                return [dict(r) for r in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Error fetching VIP users: {e}")
            return []

    def get_vip_info(self, user_id: int) -> dict:
        """Foydalanuvchining VIP ma'lumotlarini olish"""
        u = self.select_user(user_id)
        if not u:
            return {'is_vip': False, 'expires_at': None}
        is_vip = bool(u.get('is_vip'))
        exp = u.get('vip_expires_at')
        if is_vip and exp:
            try:
                exp_dt = datetime.strptime(exp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TASHKENT_TZ)
                if exp_dt < datetime.now(TASHKENT_TZ):
                    self.revoke_user_vip(user_id)
                    return {'is_vip': False, 'expires_at': None}
            except Exception:
                pass
        return {'is_vip': is_vip, 'expires_at': exp}

    def select_user(self, user_id: int):
        """Foydalanuvchi ma'lumotlarini dict ko'rinishida olish"""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users WHERE user_id = ?;", (user_id,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error(f"Error selecting user {user_id}: {e}")
            return None

    def search_user(self, query: str):
        """Foydalanuvchini Telegram ID yoki @username bo'yicha qidirish"""
        cleaned = query.strip()
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                if cleaned.isdigit():
                    cursor.execute("SELECT * FROM users WHERE user_id = ?;", (int(cleaned),))
                else:
                    uname = cleaned.lstrip('@')
                    cursor.execute("SELECT * FROM users WHERE LOWER(username) = LOWER(?);", (uname,))
                row = cursor.fetchone()
                return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error(f"Error searching user {query}: {e}")
            return None

    def count_users(self) -> int:
        """Jami foydalanuvchilar soni"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM users;")
                res = cursor.fetchone()
                return res[0] if res else 0
        except sqlite3.Error as e:
            logger.error(f"Error counting users: {e}")
            return 0

    def get_detailed_stats(self) -> dict:
        """Shaffof va to'liq statistika hisoboti"""
        now = datetime.now(TASHKENT_TZ)
        today_start = now.strftime("%Y-%m-%d 00:00:00")
        day_ago = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute("SELECT COUNT(*) FROM users;")
                total = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM users WHERE is_active = 1;")
                active = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM users WHERE is_active = 0;")
                blocked = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM users WHERE is_banned = 1;")
                banned = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM users WHERE is_vip = 1;")
                vip = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM users WHERE created_at >= ?;", (today_start,))
                today = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM users WHERE last_active >= ?;", (day_ago,))
                active_24h = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM users WHERE last_active >= ?;", (week_ago,))
                active_7d = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM channels;")
                channels_cnt = cursor.fetchone()[0]

                return {
                    "total": total,
                    "active": active,
                    "blocked": blocked,
                    "banned": banned,
                    "vip": vip,
                    "today": today,
                    "today_joined": today,
                    "active_24h": active_24h,
                    "active_7d": active_7d,
                    "channels_cnt": channels_cnt,
                    "channels_count": channels_cnt
                }
        except sqlite3.Error as e:
            logger.error(f"Error getting detailed stats: {e}")
            return {
                "total": 0, "active": 0, "blocked": 0, "banned": 0, "vip": 0,
                "today": 0, "today_joined": 0, "active_24h": 0, "active_7d": 0,
                "channels_cnt": 0, "channels_count": 0
            }

    def select_all_users(self):
        """Eski uslubda barcha a'zolarni olish (moslashuvchanlik uchun)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id, full_name, username, created_at FROM users;")
                return cursor.fetchall()
        except sqlite3.Error as e:
            logger.error(f"Error selecting all users: {e}")
            return []

    def select_active_user_ids(self) -> list:
        """Xabar tarqatish (broadcast) uchun faol a'zolar ID lari"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM users WHERE is_active = 1 AND is_banned = 0;")
                rows = cursor.fetchall()
                return [r[0] for r in rows]
        except sqlite3.Error as e:
            logger.error(f"Error selecting active user ids: {e}")
            return []

    def get_status(self):
        """Aktiv va bloklangan foydalanuvchilar soni (eski status jadvalidan)"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM users WHERE is_active = 1;")
                act = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM users WHERE is_active = 0;")
                blk = cursor.fetchone()[0]
                return act, blk
        except sqlite3.Error as e:
            logger.error(f"Error getting status: {e}")
            return 0, 0

    def update_status(self, active: int, block: int):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE status SET active = ?, block = ?;", (active, block))
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error updating status: {e}")

    # =========================================================================
    # MAJBURIY OBUNA KANALLARI BILAN ISHLASH
    # =========================================================================

    def add_channel(self, name: str, channel_id: str, link: str) -> bool:
        """Yangi homiy kanalini qo'shish"""
        now = datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d %H:%M:%S")
        sql = "INSERT INTO channels (name, channel_id, link, created_at) VALUES (?, ?, ?, ?);"
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, (name.strip(), str(channel_id).strip(), link.strip(), now))
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE channels SET name = ?, link = ? WHERE channel_id = ?;", (name.strip(), link.strip(), str(channel_id).strip()))
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"Error adding channel: {e}")
            return False

    def get_channels_from_db(self):
        """Barcha kanallarni ro'yxat sifatida olish: [(name, channel_id, link, id), ...]"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT name, channel_id, link, id FROM channels ORDER BY id ASC;")
                return cursor.fetchall()
        except sqlite3.Error as e:
            logger.error(f"Error fetching channels: {e}")
            return []

    def delete_channel_by_id(self, channel_db_id: int) -> bool:
        """Kanalni ID si bo'yicha o'chirish"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM channels WHERE id = ?;", (channel_db_id,))
                conn.commit()
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            logger.error(f"Error deleting channel {channel_db_id}: {e}")
            return False

    def delete_channel_by_channel_id(self, channel_id: str) -> bool:
        """Kanalni Telegram channel_id si bo'yicha o'chirish"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM channels WHERE channel_id = ?;", (str(channel_id).strip(),))
                conn.commit()
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            logger.error(f"Error deleting channel by channel_id {channel_id}: {e}")
            return False

    # =========================================================================
    # SUB-ADMINLAR (YORDAMCHI ADMINLAR)
    # =========================================================================

    def add_sub_admin(self, user_id: int, added_by: int) -> bool:
        now = datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d %H:%M:%S")
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT INTO sub_admins (user_id, added_by, created_at) VALUES (?, ?, ?);", (user_id, added_by, now))
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            return False
        except sqlite3.Error as e:
            logger.error(f"Error adding sub-admin {user_id}: {e}")
            return False

    def delete_sub_admin(self, user_id: int) -> bool:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM sub_admins WHERE user_id = ?;", (user_id,))
                conn.commit()
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            logger.error(f"Error deleting sub-admin {user_id}: {e}")
            return False

    def get_sub_admins(self) -> list:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id FROM sub_admins;")
                return [r[0] for r in cursor.fetchall()]
        except sqlite3.Error as e:
            logger.error(f"Error getting sub-admins: {e}")
            return []

    def is_admin(self, user_id: int, main_admins: list) -> bool:
        if user_id in main_admins:
            return True
        sub_admins = self.get_sub_admins()
        return user_id in sub_admins

    # =========================================================================
    # TIZIM SOZLAMALARI VA TEXNIK REJIM (MAINTENANCE)
    # =========================================================================

    def get_setting(self, key: str, default: str = None) -> str:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM settings WHERE key = ?;", (key,))
                row = cursor.fetchone()
                return row[0] if row else default
        except sqlite3.Error:
            return default

    def set_setting(self, key: str, value: str):
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value;
                """, (key, value))
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error setting {key}: {e}")

    def is_maintenance(self) -> bool:
        return self.get_setting("maintenance_mode", "0") == "1"

    def set_maintenance(self, enabled: bool):
        self.set_setting("maintenance_mode", "1" if enabled else "0")

    # =========================================================================
    # EKSPORT (EXCEL & CSV)
    # =========================================================================

    def export_users_to_excel(self, output_path: str = "users_export.xlsx") -> str:
        """Foydalanuvchilar ro'yxatini to'liq stillangan, professional Excel jadvaliga aylantirish"""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users ORDER BY created_at DESC;")
                users = cursor.fetchall()

            workbook = xl.Workbook(output_path)
            worksheet = workbook.add_worksheet("Foydalanuvchilar")
            worksheet.freeze_panes(1, 0)

            # Sarlavha stili (To'q-ko'k fon, oq qalin matn)
            header_format = workbook.add_format({
                'bold': True,
                'align': 'center',
                'valign': 'vcenter',
                'bg_color': '#1B365D',
                'font_color': '#FFFFFF',
                'border': 1,
                'font_size': 11
            })

            center_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})
            left_format = workbook.add_format({'align': 'left', 'valign': 'vcenter', 'border': 1})

            # Rangli Badge stillari
            active_badge = workbook.add_format({'bg_color': '#D4EDDA', 'font_color': '#155724', 'align': 'center', 'border': 1, 'bold': True})
            blocked_badge = workbook.add_format({'bg_color': '#FFF3CD', 'font_color': '#856404', 'align': 'center', 'border': 1})
            banned_badge = workbook.add_format({'bg_color': '#F8D7DA', 'font_color': '#721C24', 'align': 'center', 'border': 1, 'bold': True})
            vip_badge = workbook.add_format({'bg_color': '#E2D9F3', 'font_color': '#553C9A', 'align': 'center', 'border': 1, 'bold': True})

            headers = ["№", "User ID", "To'liq Ism", "Username", "Til", "Ro'yxatdan o'tgan", "Oxirgi faollik", "Holati", "Maqomi"]
            for col_idx, h in enumerate(headers):
                worksheet.write(0, col_idx, h, header_format)

            for row_idx, u in enumerate(users, start=1):
                worksheet.write(row_idx, 0, row_idx, center_format)
                worksheet.write(row_idx, 1, u["user_id"], center_format)
                worksheet.write(row_idx, 2, u["full_name"] or "", left_format)
                worksheet.write(row_idx, 3, f"@{u['username']}" if u["username"] else "-", left_format)
                worksheet.write(row_idx, 4, u["language"] if "language" in u.keys() and u["language"] else "uz", center_format)
                worksheet.write(row_idx, 5, u["created_at"] or "-", center_format)
                worksheet.write(row_idx, 6, u["last_active"] or "-", center_format)

                is_banned = u["is_banned"] if "is_banned" in u.keys() else 0
                is_active = u["is_active"] if "is_active" in u.keys() else 1
                is_vip = u["is_vip"] if "is_vip" in u.keys() else 0

                if is_banned == 1:
                    worksheet.write(row_idx, 7, "🚫 Ban", banned_badge)
                elif is_active == 1:
                    worksheet.write(row_idx, 7, "✅ Faol", active_badge)
                else:
                    worksheet.write(row_idx, 7, "⏸ Bloklagan", blocked_badge)

                if is_vip == 1:
                    worksheet.write(row_idx, 8, "⭐️ VIP", vip_badge)
                else:
                    worksheet.write(row_idx, 8, "Oddiy", center_format)

            # Ustun kengliklari
            worksheet.set_column(0, 0, 6)
            worksheet.set_column(1, 1, 15)
            worksheet.set_column(2, 2, 26)
            worksheet.set_column(3, 3, 20)
            worksheet.set_column(4, 4, 8)
            worksheet.set_column(5, 6, 20)
            worksheet.set_column(7, 8, 15)

            workbook.close()
            return output_path
        except Exception as e:
            logger.error(f"Error exporting to Excel: {e}", exc_info=True)
            return output_path

    def export_users_to_csv(self, output_path: str = "users_export.csv") -> str:
        """Foydalanuvchilar ro'yxatini UTF-8 BOM bilan CSV ga eksport qilish"""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT * FROM users ORDER BY created_at DESC;")
                users = cursor.fetchall()

            with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["User ID", "Full Name", "Username", "Language", "Created At", "Last Active", "Is Active", "Is Banned", "Is VIP"])
                for u in users:
                    writer.writerow([
                        u["user_id"],
                        u["full_name"] or "",
                        u["username"] or "",
                        u["language"] if "language" in u.keys() else "uz",
                        u["created_at"] or "",
                        u["last_active"] or "",
                        u["is_active"] if "is_active" in u.keys() else 1,
                        u["is_banned"] if "is_banned" in u.keys() else 0,
                        u["is_vip"] if "is_vip" in u.keys() else 0
                    ])
            return output_path
        except Exception as e:
            logger.error(f"Error exporting to CSV: {e}")
            return output_path

    def backup_database(self, output_path: str = None) -> str:
        """SQLite ma'lumotlar bazasining to'liq zaxira (backup) nusxasini olish"""
        if not output_path:
            output_path = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite"
        try:
            with self._get_connection() as src_conn:
                dst_conn = sqlite3.connect(output_path)
                with dst_conn:
                    src_conn.backup(dst_conn)
                dst_conn.close()
            return output_path
        except Exception as e:
            logger.error(f"Error backing up database: {e}")
            return ""

    # =========================================================================
    # IZOH REKLAMASI (CAPTION ADS)
    # =========================================================================

    def get_active_ad(self) -> dict | None:
        """Hozirda faol bo'lgan izoh reklamasini olish"""
        try:
            with self._get_connection() as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM caption_ads 
                    WHERE is_active = 1 AND (expires_at IS NULL OR expires_at > datetime('now'))
                    ORDER BY id DESC LIMIT 1;
                """)
                row = cursor.fetchone()
                return dict(row) if row else None
        except sqlite3.Error as e:
            logger.error(f"Error getting active ad: {e}")
            return None

    def set_caption_ad(self, ad_text: str, ad_link: str = None, duration_hours: float = None):
        """Yangi izoh reklamasini o'rnatish"""
        now = datetime.now(TASHKENT_TZ)
        now_str = now.strftime("%Y-%m-%d %H:%M:%S")
        exp_str = None
        if duration_hours is not None and duration_hours > 0:
            exp_str = (now + timedelta(hours=duration_hours)).strftime("%Y-%m-%d %H:%M:%S")

        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE caption_ads SET is_active = 0;")
                cursor.execute("""
                    INSERT INTO caption_ads (ad_text, ad_link, expires_at, is_active, created_at)
                    VALUES (?, ?, ?, 1, ?);
                """, (ad_text, ad_link, exp_str, now_str))
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error setting caption ad: {e}")

    def disable_caption_ad(self):
        """Faol izoh reklamasini o'chirish"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE caption_ads SET is_active = 0;")
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error disabling caption ad: {e}")

    def get_caption_ad_footer(self) -> str:
        """Chiquvchi fayllar tagiga qo'shiladigan izoh reklama matnini shakllantirish"""
        ad = self.get_active_ad()
        if ad and ad.get('ad_text'):
            text = ad['ad_text'].strip()
            link = ad.get('ad_link')
            if link and link.startswith(('http://', 'https://', 'tg://', 't.me/')):
                return f"\n\n📢 <a href='{link}'>{text}</a>"
            return f"\n\n📢 <b>{text}</b>"
        return ""

    # =========================================================================
    # PLATFORMALAR & MODULLAR STATISTIKASI
    # =========================================================================

    def get_platform_stats(self) -> dict:
        """Konvertatsiya platformalari va modullari bo'yicha statistikani olish"""
        default_modules = [
            "📄 PDF & Hujjatlar",
            "🖼 Rasm formatlari",
            "🎵 Audio & Musiqa",
            "🎙 AI Ovoz tozalash",
            "🎧 ASMR Ruchka yozuvi",
            "🔍 OCR Matn ajratish",
            "📦 ZIP Arxivlar"
        ]
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT module_name, count FROM conversion_stats ORDER BY count DESC;")
                rows = cursor.fetchall()
                data_dict = {r[0]: r[1] for r in rows}

                for m in default_modules:
                    if m not in data_dict:
                        data_dict[m] = 0

                total = sum(data_dict.values())
                breakdown = []
                for mod_name in default_modules:
                    cnt = data_dict.get(mod_name, 0)
                    pct = round((cnt / total * 100), 1) if total > 0 else 0.0
                    breakdown.append({'platform': mod_name, 'count': cnt, 'pct': pct})

                return {'total': total, 'breakdown': breakdown}
        except sqlite3.Error as e:
            logger.error(f"Error getting platform stats: {e}")
            return {'total': 0, 'breakdown': []}

    def increment_conversion_stat(self, module_name: str, count: int = 1):
        """Muayyan konvertatsiya moduli hisoblagichini oshirish"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO conversion_stats (module_name, count)
                    VALUES (?, ?)
                    ON CONFLICT(module_name) DO UPDATE SET count = count + ?;
                """, (module_name, count, count))
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error incrementing stat for {module_name}: {e}")
