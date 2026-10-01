"""Long polling: бесконечный поток событий.

Маркер сохраняется в файл после того, как пачка событий обработана: после перезапуска бот
продолжит с того же места — событие может прийти повторно, но не потеряется (поэтому обработчики
в боте идемпотентны). Сетевые сбои и 5xx цикл не роняют — пауза растёт до минуты; неверный токен
(401, 403) роняет: чинить его должен человек.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from pathlib import Path

from maxkit.client import MaxApi, MaxApiError, Sleep
from maxkit.types import Update, parse_update

logger = logging.getLogger(__name__)


class LongPoll:
    def __init__(
        self,
        api: MaxApi,
        *,
        marker_file: Path | None = None,
        timeout: int = 30,
        types: list[str] | None = None,
        sleep: Sleep = time.sleep,
        max_pause: float = 60,
    ) -> None:
        self.api = api
        self.marker_file = marker_file
        self.timeout = timeout
        self.types = types
        self.max_pause = max_pause
        self._sleep = sleep
        self.marker = self._load()

    def _load(self) -> int | None:
        if self.marker_file is None or not self.marker_file.exists():
            return None
        text = self.marker_file.read_text(encoding="utf-8").strip()
        return int(text) if text.isdigit() else None

    def _save(self, marker: int) -> None:
        self.marker = marker
        if self.marker_file is None:
            return
        self.marker_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.marker_file.with_suffix(".tmp")
        temporary.write_text(str(marker), encoding="utf-8")
        # атомарно: при сбое останется старый маркер, а не пустой файл
        os.replace(temporary, self.marker_file)

    def fetch(self) -> tuple[list[Update], int | None]:
        raw, marker = self.api.get_updates(marker=self.marker, timeout=self.timeout, types=self.types)
        return [parse_update(item) for item in raw], marker

    def listen(self) -> Iterator[Update]:
        pause = 1.0
        while True:
            try:
                updates, marker = self.fetch()
            except MaxApiError as exc:
                if exc.status in (401, 403):
                    raise
                logger.warning("Не удалось получить события: %s — повтор через %.0f с", exc, pause)
                self._sleep(pause)
                pause = min(pause * 2, self.max_pause)
                continue
            pause = 1.0
            yield from updates  # генератор продолжится, когда вызывающий код обработает все события
            if marker is not None:
                self._save(marker)
