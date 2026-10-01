import sqlite3
import logging
from datetime import datetime
import pytz
import os
import xlsxwriter as xl

logger = logging.getLogger(__name__)

class Database:
    """
    SQLite ma'lumotlar bazasi bilan xavfsiz va tezkor ishlash klassi.
    Eski va yangi baza strukturalarini avtomatik moslashtiradi (Auto-migration).
    """
    def __init__(self, path_to_db: str = "database.db"):
        self.path_to_db = path_to_db
        self._init_db()

    def _get_connection(self):
        return sqlite3.connect(self.path_to_db, timeout=20)

    def _init_db(self):
        """Baza jadvallarini avtomatik yaratish va tuzilmani yangilash"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                
                # 1. Foydalanuvchilar jadvali
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    full_name TEXT,
                    username TEXT,
                    created_at TEXT
                );
                """)
                
                # Agar eski bazada created_at ustuni bo'lmasa qo'shamiz (Migration)
                cursor.execute("PRAGMA table_info(users)")
                columns = [c[1] for c in cursor.fetchall()]
                if 'created_at' not in columns:
                    cursor.execute("ALTER TABLE users ADD COLUMN created_at TEXT")

                # 2. Holat / Statistika jadvali
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS status (
                    active INTEGER DEFAULT 0,
                    block INTEGER DEFAULT 0
                );
                """)
                cursor.execute("SELECT COUNT(*) FROM status")
                if cursor.fetchone()[0] == 0:
                    cursor.execute("INSERT INTO status (active, block) VALUES (0, 0)")

                # 3. Homiylik kanallari jadvali
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    channel_id TEXT NOT NULL UNIQUE,
                    link TEXT NOT NULL
                );
                """)
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Database initialization error: {e}")

    def add_user(self, user_id: int, full_name: str, username: str = "") -> bool:
        """Yangi foydalanuvchini bazaga qo'shish yoki yangilash"""
        now = datetime.now(pytz.timezone('Asia/Tashkent')).strftime("%Y-%m-%d %H:%M:%S")
        sql = """
        INSERT INTO users (user_id, full_name, username, created_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            full_name = excluded.full_name,
            username = excluded.username;
        """
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, (user_id, full_name or "", username or "", now))
                conn.commit()
                return True
        except sqlite3.Error as e:
            logger.error(f"Error adding user {user_id}: {e}")
            return False

    def select_user(self, user_id: int):
        """Foydalanuvchi ma'lumotlarini olish"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id, full_name, username FROM users WHERE user_id = ?", (user_id,))
                return cursor.fetchone()
        except sqlite3.Error as e:
            logger.error(f"Error selecting user {user_id}: {e}")
            return None

    def count_users(self) -> int:
        """Jami foydalanuvchilar sonini aniq butun son (int) sifatida qaytaradi"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM users")
                res = cursor.fetchone()
                return res[0] if res else 0
        except sqlite3.Error as e:
            logger.error(f"Error counting users: {e}")
            return 0

    def select_all_users(self):
        """Barcha foydalanuvchilar ro'yxatini olish"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id, full_name, username, created_at FROM users")
                return cursor.fetchall()
        except sqlite3.Error as e:
            logger.error(f"Error selecting all users: {e}")
            return []

    def get_status(self):
        """Aktiv va bloklangan foydalanuvchilar sonini olish"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT active, block FROM status LIMIT 1")
                res = cursor.fetchone()
                if res:
                    return res[0], res[1]
                return 0, 0
        except sqlite3.Error as e:
            logger.error(f"Error getting status: {e}")
            return 0, 0

    def update_status(self, active: int, block: int):
        """Aktiv va blok statistikani yangilash"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE status SET active = ?, block = ?", (active, block))
                conn.commit()
        except sqlite3.Error as e:
            logger.error(f"Error updating status: {e}")

    def add_channel(self, name: str, channel_id: str, link: str) -> bool:
        """Yangi majburiy a'zolik kanalini qo'shish"""
        sql = "INSERT INTO channels (name, channel_id, link) VALUES (?, ?, ?)"
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(sql, (name.strip(), channel_id.strip(), link.strip()))
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE channels SET name = ?, link = ? WHERE channel_id = ?", (name.strip(), link.strip(), channel_id.strip()))
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
                cursor.execute("SELECT name, channel_id, link, id FROM channels")
                return cursor.fetchall()
        except sqlite3.Error as e:
            logger.error(f"Error fetching channels: {e}")
            return []

    def delete_channel_by_id(self, channel_db_id: int) -> bool:
        """Kanalni IDsi bo'yicha o'chirish"""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM channels WHERE id = ?", (channel_db_id,))
                conn.commit()
                return cursor.rowcount > 0
        except sqlite3.Error as e:
            logger.error(f"Error deleting channel {channel_db_id}: {e}")
            return False

    def export_users_to_excel(self, output_path: str = "users.xlsx") -> str:
        """Foydalanuvchilar ro'yxatini chiroyli Excel faylga eksport qilish"""
        users = self.select_all_users()
        workbook = xl.Workbook(output_path)
        worksheet = workbook.add_worksheet("Foydalanuvchilar")

        header_format = workbook.add_format({
            'bold': True,
            'align': 'center',
            'valign': 'vcenter',
            'fg_color': '#3b82f6',
            'font_color': '#ffffff',
            'border': 1
        })
        cell_format = workbook.add_format({'align': 'left', 'valign': 'vcenter', 'border': 1})
        num_format = workbook.add_format({'align': 'center', 'valign': 'vcenter', 'border': 1})

        worksheet.set_column('A:A', 8)
        worksheet.set_column('B:B', 18)
        worksheet.set_column('C:C', 30)
        worksheet.set_column('D:D', 22)
        worksheet.set_column('E:E', 22)

        worksheet.write('A1', 'T/r', header_format)
        worksheet.write('B1', 'User ID', header_format)
        worksheet.write('C1', 'F.I.SH / Ism', header_format)
        worksheet.write('D1', 'Username', header_format)
        worksheet.write('E1', "Qo'shilgan sana", header_format)

        for idx, user in enumerate(users, start=1):
            row = idx + 1
            user_id = user[0]
            fullname = user[1] if len(user) > 1 else ""
            username = f"@{user[2]}" if len(user) > 2 and user[2] else "-"
            created_at = user[3] if len(user) > 3 and user[3] else "-"

            worksheet.write(f'A{row}', idx, num_format)
            worksheet.write(f'B{row}', user_id, num_format)
            worksheet.write(f'C{row}', fullname, cell_format)
            worksheet.write(f'D{row}', username, cell_format)
            worksheet.write(f'E{row}', created_at, num_format)

        workbook.close()
        return output_path
