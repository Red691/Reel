# database.py
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from config import Config

@dataclass
class ScheduledJob:
    id: int
    user_id: int
    platform: str  # 'telegram' or 'instagram'
    target_account: str
    video_path: str
    caption: str
    scheduled_time: datetime
    status: str  # 'pending', 'completed', 'failed'
    created_at: datetime
    error_msg: Optional[str] = None

@dataclass
class UserAccount:
    id: int
    user_id: int
    platform: str
    account_name: str
    credentials: Dict[str, Any]  # JSON stored as text
    is_active: bool

class Database:
    def __init__(self):
        self.db_path = Config.DATABASE_PATH
        self.init_db()
    
    def get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_db(self):
        with self.get_conn() as conn:
            # Scheduled jobs table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scheduled_jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    platform TEXT NOT NULL,
                    target_account TEXT NOT NULL,
                    video_path TEXT NOT NULL,
                    caption TEXT,
                    scheduled_time TIMESTAMP NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    error_msg TEXT
                )
            """)
            
            # User accounts table (for Telegram channels/IG accounts)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    platform TEXT NOT NULL,
                    account_name TEXT NOT NULL,
                    credentials TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    UNIQUE(user_id, platform, account_name)
                )
            """)
            
            # User state for conversation handling
            conn.execute("""
                CREATE TABLE IF NOT EXISTS user_states (
                    user_id INTEGER PRIMARY KEY,
                    state TEXT,
                    data TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
    
    # Jobs Management
    def add_job(self, user_id: int, platform: str, target: str, 
                video_path: str, caption: str, scheduled_time: datetime) -> int:
        with self.get_conn() as conn:
            cursor = conn.execute(
                """INSERT INTO scheduled_jobs 
                   (user_id, platform, target_account, video_path, caption, scheduled_time)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (user_id, platform, target, video_path, caption, scheduled_time)
            )
            conn.commit()
            return cursor.lastrowid
    
    def get_pending_jobs(self) -> List[ScheduledJob]:
        with self.get_conn() as conn:
            cursor = conn.execute(
                """SELECT * FROM scheduled_jobs 
                   WHERE status = 'pending' AND scheduled_time <= ?
                   ORDER BY scheduled_time ASC""",
                (datetime.now(),)
            )
            rows = cursor.fetchall()
            return [self._row_to_job(row) for row in rows]
    
    def update_job_status(self, job_id: int, status: str, error_msg: str = None):
        with self.get_conn() as conn:
            conn.execute(
                "UPDATE scheduled_jobs SET status = ?, error_msg = ? WHERE id = ?",
                (status, error_msg, job_id)
            )
            conn.commit()
    
    def get_user_jobs(self, user_id: int) -> List[ScheduledJob]:
        with self.get_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM scheduled_jobs WHERE user_id = ? ORDER BY created_at DESC",
                (user_id,)
            )
            rows = cursor.fetchall()
            return [self._row_to_job(row) for row in rows]
    
    def _row_to_job(self, row: sqlite3.Row) -> ScheduledJob:
        return ScheduledJob(
            id=row['id'],
            user_id=row['user_id'],
            platform=row['platform'],
            target_account=row['target_account'],
            video_path=row['video_path'],
            caption=row['caption'] or "",
            scheduled_time=datetime.fromisoformat(row['scheduled_time']),
            status=row['status'],
            created_at=datetime.fromisoformat(row['created_at']),
            error_msg=row['error_msg']
        )
    
    # Accounts Management
    def add_account(self, user_id: int, platform: str, account_name: str, 
                   credentials: dict) -> bool:
        try:
            with self.get_conn() as conn:
                conn.execute(
                    """INSERT INTO user_accounts (user_id, platform, account_name, credentials)
                       VALUES (?, ?, ?, ?)""",
                    (user_id, platform, account_name, json.dumps(credentials))
                )
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            return False
    
    def get_user_accounts(self, user_id: int, platform: Optional[str] = None) -> List[UserAccount]:
        with self.get_conn() as conn:
            if platform:
                cursor = conn.execute(
                    "SELECT * FROM user_accounts WHERE user_id = ? AND platform = ? AND is_active = 1",
                    (user_id, platform)
                )
            else:
                cursor = conn.execute(
                    "SELECT * FROM user_accounts WHERE user_id = ? AND is_active = 1",
                    (user_id,)
                )
            rows = cursor.fetchall()
            return [self._row_to_account(row) for row in rows]
    
    def remove_account(self, user_id: int, platform: str, account_name: str):
        with self.get_conn() as conn:
            conn.execute(
                "DELETE FROM user_accounts WHERE user_id = ? AND platform = ? AND account_name = ?",
                (user_id, platform, account_name)
            )
            conn.commit()
    
    def _row_to_account(self, row: sqlite3.Row) -> UserAccount:
        return UserAccount(
            id=row['id'],
            user_id=row['user_id'],
            platform=row['platform'],
            account_name=row['account_name'],
            credentials=json.loads(row['credentials'] or '{}'),
            is_active=row['is_active']
        )
    
    # User State Management (for conversations)
    def set_user_state(self, user_id: int, state: str, data: dict):
        with self.get_conn() as conn:
            conn.execute(
                """INSERT INTO user_states (user_id, state, data) 
                   VALUES (?, ?, ?) 
                   ON CONFLICT(user_id) DO UPDATE SET 
                   state=excluded.state, data=excluded.data, updated_at=CURRENT_TIMESTAMP""",
                (user_id, state, json.dumps(data))
            )
            conn.commit()
    
    def get_user_state(self, user_id: int) -> Optional[tuple]:
        with self.get_conn() as conn:
            cursor = conn.execute(
                "SELECT state, data FROM user_states WHERE user_id = ?",
                (user_id,)
            )
            row = cursor.fetchone()
            if row:
                return row['state'], json.loads(row['data'] or '{}')
            return None
    
    def clear_user_state(self, user_id: int):
        with self.get_conn() as conn:
            conn.execute("DELETE FROM user_states WHERE user_id = ?", (user_id,))
            conn.commit()

db = Database()
