# bot.py (Main Entry Point)
import logging
import os
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)
from config import Config, TEMP_DIR
from database import db
from downloader import downloader
from instagram_manager import ig_manager

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
(MAIN_MENU, WAITING_FOR_LINK, WAITING_FOR_FILE, EDIT_CAPTION, 
 SELECT_PLATFORM, SELECT_ACCOUNT, SCHEDULE_OR_POST, SET_SCHEDULE_TIME,
 ADD_IG_ACCOUNT, ADD_IG_PASSWORD) = range(10)

# ==================== HELPER FUNCTIONS ====================

def is_admin(user_id: int) -> bool:
    return not Config.ADMIN_USER_IDS or user_id in Config.ADMIN_USER_IDS

def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("🔗 Send Link", callback_data='send_link'),
         InlineKeyboardButton("📁 Upload Video", callback_data='upload_video')],
        [InlineKeyboardButton("➕ Add Account", callback_data='add_account'),
         InlineKeyboardButton("📋 My Accounts", callback_data='list_accounts')],
        [InlineKeyboardButton("📅 Scheduled Jobs", callback_data='list_jobs')]
    ]
    return InlineKeyboardMarkup(keyboard)

# ==================== COMMAND HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send welcome message"""
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("⛔ You are not authorized to use this bot.")
        return ConversationHandler.END
    
    welcome_text = (
        f"👋 Hello {user.first_name}!\n\n"
        "🤖 Welcome to *ReelBot Pro*\n\n"
        "Features:\n"
        "• Download from YouTube/Instagram links\n"
        "• Upload video files directly\n"
        "• Post to multiple Telegram channels\n"
        "• Post to multiple Instagram accounts\n"
        "• Schedule posts for later\n\n"
        "Select an option below:"
    )
    
    await update.message.reply_text(
        welcome_text, 
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )
    return MAIN_MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel conversation"""
    user_id = update.effective_user.id
    db.clear_user_state(user_id)
    
    # Cleanup any temp data
    if 'current_post' in context.user_data:
        if 'file_path' in context.user_data['current_post']:
            downloader.cleanup(context.user_data['current_post']['file_path'])
        context.user_data.clear()
    
    await update.message.reply_text(
        "❌ Cancelled. Back to main menu:", 
        reply_markup=get_main_keyboard()
    )
    return ConversationHandler.END

# ==================== CALLBACK HANDLERS ====================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button clicks"""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        return
    
    data = query.data
    
    if data == 'send_link':
        await query.edit_message_text(
            "🔗 Send me a YouTube Shorts or Instagram Reel link:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data='back_main')
            ]])
        )
        return WAITING_FOR_LINK
    
    elif data == 'upload_video':
        await query.edit_message_text(
            "📁 Send me a video file (max 50MB):\n\n"
            "Note: Compression may reduce quality.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data='back_main')
            ]])
        )
        return WAITING_FOR_FILE
    
    elif data == 'add_account':
        keyboard = [
            [InlineKeyboardButton("📱 Instagram", callback_data='add_ig')],
            [InlineKeyboardButton("💬 Telegram Channel", callback_data='add_tg')],
            [InlineKeyboardButton("🔙 Back", callback_data='back_main')]
        ]
        await query.edit_message_text(
            "Select platform to add account:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return SELECT_PLATFORM
    
    elif data == 'add_ig':
        await query.edit_message_text(
            "📱 Send your Instagram username:\n\n"
            "⚠️ Note: 2FA must be disabled temporarily",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data='back_main')
            ]])
        )
        db.set_user_state(user_id, 'adding_ig', {})
        return ADD_IG_ACCOUNT
    
    elif data == 'add_tg':
        await query.edit_message_text(
            "💬 Send your Telegram Channel ID or @username\n\n"
            "Format:\n"
            "• @channelusername\n"
            "• -1001234567890 (ID with -100 prefix)\n\n"
            "Make sure the bot is admin in the channel!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data='back_main')
            ]])
        )
        db.set_user_state(user_id, 'adding_tg', {})
        return SELECT_ACCOUNT
    
    elif data == 'list_accounts':
        accounts = db.get_user_accounts(user_id)
        ig_accounts = [a for a in accounts if a.platform == 'instagram']
        tg_accounts = [a for a in accounts if a.platform == 'telegram']
        
        text = "*Your Accounts:*\n\n"
        
        if ig_accounts:
            text += "📱 *Instagram:*\n"
            for acc in ig_accounts:
                text += f"• @{acc.account_name}\n"
        else:
            text += "📱 *Instagram:* None\n"
        
        text += "\n"
        
        if tg_accounts:
            text += "💬 *Telegram Channels:*\n"
            for acc in tg_accounts:
                text += f"• {acc.account_name}\n"
        else:
            text += "💬 *Telegram:* None\n"
        
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=get_main_keyboard()
        )
        return MAIN_MENU
    
    elif data == 'list_jobs':
        jobs = db.get_user_jobs(user_id)
        if not jobs:
            text = "📅 No scheduled jobs found."
        else:
            text = "*Your Scheduled Jobs:*\n\n"
            for job in jobs[:10]:
                status_emoji = "⏳" if job.status == 'pending' else "✅" if job.status == 'completed' else "❌"
                date_str = job.scheduled_time.strftime("%d/%m %H:%M")
                text += f"{status_emoji} `{date_str}` → {job.platform}: {job.target_account}\n"
        
        await query.edit_message_text(
            text,
            parse_mode='Markdown',
            reply_markup=get_main_keyboard()
        )
        return MAIN_MENU
    
    elif data == 'back_main':
        await query.edit_message_text(
            "Main Menu:",
            reply_markup=get_main_keyboard()
        )
        return MAIN_MENU
    
    elif data.startswith('select_'):
        parts = data.split('_', 2)
        if len(parts) >= 3:
            platform = parts[1]
            account = parts[2]
            
            if 'current_post' not in context.user_data:
                context.user_data['current_post'] = {}
            
            context.user_data['current_post']['platform'] = platform
            context.user_data['current_post']['account'] = account
            
            await show_post_options(update, context)
            return SCHEDULE_OR_POST
    
    elif data == 'post_now':
        await process_post(update, context, immediate=True)
        return ConversationHandler.END
    
    elif data == 'schedule_post':
        await query.edit_message_text(
            "📅 Send date/time to schedule\n\n"
            "Format: `DD/MM/YYYY HH:MM`\n"
            "Example: `15/04/2026 14:30`",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data='back_main')
            ]])
        )
        return SET_SCHEDULE_TIME
    
    elif data == 'edit_caption':
        current_caption = context.user_data.get('current_post', {}).get('caption', 'None')
        await query.edit_message_text(
            f"✏️ Send new caption for the video:\n\n"
            f"Current: `{current_caption[:100]}`",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data='back_main')
            ]])
        )
        return EDIT_CAPTION

