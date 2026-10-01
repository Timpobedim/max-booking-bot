"""Запуск: `python -m tyre_bot` (настройки — в .env, пример — .env.example)."""

from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from maxkit import MaxApi, MaxApiError
from maxkit.longpoll import LongPoll
from tyre_bot.bot import build_router
from tyre_bot.settings import Settings
from tyre_bot.storage import Storage

logger = logging.getLogger("tyre_bot")
UPDATE_TYPES = ["bot_started", "message_created", "message_callback"]


def main() -> None:
    settings = Settings()
    if not settings.max_token:
        raise SystemExit("Задайте TYRE_BOT_MAX_TOKEN в .env — пример в .env.example")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # не писать в лог каждый запрос к API
    zone = ZoneInfo(settings.timezone)
    storage = Storage(settings.database)
    with MaxApi(settings.max_token) as api:
        logger.info("Бот «%s» запущен", api.me().get("name"))
        router = build_router(storage, settings, now=lambda: datetime.now(zone).replace(tzinfo=None))
        poll = LongPoll(api, marker_file=settings.database.with_suffix(".marker"), types=UPDATE_TYPES)
        for update in poll.listen():
            try:
                if not router.dispatch(api, update):
                    logger.info("Событие %s боту не нужно", update.kind)
            except MaxApiError:  # одно неотправленное сообщение не должно останавливать бота
                logger.exception("Не удалось ответить на событие %s", update.kind)


if __name__ == "__main__":
    main()
