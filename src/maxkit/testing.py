"""Эмулятор MAX Bot API для тестов ботов — через httpx.MockTransport, без сети.

    emulator = MaxEmulator()
    api = MaxApi(emulator.token, transport=emulator.transport(), sleep=lambda _: None)
    emulator.user_says(1, "/start")
    ...
    assert emulator.sent[-1]["text"].startswith("Здравствуйте")

Повторяет то, от чего зависит бот: заголовок Authorization с токеном, отправку, правку и удаление
сообщений, ответы на нажатия кнопок, очередь событий с маркером, двухшаговую загрузку файла.
В vk_api для тестов ботов ничего подобного нет — проверять их приходилось на живом сообществе.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

UPLOAD_HOST = "upload.max.test"


class MaxEmulator:
    def __init__(self, token: str = "test-token") -> None:  # noqa: S107 — тестовый токен
        self.token = token
        self.events: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []
        self.edited: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.answers: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []
        self.fail_next: list[int] = []  # статусы, которыми ответить на ближайшие запросы
        self._counter = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}.{self._counter}"

    # --- что делают пользователи --------------------------------------------------------------

    @staticmethod
    def dialog(user_id: int) -> int:
        return 10**9 + user_id  # у диалога с ботом свой chat_id

    def _user(self, user_id: int, name: str) -> dict[str, Any]:
        return {"user_id": user_id, "name": name, "is_bot": False}

    def user_starts(self, user_id: int, *, name: str = "Покупатель", payload: str | None = None) -> None:
        self.events.append(
            {
                "update_type": "bot_started",
                "chat_id": self.dialog(user_id),
                "user": self._user(user_id, name),
                "payload": payload,
            }
        )

    def user_says(
        self,
        user_id: int,
        text: str = "",
        *,
        name: str = "Покупатель",
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        body = {"mid": self._next_id("mid"), "text": text, "attachments": attachments or []}
        message = {
            "sender": self._user(user_id, name),
            "recipient": {"chat_id": self.dialog(user_id)},
            "body": body,
        }
        self.events.append({"update_type": "message_created", "message": message})

    def user_shares_contact(self, user_id: int, phone: str, *, name: str = "Покупатель") -> None:
        vcard = f"BEGIN:VCARD\nVERSION:3.0\nFN:{name}\nTEL;TYPE=cell:{phone}\nEND:VCARD"
        self.user_says(user_id, name=name, attachments=[{"type": "contact", "payload": {"vcf_info": vcard}}])

    def user_presses(self, user_id: int, payload: str, *, name: str = "Покупатель") -> str:
        callback_id = self._next_id("cb")
        message = {"recipient": {"chat_id": self.dialog(user_id)}, "body": {"mid": self._next_id("mid")}}
        self.events.append(
            {
                "update_type": "message_callback",
                "callback": {
                    "callback_id": callback_id,
                    "payload": payload,
                    "user": self._user(user_id, name),
                },
                "message": message,
            }
        )
        return callback_id

    # --- ответы сервера ---------------------------------------------------------------------------

    @staticmethod
    def _json(status: int, payload: Any) -> httpx.Response:
        return httpx.Response(status, json=payload)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == UPLOAD_HOST:
            return self._json(200, {"token": f"file-{request.url.path.rsplit('/', 1)[-1]}"})
        if request.headers.get("Authorization") != self.token:
            return self._json(401, {"code": "verify.token", "message": "Invalid access_token"})
        if self.fail_next:
            status = self.fail_next.pop(0)
            return httpx.Response(
                status,
                json={"code": "too.many.requests", "message": "Слишком много запросов"},
                headers={"Retry-After": "0"},
            )
        params = dict(request.url.params)
        body = json.loads(request.content) if request.content else None
        route = (request.method, request.url.path)
        if route == ("GET", "/me"):
            return self._json(
                200,
                {"user_id": 1000, "name": "Шиномонтаж «Колесо»", "username": "koleso_bot", "is_bot": True},
            )
        if route == ("POST", "/messages"):
            target = {key: int(value) for key, value in params.items() if key in ("user_id", "chat_id")}
            message = {**target, **(body or {}), "mid": self._next_id("mid")}
            self.sent.append(message)
            return self._json(
                200, {"message": {"body": {"mid": message["mid"], "text": message.get("text")}}}
            )
        if route == ("PUT", "/messages"):
            self.edited.append({"message_id": params.get("message_id"), **(body or {})})
            return self._json(200, {"success": True})
        if route == ("DELETE", "/messages"):
            self.deleted.append(params.get("message_id", ""))
            return self._json(200, {"success": True})
        if route == ("POST", "/answers"):
            self.answers.append({"callback_id": params.get("callback_id"), **(body or {})})
            return self._json(200, {"success": True})
        if route == ("GET", "/updates"):
            marker = int(params.get("marker") or 0)
            limit = int(params.get("limit") or 100)
            batch = self.events[marker : marker + limit]
            return self._json(200, {"updates": batch, "marker": marker + len(batch)})
        if route == ("POST", "/uploads"):
            return self._json(200, {"url": f"https://{UPLOAD_HOST}/upload/{self._next_id('up')}"})
        return self._json(
            404, {"code": "not.found", "message": f"Нет метода {request.method} {request.url.path}"}
        )
