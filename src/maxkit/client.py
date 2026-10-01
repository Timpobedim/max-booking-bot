"""Клиент MAX Bot API на httpx — то, чем vk_api был для VK: методы, темп запросов, повторы, загрузки.

    with MaxApi(token) as api:
        keyboard = InlineKeyboard().row(Button.callback("Записаться", "book"))
        api.send_message(user_id=42, text="Здравствуйте!", keyboard=keyboard)

Особенности:
- темп 30 запросов в секунду (лимит MAX) держит ведро с резервированием времени под замком, а спит
  поток уже вне замка; в vk_api поток спал 0,34 с, удерживая общий `threading.Lock`;
- повторы ограничены: 429 и 5xx — не больше `retries` раз с растущей паузой (в vk_api обработчик
  ошибки «слишком много запросов» ждал 0,5 с и повторял запрос без счётчика попыток);
- токен передаётся заголовком `Authorization` и нигде не сохраняется: в vk_api токены по умолчанию
  записывались открытым текстом в `vk_config.v2.json` в текущей папке.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, Self

import httpx

from maxkit.keyboard import InlineKeyboard

BASE_URL = "https://platform-api2.max.ru"
RPS = 30  # лимит MAX Bot API

Sleep = Callable[[float], None]


class MaxApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"MAX API {status}: {message}" if status else message)
        self.status = status
        self.code = code


def _error(response: httpx.Response) -> MaxApiError:
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return MaxApiError(
        response.status_code,
        str(payload.get("code", "")),
        str(payload.get("message") or response.reason_phrase),
    )


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["Retry-After"])
    except (KeyError, ValueError):
        return None


class TokenBucket:
    """Не больше `rate` запросов в секунду, равномерно; потокобезопасно."""

    def __init__(
        self, rate: float, *, clock: Callable[[], float] = time.monotonic, sleep: Sleep = time.sleep
    ) -> None:
        self.rate = rate
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:  # под замком — только расчёт очереди, без сна
            now = self._clock()
            start = max(now, self._next)
            self._next = start + 1 / self.rate
        if start > now:
            self._sleep(start - now)


class MaxApi:
    def __init__(
        self,
        token: str,
        base_url: str = BASE_URL,
        *,
        transport: httpx.BaseTransport | None = None,
        rate: float = RPS,
        retries: int = 3,
        backoff: float = 0.5,
        timeout: float = 40,
        sleep: Sleep = time.sleep,
    ) -> None:
        if not token.strip() or not token.isascii():
            # иначе httpx упадёт с UnicodeEncodeError — например, если в токен попала кириллическая «а»
            raise ValueError(
                "Токен MAX должен состоять из латинских букв и цифр — проверьте, что он скопирован целиком"
            )
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": token},
            transport=transport,
            timeout=httpx.Timeout(timeout, connect=10),
        )
        # отдельный клиент без токена: адрес загрузки выдаёт API, и токену бота там делать нечего
        self._upload_http = httpx.Client(transport=transport, timeout=httpx.Timeout(timeout * 3, connect=10))
        self._bucket = TokenBucket(rate, sleep=sleep)
        self._sleep = sleep
        self.retries = retries
        self.backoff = backoff

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()
        self._upload_http.close()

    def call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        timeout: float | None = None,
    ) -> Any:
        query = {key: value for key, value in (params or {}).items() if value is not None}
        request_timeout: Any = timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT
        for attempt in range(self.retries + 1):
            self._bucket.acquire()
            try:
                response = self._http.request(method, path, params=query, json=json, timeout=request_timeout)
            except httpx.TransportError as exc:
                if attempt == self.retries:
                    raise MaxApiError(0, "network", f"Нет связи с MAX: {exc}") from exc
                self._sleep(self.backoff * 2**attempt)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                if attempt == self.retries:
                    raise _error(response)
                self._sleep(_retry_after(response) or self.backoff * 2**attempt)
                continue
            if response.status_code >= 400:
                raise _error(response)
            return response.json() if response.content else None
        raise AssertionError("недостижимо")  # pragma: no cover

    # --- методы ------------------------------------------------------------------------------------

    def me(self) -> dict[str, Any]:
        result: dict[str, Any] = self.call("GET", "/me")
        return result

    def send_message(
        self,
        *,
        text: str,
        user_id: int | None = None,
        chat_id: int | None = None,
        keyboard: InlineKeyboard | None = None,
        attachments: list[dict[str, Any]] | None = None,
        notify: bool = True,
    ) -> dict[str, Any]:
        if (user_id is None) == (chat_id is None):
            raise ValueError("Нужно указать ровно одно: user_id или chat_id")
        body: dict[str, Any] = {"text": text, "notify": notify}
        items = [*(attachments or []), *([keyboard.attachment()] if keyboard else [])]
        if items:
            body["attachments"] = items
        result: dict[str, Any] = self.call(
            "POST", "/messages", params={"user_id": user_id, "chat_id": chat_id}, json=body
        )
        return dict(result.get("message", {}))

    def edit_message(self, message_id: str, *, text: str, keyboard: InlineKeyboard | None = None) -> None:
        body: dict[str, Any] = {"text": text, "attachments": [keyboard.attachment()] if keyboard else []}
        self.call("PUT", "/messages", params={"message_id": message_id}, json=body)

    def delete_message(self, message_id: str) -> None:
        self.call("DELETE", "/messages", params={"message_id": message_id})

    def answer_callback(self, callback_id: str, *, notification: str) -> None:
        """Всплывающее уведомление в ответ на нажатие кнопки — без него клиент покажет «загрузку»."""
        self.call(
            "POST", "/answers", params={"callback_id": callback_id}, json={"notification": notification}
        )

    def get_updates(
        self,
        *,
        marker: int | None = None,
        timeout: int = 30,
        limit: int = 100,
        types: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        data = self.call(
            "GET",
            "/updates",
            params={
                "marker": marker,
                "timeout": timeout,
                "limit": limit,
                "types": ",".join(types) if types else None,
            },
            timeout=timeout + 10,  # сервер держит запрос до timeout секунд — клиенту нужно ждать дольше
        )
        return list(data.get("updates", [])), data.get("marker")

    def upload(self, kind: str, filename: str, content: bytes) -> dict[str, Any]:
        """Двухшаговая загрузка, как VkUpload: POST /uploads?type=… выдаёт адрес, файл уходит туда,
        из ответа собирается вложение {"type": kind, "payload": …} для send_message."""
        target = self.call("POST", "/uploads", params={"type": kind})
        self._bucket.acquire()
        response = self._upload_http.post(target["url"], files={"data": (filename, content)})
        if response.status_code >= 400:
            raise _error(response)
        uploaded = response.json() if response.content else {}
        payload = uploaded if uploaded else {"token": target.get("token")}
        return {"type": kind, "payload": payload}
