import sqlite3
import logging
import atexit
from typing import List, Tuple, Optional, Any

logger = logging.getLogger(__name__)

class DatabaseError(Exception):
    """Custom exception for database-related errors."""
    pass

class MemoryDB:
    def __init__(self, db_path: str = "Nxora_memory.db"):
        self.db_path = db_path
        try:
            self.db_conn = sqlite3.connect(db_path, check_same_thread=False)
            self.init_db()
            logger.info(f"Database connected successfully: {db_path}")
        except sqlite3.Error as e:
            logger.error(f"Failed to connect to database {db_path}: {e}")
            raise DatabaseError(f"Connection failed: {e}")

    def close(self):
        """Safely close the database connection."""
        try:
            if hasattr(self, 'db_conn') and self.db_conn:
                self.db_conn.close()
                self.db_conn = None # Set connection to None after closing
                logger.info("Database connection closed.")
        except Exception as e:
            logger.error(f"Error closing database: {e}")

    def _execute(self, query: str, params: tuple = (), fetch: str = "none") -> Any:
        """Helper method to execute SQL queries with try/except."""
        if not self.db_conn:
            logger.error(f"Database connection is closed. Cannot execute: {query}")
            return None
        try:
            cursor = self.db_conn.cursor()
            cursor.execute(query, params)
            if fetch == "all":
                return cursor.fetchall()
            elif fetch == "one":
                return cursor.fetchone()
            else:
                self.db_conn.commit()
                return cursor.lastrowid
        except sqlite3.Error as e:
            logger.error(f"Database query failed: '{query}' with params {params}. Error: {e}")
            self.db_conn.rollback()
            raise DatabaseError(f"Query execution failed: {e}")

    def init_db(self):
        tables = [
            '''CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, sender TEXT, text TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)''',
            '''CREATE TABLE IF NOT EXISTS user_prefs (key TEXT PRIMARY KEY, value TEXT)''',
            '''CREATE TABLE IF NOT EXISTS sys_command (id INTEGER PRIMARY KEY, name VARCHAR(100), path VARCHAR(1000))''',
            '''CREATE TABLE IF NOT EXISTS web_command (id INTEGER PRIMARY KEY, name VARCHAR(100), url VARCHAR(1000))''',
            '''CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY, name VARCHAR(200), mobile_no VARCHAR(255))'''
        ]
        for query in tables:
            self._execute(query)

    def get_contact(self, name: str) -> Optional[str]:
        result = self._execute("SELECT mobile_no FROM contacts WHERE LOWER(name) LIKE ? OR LOWER(name) LIKE ?", 
                               ('%' + name + '%', name + '%'), fetch="all")
        if result:
            number = str(result[0][0])
            if not number.startswith('+91') and not number.startswith('+'):
                number = '+91' + number
            return number
        return None

    def load_history(self) -> List[Tuple[str, str]]:
        return self._execute("SELECT sender, text FROM messages ORDER BY id ASC", fetch="all")
        
    def save_message(self, sender: str, text: str):
        self._execute("INSERT INTO messages (sender, text) VALUES (?, ?)", (sender, text))

    def set_pref(self, key: str, value: str):
        self._execute('INSERT OR REPLACE INTO user_prefs (key, value) VALUES (?, ?)', (key, value))
        
    def get_pref(self, key: str) -> Optional[str]:
        result = self._execute('SELECT value FROM user_prefs WHERE key = ?', (key,), fetch="one")
        return result[0] if result else None

