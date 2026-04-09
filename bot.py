"""
ReelBot Pro — Multi Account Auto Poster
Apni videos (YouTube/Instagram link ya direct file) multiple accounts pe post karo
"""

import os, json, logging, asyncio, re, tempfile
from datetime import datetime
from pathlib import Path

import requests
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
BOT_TOKEN   = os.environ["BOT_TOKEN"]
ADMIN_IDS   = list(map(int, os.environ.get("ADMIN_IDS", "0").split(",")))
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
PORT        = int(os.environ.get("PORT", 8443))
DATA_FILE   = "data.json"
DOWNLOADS   = "downloads"
os.makedirs(DOWNLOADS, exist_ok=True)

# ── States ─────────────────────────────────────────────────────────────────────
(
    ADD_PLATFORM, ADD_USERNAME, ADD_IG_USER, ADD_IG_TOKEN,
    ADD_TG_CHAT, ADD_TG_TOKEN,
    POST_SOURCE, POST_CAPTION, POST_ACCOUNTS, POST_WHEN,
) = range(10)

# ── Data ───────────────────────────────────────────────────────────────────────
def load():
    if Path(DATA_FILE).exists():
        with open(DATA_FILE) as f:
            return json.load(f)
    return {"accounts": [], "posts": [], "nid": 1}

def save(d):
    with open(DATA_FILE, "w") as f:
        json.dump(d, f, indent=2)

data = load()
scheduler = AsyncIOScheduler()

def is_admin(uid): return uid in ADMIN_IDS
def get_acc(aid):  return next((a for a in data["accounts"] if a["id"] == aid), None)

# ── Video Downloader (yt-dlp) ──────────────────────────────────────────────────
def download_video(url: str) -> str | None:
    """Download video from any URL using yt-dlp. Returns local file path."""
    out_tmpl = os.path.join(DOWNLOADS, "%(id)s.%(ext)s")
    ydl_opts = {
        "outtmpl": out_tmpl,
        "format": "mp4/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "merge_output_format": "mp4",
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            fname = ydl.prepare_filename(info)
            if not fname.endswith(".mp4"):
                fname = fname.rsplit(".", 1)[0] + ".mp4"
            return fname if os.path.exists(fname) else None
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return None

# ── Instagram Post ─────────────────────────────────────────────────────────────
def ig_post_reel(account: dict, video_path: str, caption: str) -> tuple[bool, str]:
    token     = account.get("ig_token")
    user_id   = account.get("ig_user_id")
    if not token or not user_id:
        return False, "Missing IG token or user ID"

    init_url = f"https://graph.facebook.com/v19.0/{user_id}/media"
    r = requests.post(init_url, data={
        "media_type":   "REELS",
        "caption":      caption,
        "access_token": token,
        "upload_type":  "resumable",
    })
    if not r.ok:
        return False, f"Init error: {r.text[:200]}"

    container_id = r.json().get("id")
    if not container_id:
        return False, "No container ID returned"

    pub_url = f"https://graph.facebook.com/v19.0/{user_id}/media_publish"
    r2 = requests.post(pub_url, data={
        "creation_id":  container_id,
        "access_token": token,
    })
    if r2.ok:
        return True, r2.json().get("id", "posted")
    return False, r2.text[:200]

# ── Telegram Channel Post ──────────────────────────────────────────────────────
async def tg_post_video(app, account: dict, video_path: str, caption: str) -> tuple[bool, str]:
    try:
        chat_id  = account.get("tg_chat_id")
        bot_tok  = account.get("tg_bot_token", BOT_TOKEN)
        if bot_tok != BOT_TOKEN:
            from telegram import Bot
            bot = Bot(token=bot_tok)
        else:
            bot = app.bot

        with open(video_path, "rb") as vf:
            await bot.send_video(chat_id=chat_id, video=vf, caption=caption, supports_streaming=True)
        return True, "✅ Sent"
    except Exception as e:
        return False, str(e)[:200]

# ── Run Scheduled Post ─────────────────────────────────────────────────────────
async def run_post(app, post_id: int):
    global data
    data = load()
    post = next((p for p in data["posts"] if p["id"] == post_id), None)
    if not post or post["status"] != "scheduled": return

    src      = post.get("source")
    caption  = post.get("caption", "")
    results  = []

    if isinstance(src, str) and src.startswith("http"):
        msg = await app.bot.send_message(ADMIN_IDS[0], f"⬇️ Downloading video for Post #{post_id}...")
        video_path = download_video(src)
        if not video_path:
            for aid in ADMIN_IDS:
                await app.bot.send_message(aid, f"❌ Post #{post_id}: Video download failed!")
            post["status"] = "failed"
            save(data)
            return
        await msg.delete()
    else:
        video_path = src

    for acc_id in post.get("account_ids", []):
        acc = get_acc(acc_id)
        if not acc: continue
        if acc["platform"] == "Instagram":
            ok, info = ig_post_reel(acc, video_path, caption)
        else:
            ok, info = await tg_post_video(app, acc, video_path, caption)
        results.append(f"{'✅' if ok else '❌'} *{acc['username']}*: {info}")

    post["status"]  = "sent"
    post["sent_at"] = datetime.now().isoformat()
    save(data)

    report = "\n".join(results)
    for aid in ADMIN_IDS:
        try: await app.bot.send_message(aid, f"📬 *Post #{post_id} Done!*\n\n{report}", parse_mode="Markdown")
        except Exception: pass

# ── Commands / Conversation Handlers ───────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied."); return
    kb = [["🎬 New Post", "📱 Accounts"], ["📋 Posts", "⏳ Scheduled"], ["📊 Stats", "❓ Help"]]
    await update.message.reply_text(
        "🎬 *ReelBot Pro*\n\nAuto poster — multi account!\nApna video link ya file bhejo.",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True),
        parse_mode="Markdown"
    )

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Commands:*\n\n"
        "/addaccount — Account add karo\n"
        "/accounts — Accounts list\n"
        "/newpost — Naya post schedule karo\n"
        "/posts — Saare posts\n"
        "/scheduled — Pending posts\n"
        "/stats — Stats\n"
        "/cancel — Cancel\n\n"
        "📌 *Flow:*\n1️⃣ /addaccount\n2️⃣ /newpost → link ya video bhejo\n3️⃣ Caption likho\n4️⃣ Accounts chuno\n5️⃣ Time set karo ✅",
        parse_mode="Markdown"
    )

