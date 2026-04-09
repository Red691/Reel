import os
import logging
from pathlib import Path
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ChallengeRequired, BadPassword, TwoFactorRequired
from typing import Optional, Dict, Tuple
import time
from config import Config, SESSIONS_DIR

logger = logging.getLogger(__name__)

class InstagramManager:
    def __init__(self):
        self.clients: Dict[str, Client] = {}
        self._load_preconfigured_accounts()
    
    def _get_session_path(self, username: str) -> Path:
        return SESSIONS_DIR / f"{username}.json"
    
    def _load_preconfigured_accounts(self):
        """Load Instagram accounts from .env"""
        for username, password in Config.IG_ACCOUNTS.items():
            logger.info(f"Auto-loading account: {username}")
            self._login(username, password)
    
    def _login(self, username: str, password: str) -> Tuple[bool, str]:
        """Login with challenge handling"""
        client = Client()
        session_path = self._get_session_path(username)
        
        # Set delay range to avoid rate limits
        client.delay_range = [2, 5]
        
        try:
            # Try existing session first
            if session_path.exists():
                try:
                    logger.info(f"Loading session for {username}")
                    client.load_settings(str(session_path))
                    client.login(username, password)
                    
                    # Verify session works
                    client.get_timeline_feed()
                    self.clients[username] = client
                    logger.info(f"Session loaded successfully for {username}")
                    return True, "Session restored"
                    
                except (LoginRequired, ChallengeRequired) as e:
                    logger.warning(f"Session expired/invalid for {username}, trying fresh login...")
                    # Delete old session and try fresh
                    os.remove(session_path)
                    client = Client()  # Fresh client
                    client.delay_range = [2, 5]
            
            # Fresh login
            logger.info(f"Attempting fresh login for {username}")
            client.login(username, password)
            
            # If we get here, login worked
            client.dump_settings(str(session_path))
            self.clients[username] = client
            logger.info(f"Fresh login successful for {username}")
            return True, "Login successful"
            
        except ChallengeRequired:
            logger.error(f"Challenge required for {username} - Instagram flagged the login")
            # Try to handle challenge
            try:
                client.challenge_resolve(client.last_json)
                client.dump_settings(str(session_path))
                self.clients[username] = client
                return True, "Challenge resolved"
            except Exception as e:
                logger.error(f"Challenge resolution failed: {e}")
                return False, f"Instagram security check required. Login from your phone first, then retry."
                
        except TwoFactorRequired:
            logger.error(f"2FA enabled for {username}")
            return False, "2FA is enabled on this account. Disable it temporarily or use app-based 2FA."
            
        except BadPassword:
            logger.error(f"Bad password for {username}")
            return False, "Incorrect password"
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Login error for {username}: {error_msg}")
            
            if "invalid_user" in error_msg.lower():
                return False, "Username not found"
            elif "checkpoint" in error_msg.lower():
                return False, "Instagram security checkpoint. Open Instagram app and confirm 'It was me'"
            elif "rate_limit" in error_msg.lower() or "wait" in error_msg.lower():
                return False, "Rate limited. Wait 10-15 minutes before retrying."
            else:
                return False, f"Login failed: {error_msg[:100]}"
    
    def add_account(self, username: str, password: str) -> Tuple[bool, str]:
        """Add new Instagram account with retry logic"""
        # Clean username
        username = username.strip().lower().replace("@", "")
        
        if username in self.clients:
            return True, "Account already logged in"
        
        # Try login with delay
        success, msg = self._login(username, password)
        
        if success:
            return True, f"✅ Successfully added @{username}"
        else:
            # If failed, suggest workaround
            full_msg = f"❌ {msg}\n\n"
            full_msg += "Try this:\n"
            full_msg += "1. Open Instagram on your phone\n"
            full_msg += "2. Check for 'Suspicious Login' notification\n"
            full_msg += "3. Tap 'It was me' / 'Yes, this was me'\n"
            full_msg += "4. Wait 2 minutes\n"
            full_msg += "5. Try again here"
            return False, full_msg
    
    def upload_video(self, username: str, video_path: str, caption: str) -> Tuple[bool, str]:
        """Upload video with re-login on session expiry"""
        if username not in self.clients:
            return False, "Account not found"
        
        client = self.clients[username]
        
        try:
            # Check session validity
            try:
                client.get_timeline_feed()
            except LoginRequired:
                logger.info(f"Session expired for {username}, attempting relogin...")
                # Try to reload settings
                session_path = self._get_session_path(username)
                if session_path.exists():
                    client.load_settings(str(session_path))
                    # Need password for relogin - we don't store it, so fail
                    return False, "Session expired. Please remove and re-add the account."
            
            # Upload
            logger.info(f"Uploading video to {username}: {video_path}")
            
            # Check if thumbnail exists or generate it
            from instagrapi.types import StoryMention, StoryLocation, StoryHashtag, StoryLink, StorySticker
            
            media = client.video_upload(
                Path(video_path),
                caption=caption,
                thumbnail=None  # Auto generate
            )
            
            return True, f"✅ Posted successfully!"
            
        except Exception as e:
            logger.error(f"Upload error for {username}: {e}")
            return False, f"Upload failed: {str(e)[:200]}"
    
    def get_accounts(self) -> list:
        """Get list of logged-in usernames"""
        return list(self.clients.keys())
    
    def remove_account(self, username: str):
        """Remove account and session file"""
        if username in self.clients:
            del self.clients[username]
        
        session_path = self._get_session_path(username)
        if session_path.exists():
            os.remove(session_path)
            logger.info(f"Removed session for {username}")

ig_manager = InstagramManager()
