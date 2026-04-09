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
            # ensure .mp4
            if not fname.endswith(".mp4"):
                fname = fname.rsplit(".", 1)[0] + ".mp4"
            return fname if os.path.exists(fname) else None
    except Exception as e:
        logger.error(f"Download failed: {e}")
        return None

# ── Instagram Post ─────────────────────────────────────────────────────────────
def ig_post_reel(account: dict, video_path: str, caption: str) -> tuple[bool, str]:
    """Post reel to Instagram via Graph API using local file."""
    token     = account.get("ig_token")
    user_id   = account.get("ig_user_id")
    if not token or not user_id:
        return False, "Missing IG token or user ID"

    # Step 1 — Upload video bytes to get upload URL
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

    # Step 2 — Publish
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
    """Send video to Telegram channel."""
    try:
        chat_id  = account.get("tg_chat_id")
        bot_tok  = account.get("tg_bot_token", BOT_TOKEN)
        # Use separate bot token if account has its own
        if bot_tok != BOT_TOKEN:
            from telegram import Bot
            bot = Bot(token=bot_tok)
        else:
            bot = app.bot

        with open(video_path, "rb") as vf:
            await bot.send_video(
                chat_id=chat_id,
                video=vf,
                caption=caption,
                supports_streaming=True,
            )
        return True, "✅ Sent"
    except Exception as e:
        return False, str(e)[:200]

# ── Run Scheduled Post ─────────────────────────────────────────────────────────
async def run_post(app, post_id: int):
    global data
    data = load()
    post = next((p for p in data["posts"] if p["id"] == post_id), None)
    if not post or post["status"] != "scheduled":
        return

    src      = post.get("source")       # URL or local file_path
    caption  = post.get("caption", "")
    results  = []

    # Download if URL
    if src.startswith("http"):
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
        video_path = src  # local path from direct upload

    for acc_id in post.get("account_ids", []):
        acc = get_acc(acc_id)
        if not acc:
            continue
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
        try:
            await app.bot.send_message(aid, f"📬 *Post #{post_id} Done!*\n\n{report}", parse_mode="Markdown")
        except Exception:
            pass

# ── /start ─────────────────────────────────────────────────────────────────────
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Access denied.")
        return
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

# ── ADD ACCOUNT ────────────────────────────────────────────────────────────────
async def add_acc_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    kb = [[
        InlineKeyboardButton("📷 Instagram", callback_data="P_Instagram"),
        InlineKeyboardButton("✈️ Telegram",  callback_data="P_Telegram"),
    ]]
    await update.message.reply_text("➕ Platform chuno:", reply_markup=InlineKeyboardMarkup(kb))
    return ADD_PLATFORM

