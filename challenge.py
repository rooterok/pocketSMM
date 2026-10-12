"""Выдача дней челленджа: старт сразу после оплаты, дальше раз в DAY_INTERVAL (по умолчанию сутки)."""
import asyncio
import logging
import time

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import content
from config import Config
from db import DB

log = logging.getLogger(__name__)
TOTAL_DAYS = len(content.DAYS)


def hw_kb(day: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Сдать задание", callback_data=f"hw:{day}")]])


async def send_task(bot: Bot, user_id: int, day: int) -> None:
    """Отправить задание дня day (1..7) — без изменения прогресса."""
    d = content.DAYS[day - 1]
    if d.get("video"):
        await bot.send_video(user_id, d["video"])
    await bot.send_message(user_id, f"<b>{d['title']}</b>\n\n{d['text']}", parse_mode="HTML",
                           reply_markup=hw_kb(day), disable_web_page_preview=True)


async def send_next_day(bot: Bot, cfg: Config, db: DB, user_id: int) -> None:
    """Шаги 1..7 — задания, шаг 8 — финальное сообщение. Всё хранится в БД и переживает перезапуск."""
    user = await db.get_user(user_id)
    step = user["challenge_day"] + 1
    if step > TOTAL_DAYS + 1:
        return
    if step == TOTAL_DAYS + 1:
        await bot.send_message(user_id, content.FINISH, parse_mode="HTML")
        await db.set_day_sent(user_id, step, None)
        return
    if step == 1:
        await bot.send_message(user_id, content.START)
    await send_task(bot, user_id, step)
    await db.set_day_sent(user_id, step, int(time.time()) + cfg.day_interval)


async def start_challenge(bot: Bot, cfg: Config, db: DB, user_id: int) -> None:
    user = await db.get_user(user_id)
    if user and user["challenge_day"] == 0:
        await send_next_day(bot, cfg, db, user_id)


async def challenge_loop(bot: Bot, cfg: Config, db: DB, every: int = 30) -> None:
    while True:
        try:
            for user in await db.due_users(int(time.time()), TOTAL_DAYS + 1):
                try:
                    await send_next_day(bot, cfg, db, user["user_id"])
                except Exception:
                    log.exception("Не удалось отправить день пользователю %s", user["user_id"])
                    await db.set_day_sent(user["user_id"], user["challenge_day"], int(time.time()) + 600)
        except Exception:
            log.exception("challenge_loop")
        await asyncio.sleep(every)