# ==================== MESSAGE HANDLERS ====================

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process YouTube/Instagram link"""
    url = update.message.text.strip()
    user_id = update.effective_user.id
    
    if not downloader.is_valid_url(url):
        await update.message.reply_text(
            "❌ Invalid URL. Please send YouTube Shorts or Instagram Reel link.",
            reply_markup=get_main_keyboard()
        )
        return MAIN_MENU
    
    status_msg = await update.message.reply_text("⏳ Downloading video...")
    
    success, msg, file_path = downloader.download(url)
    
    if not success:
        await status_msg.edit_text(f"{msg}\n\nTry again:", reply_markup=get_main_keyboard())
        return MAIN_MENU
    
    info = downloader.extract_info(url)
    default_caption = f"📹 {info['title']}\n\nVia @ReelBotPro" if info else "Via @ReelBotPro"
    
    context.user_data['current_post'] = {
        'file_path': file_path,
        'caption': default_caption,
        'original_link': url
    }
    
    await status_msg.delete()
    await show_account_selection(update, context)
    return SELECT_ACCOUNT

async def handle_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded video file"""
    user_id = update.effective_user.id
    
    file = update.message.video or update.message.document
    if not file:
        await update.message.reply_text("❌ Please send a valid video file.")
        return WAITING_FOR_FILE
    
    status_msg = await update.message.reply_text("⏳ Downloading file...")
    
    try:
        if update.message.video:
            file_obj = await update.message.video.get_file()
            ext = ".mp4"
        else:
            file_obj = await update.message.document.get_file()
            ext = ".mp4" if file.mime_type == 'video/mp4' else ""
        
        file_path = str(TEMP_DIR / f"upload_{user_id}_{datetime.now().timestamp()}{ext}")
        await file_obj.download_to_drive(file_path)
        
        context.user_data['current_post'] = {
            'file_path': file_path,
            'caption': "📹 New upload\n\nVia @ReelBotPro",
            'original_link': None
        }
        
        await status_msg.delete()
        await show_account_selection(update, context)
        return SELECT_ACCOUNT
        
    except Exception as e:
        logger.error(f"File download error: {e}")
        await status_msg.edit_text("❌ Failed to download file. Try again.")
        return WAITING_FOR_FILE

