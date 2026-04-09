# instagram_manager.py
import os
import logging
from pathlib import Path
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ChallengeRequired
from typing import Optional, Dict
from config import Config, SESSIONS_DIR

logger = logging.getLogger(__name__)

class InstagramManager:
    def __init__(self):
        self.clients: Dict[str, Client] = {}  # username -> client
        self._load_preconfigured_accounts()
    
    def _get_session_path(self, username: str) -> Path:
        return SESSIONS_DIR / f"{username}.json"
    
    def _load_preconfigured_accounts(self):
        """Load Instagram accounts from .env"""
        for username, password in Config.IG_ACCOUNTS.items():
            self._login(username, password)
    
    def _login(self, username: str, password: str) -> bool:
        """Login and save session"""
        try:
            client = Client()
            session_path = self._get_session_path(username)
            
            # Try to load existing session
            if session_path.exists():
                try:
                    client.load_settings(str(session_path))
                    client.login(username, password)
                    logger.info(f"Loaded existing session for {username}")
                except:
                    # If session expired, login fresh
                    client.login(username, password)
            else:
                client.login(username, password)
            
            # Save session
            client.dump_settings(str(session_path))
            self.clients[username] = client
            logger.info(f"Successfully logged in: {username}")
            return True
            
        except ChallengeRequired:
            logger.error(f"Challenge required for {username}")
            return False
        except Exception as e:
            logger.error(f"Login failed for {username}: {e}")
            return False
    
    def add_account(self, username: str, password: str) -> tuple[bool, str]:
        """Add new Instagram account"""
        if username in self.clients:
            return True, "Account already exists"
        
        success = self._login(username, password)
        if success:
            return True, f"✅ Successfully added @{username}"
        else:
            return False, "❌ Login failed. Check credentials or 2FA."
    
    def upload_video(self, username: str, video_path: str, caption: str) -> tuple[bool, str]:
        """Upload video to Instagram"""
        if username not in self.clients:
            return False, "Account not found"
        
        client = self.clients[username]
        
        try:
            # Check if login is still valid
            try:
                client.get_timeline_feed()
            except LoginRequired:
                # Re-login if session expired
                session_path = self._get_session_path(username)
                if session_path.exists():
                    os.remove(session_path)
                # We don't have password here, so return error
                return False, "Session expired. Please re-add account."
            
            # Upload
            logger.info(f"Uploading to {username}: {video_path}")
            media = client.video_upload(
                Path(video_path),
                caption=caption,
                thumbnail=None  # Auto-generate
            )
            
            return True, f"✅ Posted successfully! Media ID: {media.pk}"
            
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return False, f"❌ Upload failed: {str(e)}"
    
    def get_accounts(self) -> list[str]:
        """Get list of logged-in usernames"""
        return list(self.clients.keys())
    
    def remove_account(self, username: str):
        """Remove account and session file"""
        if username in self.clients:
            del self.clients[username]
        
        session_path = self._get_session_path(username)
        if session_path.exists():
            os.remove(session_path)

ig_manager = InstagramManager()
