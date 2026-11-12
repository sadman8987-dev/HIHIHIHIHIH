from __future__ import annotations

import asyncio
import logging
import shutil

from aiogram.types import BotCommand

from .bot import build_dispatcher, create_bot
from .config import Settings
from .database import Database


async def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    db = Database(str(settings.data_dir / "bot.sqlite3"))
    await db.open()
    bot = create_bot(settings.bot_token)
    dispatcher = build_dispatcher(settings, db)

    try:
        await bot.set_my_commands([
            BotCommand(command="start", description="ابدأ استخدام البوت"),
            BotCommand(command="help", description="طريقة الاستخدام"),
            BotCommand(command="audio", description="تنزيل الصوت بصيغة MP3"),
            BotCommand(command="queue", description="حالة التنزيلات"),
            BotCommand(command="cancel", description="إلغاء الاختيارات المعلّقة"),
            BotCommand(command="about", description="الخصوصية والحدود"),
        ])
        logging.getLogger(__name__).info("Starting Telegram long polling")
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
            close_bot_session=False,
        )
    finally:
        await bot.session.close()
        await db.close()
        for entry in settings.temp_dir.glob("job-*"):
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