async def handle_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Update caption"""
    new_caption = update.message.text
    context.user_data['current_post']['caption'] = new_caption
    
    await update.message.reply_text("✅ Caption updated!")
    
    # Show post options again
    query = update.message.reply_text(
        "Processing...",
        reply_markup=InlineKeyboardMarkup([])
    )
    # Create fake update for show_post_options
    class FakeUpdate:
        def __init__(self, msg):
            self.callback_query = type('obj', (object,), {
                'edit_message_text': msg.edit_text,
                'answer': lambda: None
            })()
    
    fake = FakeUpdate(query)
    await show_post_options(fake, context)
    return SCHEDULE_OR_POST

async def handle_schedule_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Parse schedule time"""
    text = update.message.text.strip()
    
    try:
        schedule_time = datetime.strptime(text, "%d/%m/%Y %H:%M")
        
        if schedule_time < datetime.now():
            await update.message.reply_text("❌ Time must be in the future!")
            return SET_SCHEDULE_TIME
        
        await process_post(update, context, immediate=False, schedule_time=schedule_time)
        return ConversationHandler.END
        
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid format! Use: DD/MM/YYYY HH:MM\nExample: 15/04/2026 14:30"
        )
        return SET_SCHEDULE_TIME

async def handle_ig_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store IG username and ask for password"""
    username = update.message.text.strip()
    context.user_data['temp_ig_username'] = username
    
    await update.message.reply_text(
        f"Username: `{username}`\n\nNow send the password:",
        parse_mode='Markdown'
    )
    return ADD_IG_PASSWORD

async def handle_ig_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Login to Instagram"""
    password = update.message.text
    username = context.user_data.get('temp_ig_username')
    user_id = update.effective_user.id
    
    status_msg = await update.message.reply_text("⏳ Logging in to Instagram...")
    
    success, msg = ig_manager.add_account(username, password)
    
    if success:
        db.add_account(user_id, 'instagram', username, {'username': username})
    
    await status_msg.edit_text(msg, reply_markup=get_main_keyboard())
    db.clear_user_state(user_id)
    return MAIN_MENU

async def handle_tg_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add Telegram channel"""
    channel = update.message.text.strip()
    user_id = update.effective_user.id
    
    test_msg = await update.message.reply_text("⏳ Testing channel access...")
    
    try:
        chat = await context.bot.get_chat(channel)
        db.add_account(user_id, 'telegram', channel, {'channel_id': chat.id})
        await test_msg.edit_text(
            f"✅ Channel added successfully!\n\n"
            f"Name: {chat.title}\n"
            f"ID: `{chat.id}`",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
    except Exception as e:
        await test_msg.edit_text(
            f"❌ Failed to access channel.\n\n"
            f"Error: `{str(e)}`\n\n"
            f"Make sure:\n"
            f"1. Bot is admin in the channel\n"
            f"2. You used correct format (@channel or -100xxx)",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
    
    db.clear_user_state(user_id)
    return MAIN_MENU

# ==================== UI HELPERS ====================

async def show_account_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show available accounts to post to"""
    user_id = update.effective_user.id
    accounts = db.get_user_accounts(user_id)
    
    if not accounts:
        msg = "⚠️ No accounts configured!\n\nAdd accounts first:"
        if update.message:
            await update.message.reply_text(msg, reply_markup=get_main_keyboard())
        else:
            await update.callback_query.edit_message_text(msg, reply_markup=get_main_keyboard())
        return MAIN_MENU
    
    keyboard = []
    for acc in accounts:
        emoji = "📱" if acc.platform == 'instagram' else "💬"
        callback = f"select_{acc.platform}_{acc.account_name}"
        keyboard.append([InlineKeyboardButton(
            f"{emoji} {acc.account_name} ({acc.platform})",
            callback_data=callback
        )])
    
    keyboard.append([InlineKeyboardButton("🔙 Cancel", callback_data='back_main')])
    
    msg = "📤 Select target account:"
    
    if isinstance(update, Update):
        if update.message:
            await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.callback_query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard))

