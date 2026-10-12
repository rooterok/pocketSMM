"""Telegram-часть: согласие → меню → оплата → челлендж в боте (задания, сдача, ответы кураторов). /info как в OMGbot."""
import html
from pathlib import Path

import aiohttp
from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton as B, InlineKeyboardMarkup, Message

import texts
from config import Config
from db import DB
from challenge import TOTAL_DAYS, send_task
from payments import check_order, grant_access
from robokassa import payment_url


def kb(*rows: list[B]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=list(rows))


DOCS_DIR = Path(__file__).parent / "docs"


def doc_chunks(name: str, limit: int = 3800) -> list[str]:
    """docs/<name>.md → куски HTML для Telegram (заголовки жирным, ≤ limit символов)."""
    lines = []
    for line in (DOCS_DIR / f"{name}.md").read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            lines.append(f"<b>{html.escape(line.lstrip('# ').strip())}</b>")
        else:
            lines.append(html.escape(line))
    chunks, cur = [], ""
    for para in "\n".join(lines).split("\n\n"):
        if cur and len(cur) + len(para) + 2 > limit:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        chunks.append(cur)
    return chunks


def build_router(cfg: Config, db: DB, http: aiohttp.ClientSession) -> Router:
    r = Router()

    def doc_btn(title: str, name: str, url: str) -> B:
        return B(text=title, url=url) if url else B(text=title, callback_data=f"doc:{name}")

    privacy_btn = doc_btn("📄 Политика", "privacy", cfg.privacy_url)
    offer_btn = doc_btn("📑 Оферта", "offer", cfg.offer_url)

    consent_kb = kb([privacy_btn, offer_btn],
                    [B(text="🚀 Продолжить", callback_data="consent")])
    menu_kb = kb([B(text="🎁 Бесплатный чек-лист", callback_data="checklist")],
                 [B(text="🚀 О челлендже и цена", callback_data="product")],
                 [B(text="ℹ️ Контакты, реквизиты, возврат", callback_data="info:menu")])
    info_kb = kb([B(text="Контакты", callback_data="info:contacts")],
                 [B(text="Реквизиты", callback_data="info:requisites")],
                 [B(text="Оплата и возврат", callback_data="info:payment")],
                 [offer_btn, privacy_btn],
                 [B(text="Закрыть", callback_data="info:close")])
    back_kb = kb([B(text="← Назад", callback_data="info:menu")])

    @r.message(CommandStart())
    async def start(m: Message):
        await db.upsert_user(m.from_user.id, m.from_user.username, m.from_user.first_name)
        user = await db.get_user(m.from_user.id)
        if not user["consented_at"]:
            await m.answer(texts.consent(cfg), reply_markup=consent_kb)
            return
        await m.answer(texts.MENU, reply_markup=menu_kb)

    def paid_status(user) -> str:
        day = min(user["challenge_day"], TOTAL_DAYS)
        return f"Ты уже в челлендже ✅ Сейчас день {day} из {TOTAL_DAYS}. Повторить задание: /task"

    @r.callback_query(F.data == "consent")
    async def consent(c: CallbackQuery):
        await db.upsert_user(c.from_user.id, c.from_user.username, c.from_user.first_name)
        await db.set_consented(c.from_user.id)
        await c.message.edit_reply_markup(reply_markup=None)
        await c.message.answer(texts.MENU, reply_markup=menu_kb)
        await c.answer()

    @r.callback_query(F.data == "checklist")
    async def checklist(c: CallbackQuery):
        await c.message.answer(texts.CHECKLIST, parse_mode="HTML",
                               reply_markup=kb([B(text="🚀 Хочу в челлендж", callback_data="product")]))
        await c.answer()

    @r.callback_query(F.data == "product")
    async def product(c: CallbackQuery):
        await c.message.answer(texts.product(cfg), parse_mode="HTML", reply_markup=kb(
            [B(text=f"Записаться за {texts.price_rub(cfg)}", callback_data="buy")],
            [B(text="ℹ️ Оплата и возврат", callback_data="info:payment_new")]))
        await c.answer()

    @r.callback_query(F.data == "buy")
    async def buy(c: CallbackQuery):
        user = await db.get_user(c.from_user.id)
        if user and user["paid_at"]:
            await c.message.answer(paid_status(user))
            return await c.answer()
        if not user or not user["consented_at"]:
            await c.message.answer(texts.consent(cfg), reply_markup=consent_kb)
            return await c.answer()
        inv_id = await db.create_order(c.from_user.id, cfg.price, cfg.robokassa_is_test)
        url = payment_url(cfg.robokassa_login, cfg.robokassa_password1, cfg.price, inv_id,
                          cfg.product_name, cfg.robokassa_is_test)
        await c.message.answer(texts.pay(cfg), parse_mode="HTML", disable_web_page_preview=True, reply_markup=kb(
            [B(text=f"💳 Оплатить {texts.price_rub(cfg)}", url=url)],
            [B(text="Проверить оплату", callback_data="pay:check")]))
        await c.answer()

    @r.callback_query(F.data == "pay:check")
    async def pay_check(c: CallbackQuery, bot: Bot):
        user = await db.get_user(c.from_user.id)
        if user and user["paid_at"]:
            await c.answer("Оплата уже подтверждена")
            await c.message.answer(paid_status(user))
            return
        order = await db.latest_pending(c.from_user.id, cfg.robokassa_is_test)
        if not order:
            return await c.answer("Счёт не найден. Нажми «Записаться» ещё раз.", show_alert=True)
        try:
            is_paid, err = await check_order(http, cfg, db, order["inv_id"])
        except Exception:
            return await c.answer("Не получилось проверить оплату, попробуй через минуту", show_alert=True)
        if err:
            return await c.answer(f"Оплата пока не найдена ({err})", show_alert=True)
        if not is_paid:
            return await c.answer("Оплата пока не найдена. Если только что оплатил — подожди минуту.",
                                  show_alert=True)
        await c.answer("Оплата подтверждена")
        await grant_access(bot, cfg, db, order["inv_id"], c.from_user.id)

    # --- /info: то, что Робокасса проверяет у магазина в Telegram ---
    @r.message(Command("info"))
    async def info(m: Message):
        await m.answer("Выбери раздел:", reply_markup=info_kb)

    @r.callback_query(F.data == "info:menu")
    async def info_menu(c: CallbackQuery):
        # «Назад» из раздела — редактируем то же сообщение; из главного меню — новое сообщение
        if (c.message.text or "").startswith(("КОНТАКТЫ", "РЕКВИЗИТЫ", "ОПЛАТА")):
            await c.message.edit_text("Выбери раздел:", reply_markup=info_kb)
        else:
            await c.message.answer("Выбери раздел:", reply_markup=info_kb)
        await c.answer()

    sections = {"info:contacts": texts.contacts, "info:requisites": texts.requisites,
                "info:payment": texts.payment_refund}

    @r.callback_query(F.data.in_(sections))
    async def info_section(c: CallbackQuery):
        await c.message.edit_text(sections[c.data](cfg), reply_markup=back_kb)
        await c.answer()

    @r.callback_query(F.data == "info:payment_new")
    async def info_payment_new(c: CallbackQuery):
        await c.message.answer(texts.payment_refund(cfg))
        await c.answer()

    @r.callback_query(F.data == "info:close")
    async def info_close(c: CallbackQuery):
        await c.message.delete()
        await c.answer()

    async def send_doc(message: Message, name: str, url: str):
        if url:
            return await message.answer(url)
        for part in doc_chunks(name):
            await message.answer(part, parse_mode="HTML")

    @r.callback_query(F.data.startswith("doc:"))
    async def doc_cb(c: CallbackQuery):
        name = c.data.split(":", 1)[1]
        await send_doc(c.message, name, cfg.offer_url if name == "offer" else cfg.privacy_url)
        await c.answer()

    @r.message(Command("offer"))
    async def offer_cmd(m: Message):
        await send_doc(m, "offer", cfg.offer_url)

    @r.message(Command("privacy"))
    async def privacy_cmd(m: Message):
        await send_doc(m, "privacy", cfg.privacy_url)

    @r.message(Command("help"))
    async def help_cmd(m: Message):
        await m.answer(f"Вопросы по оплате, доступу и возврату: {cfg.contact_telegram} или {cfg.contact_email}")

    @r.message(Command("stats"), F.from_user.id.in_(set(cfg.admin_ids)))
    async def stats(m: Message):
        s = await db.stats()
        await m.answer(f"Пользователей: {s['users']}\nПриняли условия: {s['consented']}\n"
                       f"Оплатили: {s['paid']}\nПрошли все 7 дней: {s['finished']}")

    # --- челлендж: сдача заданий, вопросы, ответы кураторов ---
    admins = set(cfg.admin_ids)
    done_kb = kb([B(text="✅ Готово", callback_data="hw:done")])

    @r.message(Command("task"))
    async def task_cmd(m: Message, bot: Bot):
        user = await db.get_user(m.from_user.id)
        if not user or not user["paid_at"] or user["challenge_day"] == 0:
            return await m.answer(texts.NOT_PAID_HINT)
        await send_task(bot, m.from_user.id, min(user["challenge_day"], TOTAL_DAYS))

    @r.callback_query(F.data.startswith("hw:"))
    async def hw(c: CallbackQuery):
        if c.data == "hw:done":
            await db.set_awaiting(c.from_user.id, None)
            await c.message.edit_reply_markup(reply_markup=None)
            await c.message.answer(texts.HW_DONE)
            return await c.answer()
        day = int(c.data.split(":")[1])
        await db.set_awaiting(c.from_user.id, day)
        await c.message.answer(texts.HW_ASK, reply_markup=done_kb)
        await c.answer()

    async def relay_to_admins(m: Message, bot: Bot, header: str, day: int | None):
        for admin in admins:
            try:
                h = await bot.send_message(admin, header)
                copied = await m.copy_to(admin)
                await db.add_relay(admin, h.message_id, m.from_user.id, day)
                await db.add_relay(admin, copied.message_id, m.from_user.id, day)
            except Exception:
                pass

    def who(m: Message) -> str:
        u = m.from_user
        tag = f"@{u.username}" if u.username else f"id {u.id}"
        return f"{u.full_name} ({tag})"

    @r.message(F.from_user.id.in_(admins), F.reply_to_message)
    async def admin_reply(m: Message, bot: Bot):
        link = await db.get_relay(m.chat.id, m.reply_to_message.message_id)
        if not link:
            return await m.answer("Не понял, кому ответ. Отвечай реплаем на сообщение участника.")
        try:
            await bot.send_message(link["user_id"], texts.REPLY_HEADER)
            await m.copy_to(link["user_id"])
            await m.answer("Отправлено участнику ✅")
        except Exception as e:
            await m.answer(f"Не удалось отправить: {e}")

    @r.message(Command("fileid"), F.from_user.id.in_(admins))
    async def file_id(m: Message):
        src = m.reply_to_message
        media = src and (src.video or src.document or src.video_note or src.animation)
        if not media:
            return await m.answer("Отправь видео боту, потом ответь на него командой /fileid")
        await m.answer(f"<code>{media.file_id}</code>", parse_mode="HTML")

    @r.message(~F.text.startswith("/"))
    async def from_participant(m: Message, bot: Bot):
        user = await db.get_user(m.from_user.id)
        if not user or not user["paid_at"]:
            return await m.answer(texts.NOT_PAID_HINT)
        day = user["awaiting_hw_day"]
        if day:
            await relay_to_admins(m, bot, f"📝 Задание дня {day} — {who(m)}\nОтветь реплаем, чтобы ответить участнику", day)
            await m.answer(texts.HW_GOT, reply_markup=done_kb)
        else:
            await relay_to_admins(m, bot, f"❓ Вопрос — {who(m)} (день {user['challenge_day']})\nОтветь реплаем", None)
            await m.answer(texts.QUESTION_GOT)

    return r