# (All add_account, new_post, list_posts, scheduled_posts, delete_post_cb, stats, cancel
# functions remain the same as in your original code)

# ── MAIN ───────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # ── Conversation Handlers with per_message=False ────────────────────────────
    acc_conv = ConversationHandler(
        entry_points=[CommandHandler("addaccount", add_acc_start)],
        states={ ... },  # same as original
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )

    post_conv = ConversationHandler(
        entry_points=[CommandHandler("newpost", new_post_start),
                      MessageHandler(filters.Regex("^🎬 New Post$"), new_post_start)],
        states={ ... },  # same as original
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False
    )

    # ── Add handlers ─────────────────────────────────────────────────────────────
    app.add_handler(acc_conv)
    app.add_handler(post_conv)
    app.add_handler(CallbackQueryHandler(delete_post_cb, pattern="^DEL_"))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("accounts", list_accounts))
    app.add_handler(CommandHandler("posts", list_posts))
    app.add_handler(CommandHandler("scheduled", scheduled_posts))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(MessageHandler(filters.Regex("^📱 Accounts$"), list_accounts))
    app.add_handler(MessageHandler(filters.Regex("^📋 Posts$"), list_posts))
    app.add_handler(MessageHandler(filters.Regex("^⏳ Scheduled$"), scheduled_posts))
    app.add_handler(MessageHandler(filters.Regex("^📊 Stats$"), stats))
    app.add_handler(MessageHandler(filters.Regex("^❓ Help$"), help_cmd))

    scheduler.start()

    # Restore scheduled jobs
    for p in data["posts"]:
        if p["status"] == "scheduled":
            try:
                run_at = datetime.fromisoformat(p["scheduled_at"])
                if run_at > datetime.now():
                    scheduler.add_job(run_post, DateTrigger(run_date=run_at),
                                      args=[app, p["id"]], id=f"post_{p['id']}", replace_existing=True)
            except Exception as e:
                logger.warning(f"Job restore failed: {e}")

    # ── Run bot ────────────────────────────────────────────────────────────────
    if WEBHOOK_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT,
                        url_path=BOT_TOKEN, webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}",
                        drop_pending_updates=True)
    else:
        app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