async def show_post_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show post now/schedule options"""
    post_data = context.user_data.get('current_post', {})
    
    caption = post_data.get('caption', '')[:200]
    platform = post_data.get('platform', '')
    account = post_data.get('account', '')
    
    text = (
        f"*Ready to Post:*\n\n"
        f"📍 Platform: `{platform}`\n"
        f"👤 Account: `{account}`\n"
        f"📝 Caption: `{caption}...`\n\n"
        f"What would you like to do?"
    )
    
    keyboard = [
        [InlineKeyboardButton("✏️ Edit Caption", callback_data='edit_caption')],
        [InlineKeyboardButton("🚀 Post Now", callback_data='post_now'),
         InlineKeyboardButton("📅 Schedule", callback_data='schedule_post')],
        [InlineKeyboardButton("🔙 Cancel", callback_data='back_main')]
    ]
    
    await update.callback_query.edit_message_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def process_post(update: Update, context: ContextTypes.DEFAULT_TYPE, 
                      immediate: bool, schedule_time: datetime = None):
    """Process the final posting"""
    user_id = update.effective_user.id
    post_data = context.user_data.get('current_post', {})
    
    file_path = post_data.get('file_path')
    caption = post_data.get('caption', '')
    platform = post_data.get('platform')
    account = post_data.get('account')
    
    if not all([file_path, platform, account]):
        await update.message.reply_text("❌ Error: Missing data", reply_markup=get_main_keyboard())
        return
    
    if immediate:
        status_msg = await update.message.reply_text("⏳ Posting...")
        
        try:
            if platform == 'instagram':
                success, msg = ig_manager.upload_video(account, file_path, caption)
            else:
                success = await send_telegram_video(context, account, file_path, caption)
                msg = "Posted successfully!" if success else "Failed to post"
            
            if success:
                await status_msg.edit_text(f"✅ {msg}")
                downloader.cleanup(file_path)
            else:
                await status_msg.edit_text(f"❌ {msg}")
                
        except Exception as e:
            logger.error(f"Post error: {e}")
            await status_msg.edit_text(f"❌ Error: {str(e)}")
    else:
        from scheduler import scheduler
        job_id = scheduler.schedule_job(
            user_id, platform, account, file_path, caption, schedule_time
        )
        
        time_str = schedule_time.strftime("%d/%m/%Y %H:%M")
        await update.message.reply_text(
            f"✅ Scheduled successfully!\n\n"
            f"🆔 Job ID: `{job_id}`\n"
            f"📅 Time: `{time_str}`\n"
            f"📍 Target: {platform} ({account})",
            parse_mode='Markdown',
            reply_markup=get_main_keyboard()
        )

async def send_telegram_video(context, channel_id: str, file_path: str, caption: str) -> bool:
    """Send video to Telegram channel"""
    try:
        with open(file_path, 'rb') as video:
            await context.bot.send_video(
                chat_id=channel_id,
                video=video,
                caption=caption,
                supports_streaming=True,
                read_timeout=60,
                write_timeout=60,
                connect_timeout=30
            )
        return True
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return False

# ==================== SCHEDULER HOOK ====================

async def post_init(application: Application):
    """Startup hook"""
    from scheduler import scheduler as sched
    await sched.start_scheduler()
    logger.info("Bot startup complete!")

# ==================== MAIN ====================

def main():
    """Start the bot"""
    if not Config.BOT_TOKEN:
        logger.error("No BOT_TOKEN found in .env")
        return
    
    application = (
        Application.builder()
        .token(Config.BOT_TOKEN)
        .post_init(post_init)
        .build()
    )
    
    from scheduler import scheduler as sched
    sched.init_app(application)
    
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            MAIN_MENU: [CallbackQueryHandler(button_handler)],
            WAITING_FOR_LINK: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link),
                CallbackQueryHandler(button_handler)
            ],
            WAITING_FOR_FILE: [
                MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_file),
                CallbackQueryHandler(button_handler)
            ],
            SELECT_ACCOUNT: [CallbackQueryHandler(button_handler)],
            EDIT_CAPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_caption),
                CallbackQueryHandler(button_handler)
            ],
            SCHEDULE_OR_POST: [CallbackQueryHandler(button_handler)],
            SET_SCHEDULE_TIME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_schedule_time),
                CallbackQueryHandler(button_handler)
            ],
            SELECT_PLATFORM: [CallbackQueryHandler(button_handler)],
            ADD_IG_ACCOUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ig_username),
                CallbackQueryHandler(button_handler)
            ],
            ADD_IG_PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_ig_password),
                CallbackQueryHandler(button_handler)
            ],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    
    application.add_handler(conv_handler)
    
    logger.info("Starting ReelBot Pro...")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True
    )

if __name__ == '__main__':
    main()
