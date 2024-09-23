import logging
import sqlite3

class Database:
    def __init__(self, path_to_db="main.db"):
        self.path_to_db = path_to_db
        self.create_table_users()
        self.create_table_status()
        self.create_table_channels()  # Create the channels table

    @property
    def connection(self):
        return sqlite3.connect(self.path_to_db)

    def execute(self, sql: str, parameters: tuple = None, fetchone=False, fetchall=False, commit=False):
        if not parameters:
            parameters = ()
        try:
            with self.connection as conn:
                cursor = conn.cursor()
                cursor.execute(sql, parameters)

                if commit:
                    conn.commit()
                if fetchall:
                    return cursor.fetchall()
                if fetchone:
                    return cursor.fetchone()
        except sqlite3.Error as e:
            logging.error(f"SQLite error: {e}")
            raise

    def create_table_users(self):
        sql = """
        CREATE TABLE IF NOT EXISTS Users (
            user_id INTEGER PRIMARY KEY,
            full_name TEXT NOT NULL,
            username TEXT
        );
        """
        self.execute(sql, commit=True)

    def create_table_status(self):
        sql = """
        CREATE TABLE IF NOT EXISTS Status (
            active INTEGER,
            block INTEGER
        );
        """
        self.execute(sql, commit=True)

    def create_table_channels(self):
        sql = """
        CREATE TABLE IF NOT EXISTS Channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            channel_id TEXT NOT NULL,
            link TEXT NOT NULL
        );
        """
        self.execute(sql, commit=True)

    def drop_table_channels(self):
        sql = "DROP TABLE IF EXISTS Channels;"
        self.execute(sql, commit=True)

    @staticmethod
    def format_args(sql, parameters: dict):
        if parameters:
            sql += " AND ".join([f"{item} = ?" for item in parameters])
        return sql, tuple(parameters.values())

    def add_user(self, user_id: int, full_name: str, username: str):
        sql = """
        INSERT INTO Users (user_id, full_name, username) VALUES (?, ?, ?)
        """
        self.execute(sql, parameters=(user_id, full_name, username), commit=True)

    def select_all_users(self):
        sql = """
        SELECT * FROM Users
        """
        return self.execute(sql, fetchall=True)

    def select_user(self, **kwargs):
        sql = "SELECT * FROM Users WHERE "
        sql, parameters = self.format_args(sql, kwargs)
        return self.execute(sql, parameters=parameters, fetchone=True)

    def count_users(self):
        sql = "SELECT COUNT(*) FROM Users;"
        return self.execute(sql, fetchone=True)

    def delete_users(self):
        self.execute("DELETE FROM Users", commit=True)

    def add_status(self, active: int = 0, block: int = 0):
        sql = """
        INSERT INTO Status (active, block) VALUES (?, ?)
        """
        self.execute(sql, parameters=(active, block), commit=True)

    def select_block(self):
        sql = "SELECT block FROM Status"
        return self.execute(sql, fetchone=True)

    def select_active(self):
        sql = "SELECT active FROM Status"
        return self.execute(sql, fetchone=True)

    def update_block(self, block):
        sql = "UPDATE Status SET block = ?"
        self.execute(sql, parameters=(block,), commit=True)

    def update_active(self, active):
        sql = "UPDATE Status SET active = ?"
        self.execute(sql, parameters=(active,), commit=True)

    def add_channel(self, name: str, channel_id: str, link: str) -> bool:
        """
        Add a new channel to the database.
        """
        sql = "INSERT INTO Channels (name, channel_id, link) VALUES (?, ?, ?)"
        try:
            self.execute(sql, parameters=(name, channel_id, link), commit=True)
            return True
        except sqlite3.Error as e:
            logging.error(f"Failed to add channel: {e}")
            return False

    def select_all_channels(self):
        sql = """
        SELECT * FROM Channels
        """
        return self.execute(sql, fetchall=True)

    def select_channel(self, **kwargs):
        sql = "SELECT * FROM Channels WHERE "
        sql, parameters = self.format_args(sql, kwargs)
        return self.execute(sql, parameters=parameters, fetchone=True)

    def select_all_channel(self):
        sql = """
        SELECT * FROM Channels
        """
        return self.execute(sql, fetchall=True)

    def delete_channel_by_name(self, name: str) -> bool:
        """
        Delete a channel from t he database by its name.
        """
        sql = "DELETE FROM Channels WHERE name = ?"
        try:
            self.execute(sql, parameters=(name,), commit=True)
            return True
        except sqlite3.Error as e:
            logging.error(f"Failed to delete channel: {e}")
            return False

    def is_subscribed(self, user_id: int, channel_url: str) -> bool:
        # Query the database to check if the user is subscribed to the given channel
        # Return True if subscribed, False otherwise
        # Example implementation:
        query = "SELECT COUNT(*) FROM subscriptions WHERE user_id = ? AND channel_url = ?"
        result = self.execute_query(query, (user_id, channel_url))
        return result[0] > 0

    def get_channels_from_db(self):
        """
        Fetch all channels from the database and return them as a list of tuples
        containing (channel name, channel ID, channel link).
        """
        try:
            channels = self.select_all_channels()
            return [(channel[1], channel[2], channel[3]) for channel in channels]
        except sqlite3.Error as e:
            logging.error(f"Failed to fetch channels: {e}")
            return []

# Configure logging
logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(levelname)s - %(message)s')

# Example usage:
if __name__ == "__main__":
    db = Database()
    channels = db.get_channels_from_db()
    print(channels)
