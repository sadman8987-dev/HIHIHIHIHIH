#!/usr/bin/env python3
"""
بوت تليجرام يقسّم أي صوت/فيديو لأجزاء (بالمدة و/أو الحجم) ويرجّعها لك.
بيستخدم Telethon (MTProto) فيتخطى حدود الـ Bot API: تنزيل/رفع لحد ~2GB.

متغيرات البيئة:
  API_ID, API_HASH     من https://my.telegram.org
  BOT_TOKEN            من @BotFather
  ALLOWED_USERS        (اختياري) ايديهات مفصولة بفاصلة، فاضي = الكل
  MAX_MINUTES          (اختياري) افتراضي 55
  MAX_MB               (اختياري) افتراضي 1900
  MAX_JOBS             (اختياري) عدد المهام المتزامنة، افتراضي 2
  WORKDIR              (اختياري) مجلد مؤقت
"""
import asyncio
import json
import logging
import math
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from telethon import TelegramClient, events

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("splitbot")

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
ALLOWED = {int(x) for x in os.getenv("ALLOWED_USERS", "").split(",") if x.strip()}
DEF_MIN = float(os.getenv("MAX_MINUTES", "55"))
DEF_MB = float(os.getenv("MAX_MB", "1900"))
MAX_JOBS = int(os.getenv("MAX_JOBS", "2"))
WORKDIR = Path(os.getenv("WORKDIR", tempfile.gettempdir())) / "splitbot"

TG_LIMIT_MB = 1950  # الحد الفعلي لتليجرام ~2GB، نسيب هامش
settings: dict[int, dict] = {}
sem = asyncio.Semaphore(MAX_JOBS)
client = TelegramClient("splitbot", API_ID, API_HASH)


# ---------- أدوات مساعدة ----------
def user_cfg(uid: int) -> dict:
    return settings.setdefault(uid, {"min": DEF_MIN, "mb": min(DEF_MB, TG_LIMIT_MB)})


def make_progress(msg, label: str):
    last = [0.0]

    async def cb(cur, total):
        now = time.time()
        if now - last[0] < 4 and cur != total:
            return
        last[0] = now
        try:
            await msg.edit(f"{label}: {cur * 100 // max(total, 1)}% "
                           f"({cur / 1048576:.0f}/{total / 1048576:.0f} MB)")
        except Exception:
            pass

    return cb


async def run(*cmd) -> tuple[int, str, str]:
    p = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await p.communicate()
    return p.returncode, out.decode(errors="ignore"), err.decode(errors="ignore")


async def probe_duration(path: str) -> float:
    code, out, err = await run("ffprobe", "-v", "error", "-print_format", "json",
                               "-show_format", path)
    if code != 0:
        raise RuntimeError("الملف مش صوت/فيديو صالح (ffprobe فشل)")
    return float(json.loads(out)["format"]["duration"])


async def split(src: Path, duration: float, max_min: float, max_mb: float,
                outdir: Path) -> list[Path]:
    size = src.stat().st_size
    seg = max_min * 60
    by_size = (max_mb * 1048576 * 0.93) / (size / duration)  # هامش أمان 7%
    seg = max(1, math.floor(min(seg, by_size)))
    ext = src.suffix or ".mkv"
    pattern = str(outdir / f"part%03d{ext}")
    code, _, err = await run(
        "ffmpeg", "-y", "-i", str(src),
        "-map", "0:v?", "-map", "0:a?",
        "-c", "copy", "-f", "segment", "-segment_time", str(seg),
        "-reset_timestamps", "1", pattern)
    if code != 0:
        raise RuntimeError(f"ffmpeg فشل:\n{err[-300:]}")
    return sorted(outdir.glob(f"part*{ext}"))


# ---------- الأوامر ----------
def allowed(e) -> bool:
    return not ALLOWED or e.sender_id in ALLOWED


@client.on(events.NewMessage(pattern=r"^/(start|help)"))
async def start(e):
    if not allowed(e):
        return
    cfg = user_cfg(e.sender_id)
    await e.reply(
        "ابعتلي أي فيديو/صوت (لحد 2GB) وأنا أقسّمه وأرجّعه أجزاء.\n\n"
        f"الإعدادات الحالية: {cfg['min']:g} دقيقة / {cfg['mb']:g} MB للجزء\n"
        "غيّرها بـ:  /set الدقائق الميجابايت\nمثال:  /set 50 400")


@client.on(events.NewMessage(pattern=r"^/set\b"))
async def set_cmd(e):
    if not allowed(e):
        return
    m = re.match(r"^/set\s+(\d+(?:\.\d+)?)(?:\s+(\d+(?:\.\d+)?))?\s*$", e.raw_text)
    if not m:
        return await e.reply("الصيغة:  /set 50 400  (دقائق ثم ميجابايت)")
    cfg = user_cfg(e.sender_id)
    cfg["min"] = min(max(float(m.group(1)), 1), 600)
    if m.group(2):
        cfg["mb"] = min(max(float(m.group(2)), 5), TG_LIMIT_MB)
    await e.reply(f"تم ✅ {cfg['min']:g} دقيقة / {cfg['mb']:g} MB للجزء")


# ---------- المعالجة ----------
@client.on(events.NewMessage(func=lambda e: e.is_private and e.file and not e.photo))
async def handle_media(e):
    if not allowed(e):
        return
    cfg = user_cfg(e.sender_id)
    if e.file.size and e.file.size > 2000 * 1048576:
        return await e.reply("الملف أكبر من 2GB، تليجرام مش بيسمح.")

    status = await e.reply("⏳ في الطابور...")
    async with sem:
        job = WORKDIR / f"{e.sender_id}_{e.id}"
        parts_dir = job / "parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        try:
            src = await e.download_media(
                file=str(job / "input"),
                progress_callback=make_progress(status, "⬇️ تنزيل"))
            src = Path(src)

            await status.edit("🔍 بحلل الملف...")
            duration = await probe_duration(str(src))
            size_mb = src.stat().st_size / 1048576
            if duration <= cfg["min"] * 60 and size_mb <= cfg["mb"]:
                return await status.edit(
                    f"الملف أصلاً داخل الحد ({duration / 60:.1f} دقيقة، "
                    f"{size_mb:.0f} MB)، مفيش داعي للتقسيم.")

            await status.edit(f"✂️ بقسّم ({duration / 60:.1f} دقيقة، {size_mb:.0f} MB)...")
            parts = await split(src, duration, cfg["min"], cfg["mb"], parts_dir)
            if not parts:
                raise RuntimeError("التقسيم ما طلّعش أجزاء")
            src.unlink(missing_ok=True)  # وفّر مساحة قبل الرفع

            n = len(parts)
            for i, p in enumerate(parts, 1):
                await client.send_file(
                    e.chat_id, str(p), force_document=True,
                    caption=f"جزء {i}/{n}", reply_to=e.id,
                    progress_callback=make_progress(status, f"⬆️ رفع {i}/{n}"))
            await status.edit(f"✅ خلصت — {n} أجزاء.")
        except Exception as ex:
            log.exception("job failed")
            try:
                await status.edit(f"❌ حصل خطأ: {ex}")
            except Exception:
                pass
        finally:
            shutil.rmtree(job, ignore_errors=True)


async def main():
    shutil.rmtree(WORKDIR, ignore_errors=True)
    WORKDIR.mkdir(parents=True, exist_ok=True)
    await client.start(bot_token=BOT_TOKEN)
    log.info("Bot is running")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
