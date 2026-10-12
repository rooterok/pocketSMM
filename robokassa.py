"""Robokassa без сайта и ResultURL — как в OMGbot:
ссылка на оплату + проверка статуса счёта через XML-интерфейс OpStateExt (пароль #2).
Алгоритм хэша в технических настройках магазина: MD5.
"""
import hashlib
from urllib.parse import urlencode
from xml.etree import ElementTree

import aiohttp

PAY_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"
OP_STATE_URL = "https://auth.robokassa.ru/Merchant/WebService/Service.asmx/OpStateExt"
OP_STATE_TEST_URL = "https://auth.robokassa.ru/Merchant/WebService/Service.asmx/OpState"
PAID_STATE = 100  # 100 — платёж успешно проведён


def _md5(value: str) -> str:
    return hashlib.md5(value.encode("utf-8")).hexdigest().upper()


def payment_url(login: str, password1: str, out_sum: str, inv_id: int, description: str, is_test: bool) -> str:
    params = {
        "MerchantLogin": login,
        "OutSum": out_sum,
        "InvId": str(inv_id),
        "Description": description[:100],
        "SignatureValue": _md5(f"{login}:{out_sum}:{inv_id}:{password1}"),
        "Culture": "ru",
    }
    if is_test:
        params["IsTest"] = "1"
    return f"{PAY_URL}?{urlencode(params)}"


def _find(root: ElementTree.Element, path: str) -> str | None:
    node = root.find(path)
    return node.text.strip() if node is not None and node.text else None


async def op_state(http: aiohttp.ClientSession, login: str, password2: str, inv_id: int,
                   is_test: bool) -> tuple[int, int | None, str | None]:
    """Возвращает (код результата запроса, код состояния платежа, описание). 0 — запрос успешен."""
    params = {"MerchantLogin": login, "InvoiceID": str(inv_id),
              "Signature": _md5(f"{login}:{inv_id}:{password2}")}
    url = OP_STATE_TEST_URL if is_test else OP_STATE_URL
    async with http.get(url, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
        body = await resp.text()
    root = ElementTree.fromstring(body)
    result_code = int(_find(root, ".//{*}Result/{*}Code") or 0)
    state = _find(root, ".//{*}State/{*}Code")
    return result_code, int(state) if state and state.isdigit() else None, _find(root, ".//{*}Result/{*}Description")
