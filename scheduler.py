# scheduler.py
import asyncio
import logging
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from database import db, ScheduledJob
from instagram_manager import ig_manager
from downloader import downloader
from pathlib import Path

logger = logging.getLogger(__name__)

class PostScheduler:
    def __init__(self, telegram_app):
        self.app = telegram_app
        self.scheduler = AsyncIOScheduler()
        self.is_running = False
    
    def start(self):
        """Start the background scheduler"""
        if not self.is_running:
            self.scheduler.add_job(
                self._check_pending_jobs,
                IntervalTrigger(seconds=30),
                id='check_jobs',
                replace_existing=True
            )
            self.scheduler.start()
            self.is_running = True
            logger.info("Scheduler started")
    
    def stop(self):
        """Stop the scheduler"""
        if self.is_running:
            self.scheduler.shutdown()
            self.is_running = False
    
    async def _check_pending_jobs(self):
        """Check and execute pending jobs"""
        jobs = db.get_pending_jobs()
        
        for job in jobs:
            logger.info(f"Processing job {job.id} for {job.target_account}")
            
            try:
                if job.platform == 'instagram':
                    success, msg = ig_manager.upload_video(
                        job.target_account,
                        job.video_path,
                        job.caption
                    )
                else:  # telegram
                    success = await self._post_to_telegram(
                        job.target_account,
                        job.video_path,
                        job.caption
                    )
                    msg = "Posted successfully" if success else "Failed to post"
                
                # Update status
                status = 'completed' if success else 'failed'
                db.update_job_status(job.id, status, None if success else msg)
                
                # Cleanup file
                if success or not Path(job.video_path).exists():
                    downloader.cleanup(job.video_path)
                    
                logger.info(f"Job {job.id} completed: {status}")
                
            except Exception as e:
                logger.error(f"Job {job.id} error: {e}")
                db.update_job_status(job.id, 'failed', str(e))
    
    async def _post_to_telegram(self, channel_id: str, video_path: str, caption: str) -> bool:
        """Post video to Telegram channel"""
        try:
            with open(video_path, 'rb') as video_file:
                await self.app.bot.send_video(
                    chat_id=channel_id,
                    video=video_file,
                    caption=caption,
                    supports_streaming=True
                )
            return True
        except Exception as e:
            logger.error(f"Telegram post error: {e}")
            return False
    
    def schedule_job(self, user_id: int, platform: str, target: str, 
                    video_path: str, caption: str, schedule_time: datetime) -> int:
        """Add job to database"""
        return db.add_job(user_id, platform, target, video_path, caption, schedule_time)

scheduler = None  # Initialized in bot.py with app context
