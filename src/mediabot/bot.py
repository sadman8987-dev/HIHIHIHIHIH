from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from dataclasses import dataclass

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, FSInputFile, Message

from .config import Settings
from .database import Database
from .downloader import DownloadError, cleanup_media, download_media, find_url, inspect_media
from .keyboards import quality_keyboard
from .splitter import split_media

log = logging.getLogger(__name__)
router = Router()


@dataclass(slots=True)
class Pending:
    user_id: int
    url: str
    title: str
    created_at: float


pending: dict[str, Pending] = {}
last_request: dict[int, float] = {}


def create_bot(token: str) -> Bot:
    return Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))


def build_dispatcher(settings: Settings, db: Database) -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(router)
    dp["settings"] = settings
    dp["db"] = db
    dp["semaphore"] = asyncio.Semaphore(settings.max_concurrent_downloads)
    return dp


@router.message(CommandStart())
async def start(message: Message, db: Database) -> None:
    if message.from_user:
        await db.touch_user(message.from_user.id)
    await message.answer(
        "أهلًا بك في <b>Media Fetch</b>\n\n"
        "أرسل رابط فيديو عام، وسأعرض الجودات المتاحة. أو استخدم <code>/audio رابط</code> للصوت.\n\n"
        "<b>الأوامر</b>\n"
        "/help — طريقة الاستخدام\n"
        "/audio رابط — تنزيل MP3 مباشرة\n"
        "/queue — حالة طابور التنزيل\n"
        "/cancel — إلغاء اختياراتك المعلّقة\n"
        "/about — الخصوصية والحدود\n\n"
        "نزّل فقط المحتوى الذي تملكه أو لديك إذن باستخدامه."
    )


@router.message(Command("help"))
async def help_command(message: Message) -> None:
    await message.answer(
        "أرسل رابطًا مباشرًا لمحتوى عام، ثم اختر الجودة أو <b>صوت MP3</b>.\n"
        "استخدم <code>/audio URL</code> للصوت مباشرة. الروابط الخاصة أو المحمية بتسجيل دخول غير مدعومة.\n"
        "قد تختلف الصيغ المتاحة حسب الموقع والوسائط."
    )


@router.message(Command("about"))
async def about(message: Message, settings: Settings) -> None:
    await message.answer(
        "<b>Media Fetch</b> يستخدم yt-dlp وFFmpeg لمعالجة الروابط العامة التي تدعمها المكتبتان.\n"
        f"حجم الجزء الأقصى: {settings.max_file_mb} MB، وحد المصدر الكامل: {settings.max_source_file_mb} MB، "
        f"وأقصى مدة: {settings.max_duration_seconds // 60} دقيقة.\n"
        "إذا تجاوز الفيديو 60 دقيقة أو تجاوز الملف حد الحجم، يقسمه FFmpeg ويرسل الأجزاء مرقّمة.\n"
        "حد الرفع الرسمي يقارب 50 MB؛ لذلك نترك هامشًا آمنًا.\n"
        "لا يتجاوز البوت تسجيل الدخول أو DRM أو القيود المدفوعة. لا ترسل روابط خاصة أو بيانات دخول. "
        "تُحذف الملفات المؤقتة بعد الإرسال، ولا تُحفظ روابط الوسائط في قاعدة البيانات."
    )


@router.message(Command("queue"))
async def queue_command(message: Message, semaphore: asyncio.Semaphore, settings: Settings) -> None:
    state = "مشغول" if semaphore.locked() else "متاح"
    await message.answer(
        f"حالة التنزيلات: <b>{state}</b>\n"
        f"الحد الأقصى للتنزيلات المتزامنة: {settings.max_concurrent_downloads}. "
        "قد ينتظر طلبك حتى يتوفر مكان."
    )


@router.message(Command("cancel"))
async def cancel_command(message: Message) -> None:
    if not message.from_user:
        return
    keys = [key for key, item in pending.items() if item.user_id == message.from_user.id]
    for key in keys:
        pending.pop(key, None)
    await message.answer("تم إلغاء الاختيارات المعلّقة." if keys else "لا توجد اختيارات معلّقة لإلغائها.")


@router.message(Command("stats"))
async def stats_command(message: Message, settings: Settings, db: Database) -> None:
    if not message.from_user or message.from_user.id not in settings.owner_ids:
        return
    users, total, today = await db.stats()
    await message.answer(f"<b>إحصاءات البوت</b>\nالمستخدمون: {users}\nالتنزيلات الكلية: {total}\nتنزيلات اليوم: {today}")


@router.message(Command("audio"))
async def audio_command(message: Message, command: CommandObject, settings: Settings, db: Database, semaphore: asyncio.Semaphore) -> None:
    url = find_url(command.args or "")
    if not url:
        await message.answer("استخدم الأمر هكذا: <code>/audio https://example.com/media</code>")
        return
    await begin_download(message, url, "audio", None, settings, db, semaphore)


@router.callback_query(F.data.startswith("cancel:"))
async def cancel_callback(callback: CallbackQuery) -> None:
    key = (callback.data or "").split(":", 1)[1]
    item = pending.get(key)
    if item and callback.from_user.id == item.user_id:
        pending.pop(key, None)
        if callback.message:
            await callback.message.edit_text("تم إلغاء الطلب.")
    await callback.answer()


