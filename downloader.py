# downloader.py
import os
import re
import yt_dlp
from pathlib import Path
from typing import Optional, Tuple
import logging
from config import Config, TEMP_DIR

logger = logging.getLogger(__name__)

class VideoDownloader:
    def __init__(self):
        self.ydl_opts = {
            'format': 'best[filesize<50M]/best',  # Prefer smaller files for Telegram
            'outtmpl': str(TEMP_DIR / '%(title)s_%(id)s.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
        }
    
    def is_valid_url(self, url: str) -> bool:
        """Check if URL is from supported platform"""
        url_lower = url.lower()
        return any(domain in url_lower for domain in Config.SUPPORTED_PLATFORMS)
    
    def extract_info(self, url: str) -> Optional[dict]:
        """Extract video info without downloading"""
        try:
            with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(url, download=False)
                return {
                    'title': info.get('title', 'Unknown'),
                    'duration': info.get('duration'),
                    'uploader': info.get('uploader'),
                    'thumbnail': info.get('thumbnail')
                }
        except Exception as e:
            logger.error(f"Error extracting info: {e}")
            return None
    
    def download(self, url: str) -> Tuple[bool, str, Optional[str]]:
        """
        Download video from URL
        Returns: (success, message, file_path)
        """
        if not self.is_valid_url(url):
            return False, "❌ Invalid URL. Supported: YouTube Shorts, Instagram Reels, Facebook", None
        
        try:
            with yt_dlp.YoutubeDL(self.ydl_opts) as ydl:
                # Get info first to check file size
                info = ydl.extract_info(url, download=False)
                
                # Check if file size is acceptable (Telegram limit ~50MB for bots)
                filesize = info.get('filesize') or info.get('filesize_approx', 0)
                if filesize and filesize > Config.MAX_FILE_SIZE:
                    return False, f"❌ File too large ({filesize/1024/1024:.1f}MB). Max: 50MB", None
                
                # Download
                logger.info(f"Downloading: {url}")
                ydl.download([url])
                
                # Get downloaded file path
                filename = ydl.prepare_filename(info)
                
                if os.path.exists(filename):
                    return True, "✅ Download complete", filename
                else:
                    # Try to find file with actual extension
                    base_path = os.path.splitext(filename)[0]
                    for ext in ['.mp4', '.mkv', '.webm', '.mov']:
                        if os.path.exists(base_path + ext):
                            return True, "✅ Download complete", base_path + ext
                    
                    return False, "❌ Download failed: File not found", None
                    
        except Exception as e:
            logger.error(f"Download error: {e}")
            return False, f"❌ Download failed: {str(e)}", None
    
    def cleanup(self, file_path: str):
        """Remove downloaded file"""
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Cleaned up: {file_path}")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")

downloader = VideoDownloader()