async def add_acc_platform(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    ctx.user_data["nacc"] = {"platform": q.data.replace("P_", "")}
    plat = ctx.user_data["nacc"]["platform"]
    await q.edit_message_text(f"*{plat}* selected.\n\nAccount ka username likho (e.g. @mypage):", parse_mode="Markdown")
    return ADD_USERNAME

async def add_acc_username(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["nacc"]["username"] = update.message.text.strip()
    plat = ctx.user_data["nacc"]["platform"]
    if plat == "Instagram":
        await update.message.reply_text("Instagram *User ID* (numeric) bhejo:", parse_mode="Markdown")
        return ADD_IG_USER
    else:
        await update.message.reply_text(
            "Telegram *Chat ID* bhejo\n(e.g. -1001234567890)\n\n"
            "📌 Chat ID pane ke liye: @JsonDumpBot", parse_mode="Markdown"
        )
        return ADD_TG_CHAT

async def add_acc_ig_user(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["nacc"]["ig_user_id"] = update.message.text.strip()
    await update.message.reply_text("Instagram *Page Access Token* bhejo:", parse_mode="Markdown")
    return ADD_IG_TOKEN

async def add_acc_ig_token(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["nacc"]["ig_token"] = update.message.text.strip()
    return await _finalize_acc(update, ctx)

async def add_acc_tg_chat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["nacc"]["tg_chat_id"] = update.message.text.strip()
    await update.message.reply_text(
        "Is channel ke liye alag Bot Token hai?\n"
        "Agar nahi toh /skip likho (main bot token use hoga):",
    )
    return ADD_TG_TOKEN

async def add_acc_tg_token(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt != "/skip":
        ctx.user_data["nacc"]["tg_bot_token"] = txt
    return await _finalize_acc(update, ctx)

async def _finalize_acc(update, ctx):
    global data
    acc = ctx.user_data.pop("nacc")
    acc["id"]       = data["nid"]
    acc["active"]   = True
    acc["added_at"] = datetime.now().isoformat()
    data["accounts"].append(acc)
    data["nid"] += 1
    save(data)
    await update.message.reply_text(f"✅ *{acc['username']}* ({acc['platform']}) add ho gaya!", parse_mode="Markdown")
    return ConversationHandler.END

# ── LIST ACCOUNTS ──────────────────────────────────────────────────────────────
async def list_accounts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    if not data["accounts"]:
        await update.message.reply_text("Koi account nahi. /addaccount use karo."); return
    lines = []
    for a in data["accounts"]:
        e = "🟢" if a.get("active") else "🔴"
        p = "📷" if a["platform"] == "Instagram" else "✈️"
        lines.append(f"{e} {p} `{a['id']}` — *{a['username']}*")
    await update.message.reply_text("👤 *Accounts:*\n\n" + "\n".join(lines), parse_mode="Markdown")

# ── NEW POST ───────────────────────────────────────────────────────────────────
async def new_post_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    if not data["accounts"]:
        await update.message.reply_text("❌ Pehle /addaccount se account add karo!")
        return ConversationHandler.END
    await update.message.reply_text(
        "🎬 *New Post*\n\n"
        "Video ka *link* bhejo (YouTube Shorts, Instagram, etc.)\n"
        "Ya seedha *video file* bhejo 📁",
        parse_mode="Markdown"
    )
    return POST_SOURCE

async def new_post_source(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["npost"] = {}
    msg = update.message

    if msg.video or msg.document:
        # Direct file upload
        vid = msg.video or msg.document
        ctx.user_data["npost"]["file_id"]  = vid.file_id
        ctx.user_data["npost"]["src_type"] = "file"
        await msg.reply_text("✅ Video mili!\n\n📝 Ab *caption* likho:", parse_mode="Markdown")
        return POST_CAPTION

    elif msg.text and msg.text.startswith("http"):
        ctx.user_data["npost"]["url"]      = msg.text.strip()
        ctx.user_data["npost"]["src_type"] = "url"
        await msg.reply_text("🔗 Link mila!\n\n📝 Ab *caption* likho\n(Ya /skip karo original caption ke liye):", parse_mode="Markdown")
        return POST_CAPTION

    await msg.reply_text("❌ Link ya video file bhejo.")
    return POST_SOURCE

async def new_post_caption(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    ctx.user_data["npost"]["caption"] = "" if txt == "/skip" else txt

    active = [a for a in data["accounts"] if a.get("active")]
    ctx.user_data["npost"]["account_ids"] = []

    kb = []
    for a in active:
        p = "📷" if a["platform"] == "Instagram" else "✈️"
        kb.append([InlineKeyboardButton(f"{p} {a['username']}", callback_data=f"A_{a['id']}")])
    kb.append([InlineKeyboardButton("✅ Done", callback_data="A_done")])

    await update.message.reply_text(
        "👤 Accounts chuno (sab jahan post karna hai):",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    return POST_ACCOUNTS

async def new_post_accounts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()

    if q.data == "A_done":
        if not ctx.user_data["npost"]["account_ids"]:
            await q.answer("⚠️ Kam az kam ek account chuno!", show_alert=True)
            return POST_ACCOUNTS
        await q.edit_message_text(
            "📅 *Schedule karo:*\n\n"
            "• `now` — Abhi post karo\n"
            "• `2026-04-10 09:30` — Date & time\n\n"
            "Format: `YYYY-MM-DD HH:MM`",
            parse_mode="Markdown"
        )
        return POST_WHEN

    acc_id = int(q.data.replace("A_", ""))
    ids = ctx.user_data["npost"]["account_ids"]
    if acc_id in ids:
        ids.remove(acc_id)
        await q.answer("❌ Removed")
    else:
        ids.append(acc_id)
        acc = get_acc(acc_id)
        await q.answer(f"✅ {acc['username']} selected")
    return POST_ACCOUNTS

async def new_post_when(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global data
    txt   = update.message.text.strip().lower()
    npost = ctx.user_data.pop("npost")
    app   = ctx.application

    now_post = txt == "now"
    if now_post:
        run_at = datetime.now()
        status = "scheduled"
    else:
        try:
            run_at = datetime.strptime(txt, "%Y-%m-%d %H:%M")
            status = "scheduled"
        except ValueError:
            await update.message.reply_text("❌ Format galat! Use: `2026-04-10 09:30` ya `now`", parse_mode="Markdown")
            return POST_WHEN

    # If direct file, download from Telegram first and save locally
    src = npost.get("url") or npost.get("file_id")
    if npost.get("src_type") == "file":
        wait_msg = await update.message.reply_text("⬇️ File save ho rahi hai...")
        tg_file  = await app.bot.get_file(npost["file_id"])
        local    = os.path.join(DOWNLOADS, f"{npost['file_id']}.mp4")
        await tg_file.download_to_drive(local)
        src = local
        await wait_msg.delete()

    pid = data["nid"]
    post = {
        "id":          pid,
        "source":      src,
        "caption":     npost.get("caption", ""),
        "account_ids": npost["account_ids"],
        "scheduled_at": run_at.isoformat(),
        "status":      status,
        "created_at":  datetime.now().isoformat(),
    }
    data["posts"].append(post)
    data["nid"] += 1
    save(data)

    scheduler.add_job(
        run_post,
        trigger=DateTrigger(run_date=run_at),
        args=[app, pid],
        id=f"post_{pid}",
        replace_existing=True,
    )

    names = [get_acc(i)["username"] for i in npost["account_ids"] if get_acc(i)]
    when  = "Abhi" if now_post else run_at.strftime("%Y-%m-%d %H:%M")
    await update.message.reply_text(
        f"✅ *Post #{pid} Scheduled!*\n\n"
        f"📅 Time: `{when}`\n"
        f"👤 Accounts: {', '.join(names)}\n"
        f"📝 Caption: {post['caption'][:60] or '(original)'}",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

# ── LIST / MANAGE POSTS ────────────────────────────────────────────────────────
async def list_posts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    posts = data["posts"][-8:]
    if not posts:
        await update.message.reply_text("Koi post nahi. /newpost use karo."); return
    for p in posts:
        names = [get_acc(i)["username"] for i in p.get("account_ids", []) if get_acc(i)]
        st    = {"scheduled": "⏳", "sent": "✅", "failed": "❌", "draft": "📝"}.get(p["status"], "❓")
        kb    = [[InlineKeyboardButton("🗑️ Delete", callback_data=f"DEL_{p['id']}")]]
        await update.message.reply_text(
            f"{st} *Post #{p['id']}*\n"
            f"📅 `{p.get('scheduled_at','?')[:16]}`\n"
            f"👤 {', '.join(names)}\n"
            f"📝 {p.get('caption','')[:60] or '(original)'}",
            reply_markup=InlineKeyboardMarkup(kb),
            parse_mode="Markdown"
        )

async def scheduled_posts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    pend = [p for p in data["posts"] if p["status"] == "scheduled"]
    if not pend:
        await update.message.reply_text("⏳ Koi scheduled post nahi."); return
    for p in pend:
        names = [get_acc(i)["username"] for i in p.get("account_ids", []) if get_acc(i)]
        await update.message.reply_text(
            f"⏳ *Post #{p['id']}*\n📅 `{p['scheduled_at'][:16]}`\n👤 {', '.join(names)}\n📝 {p.get('caption','')[:60]}",
            parse_mode="Markdown"
        )

async def delete_post_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global data
    q = update.callback_query; await q.answer()
    pid = int(q.data.replace("DEL_", ""))
    data["posts"] = [p for p in data["posts"] if p["id"] != pid]
    save(data)
    try: scheduler.remove_job(f"post_{pid}")
    except Exception: pass
    await q.edit_message_text(f"🗑️ Post #{pid} delete ho gaya.")

async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    total = len(data["posts"])
    sch   = sum(1 for p in data["posts"] if p["status"] == "scheduled")
    sent  = sum(1 for p in data["posts"] if p["status"] == "sent")
    fail  = sum(1 for p in data["posts"] if p["status"] == "failed")
    accs  = len(data["accounts"])
    await update.message.reply_text(
        f"📊 *Stats*\n\n"
        f"👤 Accounts: {accs}\n"
        f"🎬 Total Posts: {total}\n"
        f"⏳ Scheduled: {sch}\n"
        f"✅ Sent: {sent}\n"
        f"❌ Failed: {fail}",
        parse_mode="Markdown"
    )

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    await update.message.reply_text("❌ Cancel ho gaya.")
    return ConversationHandler.END

# ── MAIN ───────────────────────────────────────────────────────────────────────
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    acc_conv = ConversationHandler(
        entry_points=[CommandHandler("addaccount", add_acc_start)],
        states={
            ADD_PLATFORM: [CallbackQueryHandler(add_acc_platform, pattern="^P_")],
            ADD_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_acc_username)],
            ADD_IG_USER:  [MessageHandler(filters.TEXT & ~filters.COMMAND, add_acc_ig_user)],
            ADD_IG_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_acc_ig_token)],
            ADD_TG_CHAT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, add_acc_tg_chat)],
            ADD_TG_TOKEN: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_acc_tg_token)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    post_conv = ConversationHandler(
        entry_points=[
            CommandHandler("newpost", new_post_start),
            MessageHandler(filters.Regex("^🎬 New Post$"), new_post_start),
        ],
        states={
            POST_SOURCE:   [MessageHandler(filters.TEXT | filters.VIDEO | filters.Document.ALL, new_post_source)],
            POST_CAPTION:  [MessageHandler(filters.TEXT & ~filters.COMMAND, new_post_caption)],
            POST_ACCOUNTS: [CallbackQueryHandler(new_post_accounts, pattern="^A_")],
            POST_WHEN:     [MessageHandler(filters.TEXT & ~filters.COMMAND, new_post_when)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(acc_conv)
    app.add_handler(post_conv)
    app.add_handler(CallbackQueryHandler(delete_post_cb, pattern="^DEL_"))
    app.add_handler(CommandHandler("start",     start))
    app.add_handler(CommandHandler("help",      help_cmd))
    app.add_handler(CommandHandler("accounts",  list_accounts))
    app.add_handler(CommandHandler("posts",     list_posts))
    app.add_handler(CommandHandler("scheduled", scheduled_posts))
    app.add_handler(CommandHandler("stats",     stats))
    app.add_handler(MessageHandler(filters.Regex("^📱 Accounts$"),  list_accounts))
    app.add_handler(MessageHandler(filters.Regex("^📋 Posts$"),     list_posts))
    app.add_handler(MessageHandler(filters.Regex("^⏳ Scheduled$"), scheduled_posts))
    app.add_handler(MessageHandler(filters.Regex("^📊 Stats$"),     stats))
    app.add_handler(MessageHandler(filters.Regex("^❓ Help$"),      help_cmd))

    scheduler.start()

    # Restore saved scheduled jobs
    for p in data["posts"]:
        if p["status"] == "scheduled":
            try:
                run_at = datetime.fromisoformat(p["scheduled_at"])
                if run_at > datetime.now():
                    scheduler.add_job(run_post, DateTrigger(run_date=run_at),
                                      args=[app, p["id"]], id=f"post_{p['id']}", replace_existing=True)
            except Exception as e:
                logger.warning(f"Job restore failed: {e}")

    if WEBHOOK_URL:
        app.run_webhook(listen="0.0.0.0", port=PORT,
                        url_path=BOT_TOKEN, webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}")
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
