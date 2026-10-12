"""Точка входа: бот (polling) + фоновая проверка оплат + выдача дней челленджа. Без веб-сервера, как OMGbot."""
import asyncio
import logging

import aiohttp
from aiogram import Bot, Dispatcher

from bot import build_router, setup_profile
from config import load_config
from db import DB
from challenge import challenge_loop
from payments import poll_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def main():
    cfg = load_config()
    db = DB(cfg.db_path)
    await db.init()

    bot = Bot(cfg.bot_token)
    await setup_profile(bot, cfg)

    async with aiohttp.ClientSession() as http:
        dp = Dispatcher()
        dp.include_router(build_router(cfg, db, http))
        poller = asyncio.create_task(poll_loop(bot, cfg, db, http))
        days = asyncio.create_task(challenge_loop(bot, cfg, db))
        logging.info("Bot started, test mode=%s", cfg.robokassa_is_test)
        try:
            await dp.start_polling(bot)
        finally:
            poller.cancel()
            days.cancel()
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
