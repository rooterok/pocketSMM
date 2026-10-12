"""Настройки бота. Всё из переменных окружения (.env локально, Variables на Railway)."""
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str | None = None, required: bool = True) -> str:
    value = (os.getenv(name, default) or "").strip().strip('"').strip("'")
    if required and not value:
        raise RuntimeError(f"Не задана переменная окружения {name}")
    return value


@dataclass(frozen=True)
class Config:
    # Telegram
    bot_token: str
    admin_ids: tuple[int, ...]      # кураторы: получают оплаты, задания и вопросы
    day_interval: int               # секунд между днями челленджа (86400 = сутки)

    # Robokassa — магазин челленджа
    robokassa_login: str
    robokassa_password1: str
    robokassa_password2: str
    robokassa_is_test: bool

    # Продукт
    product_name: str
    price: str                      # "5000.00"

    # Документы: внешние ссылки (Telegraph и т.п.). Если пусто — бот показывает текст из docs/ сам.
    offer_url: str
    privacy_url: str

    # Реквизиты и контакты продавца
    seller_status: str              # «Самозанятый» / «Самозанятая»
    seller_name: str                # ФИО полностью
    seller_inn: str
    seller_city: str
    contact_email: str
    contact_phone: str
    contact_telegram: str

    db_path: str


def load_config() -> Config:
    return Config(
        bot_token=_env("BOT_TOKEN"),
        admin_ids=tuple(int(x) for x in _env("ADMIN_IDS", "", required=False).strip("()[] ").split(",")
                        if x.strip()),
        day_interval=int(_env("DAY_INTERVAL", "86400", required=False)),
        robokassa_login=_env("ROBOKASSA_LOGIN"),
        robokassa_password1=_env("ROBOKASSA_PASSWORD1"),
        robokassa_password2=_env("ROBOKASSA_PASSWORD2"),
        robokassa_is_test=_env("ROBOKASSA_IS_TEST", "0", required=False) in {"1", "true", "True"},
        product_name=_env("PRODUCT_NAME", "Челлендж «Продающие ролики через нейросеть»", required=False),
        price=_env("PAYMENT_AMOUNT", "5000.00", required=False),
        offer_url=_env("OFFER_URL", "", required=False),
        privacy_url=_env("PRIVACY_URL", "", required=False),
        seller_status=_env("SELLER_STATUS", "Самозанятый", required=False),
        seller_name=_env("SELLER_NAME"),
        seller_inn=_env("SELLER_INN"),
        seller_city=_env("SELLER_CITY", "Российская Федерация", required=False),
        contact_email=_env("CONTACT_EMAIL"),
        contact_phone=_env("CONTACT_PHONE", "", required=False),
        contact_telegram=_env("CONTACT_TELEGRAM", "@ohm_ali", required=False),
        db_path=_env("DB_PATH", "data.sqlite3", required=False),
    )