@router.callback_query(F.data.startswith("get:"))
async def quality_callback(callback: CallbackQuery, settings: Settings, db: Database, semaphore: asyncio.Semaphore) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    try:
        _, key, mode, raw_height = (callback.data or "").split(":", 3)
        height = int(raw_height) or None
    except (ValueError, TypeError):
        await callback.answer("الخيار غير صالح", show_alert=True)
        return
    if mode not in {"v", "a"}:
        await callback.answer("الخيار غير صالح", show_alert=True)
        return
    item = pending.get(key)
    if not item or item.user_id != callback.from_user.id:
        await callback.answer("انتهت صلاحية هذا الطلب. أرسل الرابط مرة أخرى.", show_alert=True)
        return
    pending.pop(key, None)
    await callback.answer("بدأ التنزيل")
    selected_mode = "audio" if mode == "a" else "video"
    await begin_download(
        callback.message,
        item.url,
        selected_mode,
        height,
        settings,
        db,
        semaphore,
        skip_cooldown=True,
        actor_user_id=callback.from_user.id,
    )


@router.message(F.text)
async def text_handler(message: Message, settings: Settings, db: Database, semaphore: asyncio.Semaphore) -> None:
    if not message.text or message.text.startswith("/"):
        return
    url = find_url(message.text)
    if not url:
        await message.answer("أرسل رابط فيديو أو استخدم /help لمعرفة طريقة الاستخدام.")
        return
    await prepare_media(message, url, settings, db, semaphore)


async def prepare_media(message: Message, url: str, settings: Settings, db: Database, semaphore: asyncio.Semaphore) -> None:
    user_id = message.from_user.id if message.from_user else 0
    if user_id:
        await db.touch_user(user_id)
        if not await check_cooldown(message, user_id, settings):
            return
    status = await message.answer("أفحص الرابط والجودات المتاحة…")
    try:
        info = await inspect_media(url, settings)
    except DownloadError as exc:
        await status.edit_text(str(exc))
        return
    key = secrets.token_urlsafe(5).replace("-", "a").replace("_", "b")[:8]
    pending[key] = Pending(user_id=user_id, url=url, title=info.title, created_at=time.monotonic())
    expire_pending()
    await status.edit_text(
        f"<b>{_escape(info.title)}</b>\nاختر جودة الفيديو أو تنزيل الصوت:",
        reply_markup=quality_keyboard(key, info.heights),
    )


async def begin_download(
    message: Message,
    url: str,
    mode: str,
    height: int | None,
    settings: Settings,
    db: Database,
    semaphore: asyncio.Semaphore,
    skip_cooldown: bool = False,
    actor_user_id: int | None = None,
) -> None:
    user_id = actor_user_id or (message.from_user.id if message.from_user else 0)
    if user_id:
        await db.touch_user(user_id)
        if not skip_cooldown and not await check_cooldown(message, user_id, settings):
            return
    status = await message.answer(
        "🎧 جارٍ تجهيز الصوت…" if mode == "audio"
        else f"🎬 جارٍ تنزيل الفيديو ({height}p)…" if height
        else "🎬 جارٍ تنزيل الفيديو…"
    )
    media = None
    try:
        async with semaphore:
            media = await download_media(url, mode, height, settings)
            max_bytes = settings.max_file_mb * 1_000_000
            if media.path.stat().st_size > max_bytes:
                await status.edit_text("الملف أكبر من الحد المسموح؛ جارٍ تقسيمه إلى أجزاء…")
            parts = await asyncio.to_thread(split_media, media.path, mode, media.duration, max_bytes, max_duration_seconds=3600)
            total_parts = len(parts)
            stem = _safe_filename(media.title)
            for index, part in enumerate(parts, start=1):
                suffix = f" — الجزء {index}/{total_parts}" if total_parts > 1 else ""
                caption = _escape(media.title) + _escape(suffix)
                filename = f"{stem}_part_{index:02d}{part.suffix}" if total_parts > 1 else f"{stem}{part.suffix}"
                upload = FSInputFile(part, filename=filename)
                if mode == "audio":
                    await message.answer_audio(
                        upload,
                        title=f"{media.title[:52]} ({index}/{total_parts})" if total_parts > 1 else media.title[:64],
                        caption=f"🎧 {caption}",
                    )
                elif part.suffix.lower() == ".mp4":
                    await message.answer_video(upload, caption=f"🎬 {caption}", supports_streaming=True)
                else:
                    await message.answer_document(upload, caption=f"🎬 {caption}")
        if user_id:
            await db.record_download(user_id, mode)
        await status.delete()
    except DownloadError as exc:
        await status.edit_text(str(exc))
    except Exception:
        log.exception("Unexpected upload or download failure")
        await status.edit_text("حدث خطأ أثناء إرسال الملف. جرّب جودة أقل أو رابطًا آخر.")
    finally:
        if media:
            cleanup_media(media.path)


async def check_cooldown(message: Message, user_id: int, settings: Settings) -> bool:
    now = time.monotonic()
    previous = last_request.get(user_id, 0)
    remaining = settings.user_cooldown_seconds - (now - previous)
    if remaining > 0:
        await message.answer(f"انتظر {remaining:.0f} ثوانٍ قبل طلب جديد.")
        return False
    last_request[user_id] = now
    return True


def expire_pending() -> None:
    now = time.monotonic()
    for key, item in list(pending.items()):
        if now - item.created_at > 900:
            pending.pop(key, None)


def _escape(value: str) -> str:
    return re.sub(r"[&<>]", lambda match: {"&": "&amp;", "<": "&lt;", ">": "&gt;"}[match.group()], value)


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "_", value, flags=re.UNICODE).strip("._")
    return cleaned[:60] or "media"