async def setup_profile(bot: Bot, cfg: Config) -> None:
    """Описание бота с ценой и реквизитами — первое, что видит модератор Робокассы."""
    try:
        await bot.set_my_commands([BotCommand(command="start", description="Начать"),
                                   BotCommand(command="task", description="Текущее задание челленджа"),
                                   BotCommand(command="info", description="Контакты, реквизиты, оплата и возврат"),
                                   BotCommand(command="offer", description="Договор-оферта"),
                                   BotCommand(command="privacy", description="Политика персональных данных"),
                                   BotCommand(command="help", description="Связаться с поддержкой")])
        await bot.set_my_short_description(
            f"{cfg.product_name} — {texts.price_rub(cfg)}. {cfg.seller_status} {cfg.seller_name}, ИНН {cfg.seller_inn}"[:120])
        await bot.set_my_description(
            (f"{cfg.product_name}. Стоимость {texts.price_rub(cfg)}. Старт сразу после оплаты, задания приходят в этот бот.\n\n"
             f"{cfg.seller_status} {cfg.seller_name}, ИНН {cfg.seller_inn}, {cfg.seller_city}.\n"
             f"Связь: {cfg.contact_telegram}, {cfg.contact_email}\n"
             f"Оферта: {cfg.offer_url or '/offer'}")[:512])
    except Exception:
        pass
