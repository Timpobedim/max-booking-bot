"""Маршрутизация событий: команды, нажатия кнопок, сообщения — вместо ручного разбора в цикле."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from maxkit.client import MaxApi
from maxkit.keyboard import InlineKeyboard
from maxkit.types import Update

Handler = Callable[["Context"], None]


@dataclass
class Context:
    api: MaxApi
    update: Update

    @property
    def argument(self) -> str:
        """Часть payload после префикса: у кнопки «service:tyres» это «tyres»."""
        return (self.update.payload or "").partition(":")[2]

    @property
    def user_id(self) -> int:
        if self.update.user is None:
            raise ValueError("В событии нет пользователя")
        return self.update.user.user_id

    def reply(self, text: str, keyboard: InlineKeyboard | None = None) -> dict[str, Any]:
        if self.update.chat_id is not None:
            return self.api.send_message(chat_id=self.update.chat_id, text=text, keyboard=keyboard)
        return self.api.send_message(user_id=self.user_id, text=text, keyboard=keyboard)

    def notify(self, text: str) -> None:
        """Всплывающее уведомление на нажатие кнопки; на обычные сообщения не отвечает."""
        if self.update.callback_id:
            self.api.answer_callback(self.update.callback_id, notification=text)


class Router:
    def __init__(self) -> None:
        self._commands: dict[str, Handler] = {}
        self._callbacks: list[tuple[str, Handler]] = []
        self._messages: list[Handler] = []
        self._started: Handler | None = None

    def command(self, name: str) -> Callable[[Handler], Handler]:
        def register(handler: Handler) -> Handler:
            self._commands[name] = handler
            return handler

        return register

    def callback(self, prefix: str) -> Callable[[Handler], Handler]:
        def register(handler: Handler) -> Handler:
            self._callbacks.append((prefix, handler))
            return handler

        return register

    def message(self) -> Callable[[Handler], Handler]:
        def register(handler: Handler) -> Handler:
            self._messages.append(handler)
            return handler

        return register

    def started(self) -> Callable[[Handler], Handler]:
        def register(handler: Handler) -> Handler:
            self._started = handler
            return handler

        return register

    def _handler(self, update: Update) -> Handler | None:
        if update.kind == "bot_started":
            return self._started
        if update.kind == "message_callback":
            payload = update.payload or ""
            for prefix, handler in self._callbacks:
                if payload == prefix or payload.startswith(f"{prefix}:"):
                    return handler
            return None
        if update.kind == "message_created":
            if update.text.startswith("/"):
                command = self._commands.get(update.text.split()[0])
                if command is not None:
                    return command
            return self._messages[0] if self._messages else None
        return None

    def dispatch(self, api: MaxApi, update: Update) -> bool:
        """Передаёт событие обработчику; False — события такого вида бот не ждал."""
        handler = self._handler(update)
        if handler is None:
            return False
        handler(Context(api, update))
        return True
