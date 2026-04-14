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
            '''CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY, name VARCHAR(200), mobile_no VARCHAR(255))''',
            '''CREATE TABLE IF NOT EXISTS live_matches (
                match_id TEXT PRIMARY KEY,
                teamA TEXT,
                teamB TEXT,
                scoreA TEXT,
                scoreB TEXT,
                status TEXT,
                url TEXT,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            )'''
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

    def save_live_match(self, m_data: dict):
        """Saves or updates a live match in the DB with defensive defaults."""
        if not isinstance(m_data, dict): return
        
        # Use URL as a unique match_id
        match_id = m_data.get('url', 'unknown')
        if not match_id: match_id = 'unknown'
        
        self._execute('''
            INSERT OR REPLACE INTO live_matches (match_id, teamA, teamB, scoreA, scoreB, status, url, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ''', (
            match_id, 
            m_data.get('teamA', 'Unknown'), 
            m_data.get('teamB', 'Unknown'), 
            m_data.get('scoreA', m_data.get('score', '0/0')), 
            m_data.get('scoreB', 'Yet to bat'), 
            m_data.get('status', 'Live'), 
            m_data.get('url', '')
        ))

    def get_latest_matches(self) -> List[dict]:
        """Retrieves all live matches sorted by last update."""
        rows = self._execute("SELECT teamA, teamB, scoreA, scoreB, status, url FROM live_matches ORDER BY last_updated DESC", fetch="all")
        return [
            {"teamA": r[0], "teamB": r[1], "scoreA": r[2], "scoreB": r[3], "status": r[4], "url": r[5]}
            for r in rows
        ]

    def cleanup_live_matches(self, keep_limit: int = 20):
        """Optimizes DB by removing old match snapshots if count exceeds limit."""
        # Simple cleanup logic: for live matches table, it's just a snapshot table
        # If we had a history table, we would purge records older than 24h
        pass
