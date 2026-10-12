"""Подтверждение оплаты без ResultURL: бот сам опрашивает Robokassa раз в минуту
(и по кнопке «Проверить оплату») и выдаёт доступ."""
import asyncio
import logging

import aiohttp
from aiogram import Bot

import texts
from challenge import start_challenge
from config import Config
from db import DB
from robokassa import PAID_STATE, op_state

log = logging.getLogger(__name__)


async def check_order(http: aiohttp.ClientSession, cfg: Config, db: DB, inv_id: int) -> tuple[bool, str | None]:
    """(оплачен?, текст ошибки Робокассы если запрос не удался)"""
    await db.mark_checked(inv_id)
    code, state, desc = await op_state(http, cfg.robokassa_login, cfg.robokassa_password2, inv_id,
                                       cfg.robokassa_is_test)
    if code != 0:
        return False, desc or f"код {code}"
    return state == PAID_STATE, None


async def grant_access(bot: Bot, cfg: Config, db: DB, inv_id: int, user_id: int) -> None:
    """Подтверждает оплату и сразу запускает челлендж. Повторно по тому же счёту не срабатывает."""
    if not await db.mark_paid(inv_id, user_id):
        return
    await bot.send_message(user_id, texts.PAID)
    await start_challenge(bot, cfg, db, user_id)
    for admin in cfg.admin_ids:
        try:
            await bot.send_message(admin, f"💰 Оплата {texts.price_rub(cfg)}: счёт {inv_id}, user {user_id}")
        except Exception:
            pass


async def poll_loop(bot: Bot, cfg: Config, db: DB, http: aiohttp.ClientSession, every: int = 30) -> None:
    while True:
        try:
            for order in await db.orders_to_check(cfg.robokassa_is_test):
                try:
                    is_paid, _ = await check_order(http, cfg, db, order["inv_id"])
                    if is_paid:
                        await grant_access(bot, cfg, db, order["inv_id"], order["user_id"])
                except Exception:
                    log.exception("Проверка счёта %s", order["inv_id"])
        except Exception:
            log.exception("poll_loop")
        await asyncio.sleep(every)
