from __future__ import annotations

import asyncio
import logging
import os
import secrets
import shutil

from aiohttp import web
from aiogram import Dispatcher
from aiogram.types import BotCommand, Update

from .bot import build_dispatcher, create_bot
from .config import Settings
from .database import Database

log = logging.getLogger(__name__)


async def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    base_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    port = int(os.getenv("PORT", "10000"))
    secret = os.getenv("WEBHOOK_SECRET", "").strip()
    if not base_url or not secret:
        raise ValueError("RENDER_EXTERNAL_URL and WEBHOOK_SECRET are required for webhook mode")

    db = Database(str(settings.data_dir / "bot.sqlite3"))
    await db.open()
    bot = create_bot(settings.bot_token)
    dispatcher: Dispatcher = build_dispatcher(settings, db)
    webhook_path = f"/telegram/webhook/{secret}"
    webhook_url = f"{base_url}{webhook_path}"
    background_tasks: set[asyncio.Task] = set()

    async def health(_: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def telegram_update(request: web.Request) -> web.Response:
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not secrets.compare_digest(supplied, secret):
            raise web.HTTPUnauthorized()
        try:
            payload = await request.json()
            update = Update.model_validate(payload, context={"bot": bot})
        except Exception:
            log.exception("Invalid Telegram update payload")
            raise web.HTTPBadRequest()

        task = asyncio.create_task(dispatcher.feed_update(bot, update))
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)
        task.add_done_callback(_log_task_error)
        return web.json_response({"ok": True})

    app = web.Application(client_max_size=1024 * 1024)
    app.router.add_get("/health", health)
    app.router.add_post(webhook_path, telegram_update)

    async def on_startup(_: web.Application) -> None:
        await bot.set_my_commands([
            BotCommand(command="start", description="ابدأ استخدام البوت"),
            BotCommand(command="help", description="طريقة الاستخدام"),
            BotCommand(command="audio", description="تنزيل الصوت بصيغة MP3"),
            BotCommand(command="queue", description="حالة التنزيلات"),
            BotCommand(command="cancel", description="إلغاء الاختيارات المعلّقة"),
            BotCommand(command="about", description="الخصوصية والحدود"),
        ])
        await bot.set_webhook(
            webhook_url,
            secret_token=secret,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
        log.info("Webhook server listening on port %s", port)

    async def on_cleanup(_: web.Application) -> None:
        try:
            await bot.delete_webhook()
            if background_tasks:
                await asyncio.gather(*background_tasks, return_exceptions=True)
        finally:
            await bot.session.close()
            await db.close()
            for entry in settings.temp_dir.glob("job-*"):
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)

    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()


def _log_task_error(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error:
        log.error("Failed to process Telegram update", exc_info=error)


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
