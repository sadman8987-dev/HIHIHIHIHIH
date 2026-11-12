"""
بوت تيليجرام مخصص لتحميل فيديوهات وصوت تيك توك فقط.

requirements.txt:
    python-telegram-bot>=21
    yt-dlp            # حدّثه دايمًا: pip install -U yt-dlp

لازم ffmpeg على السيرفر (apt install ffmpeg).

التشغيل:
    export BOT_TOKEN="توكن البوت"
    python tiktok_bot.py

اختياري: cookies.txt (صيغة Netscape) جنب الملف لو تيك توك حجب السيرفر،
أو حدد مساره بالمتغير COOKIES_FILE.
"""

import asyncio
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

import yt_dlp
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("tiktok-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
COOKIES_FILE = os.getenv("COOKIES_FILE", "cookies.txt")
MAX_SIZE_MB = 50

# يدعم الروابط الطويلة والقصيرة (vm. / vt. / www.)
TIKTOK_RE = re.compile(
    r"https?://(?:www\\.|vm\\.|vt\\.|m\\.)?tiktok\\.com/\\S+", re.IGNORECASE
)


def build_opts(mode: str, outdir: str) -> dict:
    opts = {
        "outtmpl": f"{outdir}/%(id)s.%(ext)s",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 5,
        "fragment_retries": 5,
        "socket_timeout": 30,
        "max_filesize": MAX_SIZE_MB * 1024 * 1024,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
            "Referer": "https://www.tiktok.com/",
        },
    }

    if os.path.isfile(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE

    if mode == "audio":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]
    else:
        opts["format"] = "best[ext=mp4]/best"
        opts["merge_output_format"] = "mp4"

    return opts


def download_sync(url: str, mode: str, outdir: str):
    with yt_dlp.YoutubeDL(build_opts(mode, outdir)) as ydl:
        info = ydl.extract_info(url, download=True)

    files = [p for p in Path(outdir).iterdir() if p.is_file()]
    if not files:
        return None, info
    return max(files, key=lambda p: p.stat().st_size), info


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "أهلاً! ابعتلي أي رابط تيك توك وأنا أحمّلهولك فيديو أو صوت MP3."
    )


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    match = TIKTOK_RE.search(msg.text or "")
    if not match:
        await msg.reply_text("ابعتلي رابط تيك توك صحيح من فضلك.")
        return

    key = str(msg.message_id)
    context.user_data[key] = match.group(0)

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🎬 فيديو", callback_data=f"video:{key}"),
                InlineKeyboardButton("🎵 صوت MP3", callback_data=f"audio:{key}"),
            ]
        ]
    )
    await msg.reply_text("عايزه إزاي؟", reply_markup=keyboard)


async def on_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    mode, _, key = query.data.partition(":")
    url = context.user_data.get(key)
    if not url:
        await query.edit_message_text("انتهت صلاحية الطلب، ابعت الرابط تاني.")
        return

    await query.edit_message_text("⏳ جاري التحميل...")
    outdir = tempfile.mkdtemp(prefix="tt_")
    chat_id = query.message.chat_id

    try:
        path, info = await asyncio.to_thread(download_sync, url, mode, outdir)

        if path is None or path.stat().st_size > MAX_SIZE_MB * 1024 * 1024:
            await query.edit_message_text(
                f"❌ الملف أكبر من {MAX_SIZE_MB}MB أو مقدرتش أحمله. جرّب الصوت بس."
            )
            return

        await query.edit_message_text("📤 جاري الرفع...")
        caption = ((info or {}).get("title") or "")[:200]

        with open(path, "rb") as f:
            if mode == "audio":
                await context.bot.send_audio(
                    chat_id, audio=f, title=caption,
                    read_timeout=180, write_timeout=180,
                )
            else:
                await context.bot.send_video(
                    chat_id, video=f, caption=caption,
                    supports_streaming=True,
                    read_timeout=180, write_timeout=180,
                )
        await query.delete_message()

    except yt_dlp.utils.DownloadError as e:
        log.warning("DownloadError: %s", e)
        text = str(e).lower()
        if "private" in text:
            hint = "الفيديو خاص."
        elif "unavailable" in text or "removed" in text or "not available" in text:
            hint = "الفيديو اتحذف أو مش متاح في بلد السيرفر."
        elif "unsupported url" in text or "no video" in text:
            hint = "ممكن يكون البوست صور (slideshow) وده مش مدعوم."
        elif "ip address is blocked" in text or "403" in text or "blocked" in text:
            hint = "تيك توك حجب السيرفر. ضيف cookies.txt أو استخدم IP تاني."
        else:
            hint = "حدّث yt-dlp (pip install -U yt-dlp) وجرّب تاني."
        await query.edit_message_text(f"❌ فشل التحميل.\\n{hint}")

    except Exception:
        log.exception("Unexpected error")
        await query.edit_message_text("❌ حصل خطأ غير متوقع، حاول تاني.")

    finally:
        shutil.rmtree(outdir, ignore_errors=True)
        context.user_data.pop(key, None)


def main():
    if not BOT_TOKEN:
        raise SystemExit("حط توكن البوت في المتغير BOT_TOKEN")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(on_choice, pattern=r"^(video|audio):"))

    log.info("TikTok bot is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
