"""SQLite: пользователи, счета, прогресс челленджа и связка «сообщение у админа → участник»."""
import time

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id         INTEGER PRIMARY KEY,
    username        TEXT,
    first_name      TEXT,
    created_at      INTEGER,
    consented_at    INTEGER,            -- принял оферту и политику
    paid_at         INTEGER,
    challenge_day   INTEGER NOT NULL DEFAULT 0,   -- шагов отправлено: 1..7 дни, 8 — финал
    next_day_at     INTEGER,                      -- когда слать следующий день
    awaiting_hw_day INTEGER                       -- день, по которому сейчас сдаёт задание
);
CREATE TABLE IF NOT EXISTS orders (
    inv_id        INTEGER PRIMARY KEY,
    user_id       INTEGER NOT NULL,
    amount        TEXT NOT NULL,
    is_test       INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'new',   -- new | paid
    created_at    INTEGER,
    last_check_at INTEGER,
    paid_at       INTEGER
);
CREATE TABLE IF NOT EXISTS relay (
    admin_chat_id INTEGER NOT NULL,
    admin_msg_id  INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    day           INTEGER,
    PRIMARY KEY (admin_chat_id, admin_msg_id)
);
"""


class DB:
    def __init__(self, path: str):
        self.path = path

    def _c(self):
        return aiosqlite.connect(self.path)

    async def init(self):
        async with self._c() as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def _one(self, sql: str, args: tuple = ()):
        async with self._c() as db:
            db.row_factory = aiosqlite.Row
            return await (await db.execute(sql, args)).fetchone()

    async def _all(self, sql: str, args: tuple = ()):
        async with self._c() as db:
            db.row_factory = aiosqlite.Row
            return await (await db.execute(sql, args)).fetchall()

    async def _exec(self, sql: str, args: tuple = ()) -> int:
        async with self._c() as db:
            cur = await db.execute(sql, args)
            await db.commit()
            return cur.rowcount

    # --- пользователи ---
    async def upsert_user(self, user_id: int, username: str | None, first_name: str | None):
        await self._exec(
            "INSERT INTO users(user_id, username, first_name, created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name",
            (user_id, username, first_name, int(time.time())))

    async def get_user(self, user_id: int):
        return await self._one("SELECT * FROM users WHERE user_id=?", (user_id,))

    async def set_consented(self, user_id: int):
        await self._exec("UPDATE users SET consented_at=? WHERE user_id=?", (int(time.time()), user_id))

    # --- счета ---
    async def create_order(self, user_id: int, amount: str, is_test: bool) -> int:
        now = int(time.time())
        # Номера в диапазоне ~1.7e13 — не пересекаются с OMGbot (~1.7e12), даже если магазин когда-то будет общим.
        inv_id = now * 10000 + 9000 + user_id % 1000
        async with self._c() as db:
            while True:
                try:
                    await db.execute(
                        "INSERT INTO orders(inv_id, user_id, amount, is_test, created_at) VALUES(?,?,?,?,?)",
                        (inv_id, user_id, amount, int(is_test), now))
                    await db.commit()
                    return inv_id
                except aiosqlite.IntegrityError:
                    inv_id += 1

    async def latest_pending(self, user_id: int, is_test: bool):
        return await self._one(
            "SELECT * FROM orders WHERE user_id=? AND status='new' AND is_test=? ORDER BY inv_id DESC LIMIT 1",
            (user_id, int(is_test)))

    async def orders_to_check(self, is_test: bool, min_interval: int = 60, max_age: int = 2 * 86400, limit: int = 20):
        """Неоплаченные счета не старше 2 суток, которые не проверяли последнюю минуту."""
        now = int(time.time())
        return await self._all(
            "SELECT * FROM orders WHERE status='new' AND is_test=? AND created_at>=? "
            "AND (last_check_at IS NULL OR last_check_at<=?) ORDER BY inv_id LIMIT ?",
            (int(is_test), now - max_age, now - min_interval, limit))

    async def mark_checked(self, inv_id: int):
        await self._exec("UPDATE orders SET last_check_at=? WHERE inv_id=?", (int(time.time()), inv_id))

    async def mark_paid(self, inv_id: int, user_id: int) -> bool:
        """True только при первом переводе счёта в paid. Первая оплата пользователя запускает челлендж."""
        now = int(time.time())
        if await self._exec(
                "UPDATE orders SET status='paid', paid_at=? WHERE inv_id=? AND status!='paid'", (now, inv_id)) != 1:
            return False
        await self._exec("UPDATE users SET paid_at=COALESCE(paid_at, ?) WHERE user_id=?", (now, user_id))
        return True

    # --- челлендж ---
    async def due_users(self, now: int, total_days: int):
        return await self._all(
            "SELECT * FROM users WHERE paid_at IS NOT NULL AND challenge_day<? "
            "AND next_day_at IS NOT NULL AND next_day_at<=?", (total_days, now))

    async def set_day_sent(self, user_id: int, day: int, next_at: int | None):
        await self._exec("UPDATE users SET challenge_day=?, next_day_at=? WHERE user_id=?", (day, next_at, user_id))

    async def set_awaiting(self, user_id: int, day: int | None):
        await self._exec("UPDATE users SET awaiting_hw_day=? WHERE user_id=?", (day, user_id))

    # --- пересылка участник ↔ кураторы ---
    async def add_relay(self, admin_chat_id: int, admin_msg_id: int, user_id: int, day: int | None):
        await self._exec("INSERT OR REPLACE INTO relay VALUES(?,?,?,?)", (admin_chat_id, admin_msg_id, user_id, day))

    async def get_relay(self, admin_chat_id: int, admin_msg_id: int):
        return await self._one("SELECT * FROM relay WHERE admin_chat_id=? AND admin_msg_id=?",
                               (admin_chat_id, admin_msg_id))

    async def stats(self) -> dict:
        r = await self._one(
            "SELECT COUNT(*) AS users, "
            "SUM(consented_at IS NOT NULL) AS consented, SUM(paid_at IS NOT NULL) AS paid, "
            "SUM(paid_at IS NOT NULL AND challenge_day>=7) AS finished FROM users")
        return {k: r[k] or 0 for k in ("users", "consented", "paid", "finished")}
