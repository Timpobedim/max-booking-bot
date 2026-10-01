"""Inline-клавиатура MAX — вложение `inline_keyboard` с рядами кнопок."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Self


@dataclass(frozen=True)
class Button:
    type: str
    text: str
    payload: str | None = None
    url: str | None = None

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("У кнопки должен быть текст")
        if self.type == "callback" and not self.payload:
            raise ValueError(f"Кнопке «{self.text}» нужен payload — по нему бот узнает, что нажали")
        if self.type == "link" and not (self.url or "").startswith(("https://", "http://")):
            raise ValueError(f"Кнопке-ссылке «{self.text}» нужен адрес http(s)")

    @classmethod
    def callback(cls, text: str, payload: str) -> Button:
        return cls("callback", text, payload=payload)

    @classmethod
    def link(cls, text: str, url: str) -> Button:
        return cls("link", text, url=url)

    @classmethod
    def request_contact(cls, text: str) -> Button:
        return cls("request_contact", text)

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.type, "text": self.text}
        if self.payload is not None:
            data["payload"] = self.payload
        if self.url is not None:
            data["url"] = self.url
        return data


@dataclass
class InlineKeyboard:
    rows: list[list[Button]] = field(default_factory=list)

    def row(self, *buttons: Button) -> Self:
        if not buttons:
            raise ValueError("Пустой ряд кнопок")
        self.rows.append(list(buttons))
        return self

    def attachment(self) -> dict[str, Any]:
        if not self.rows:
            raise ValueError("В клавиатуре нет ни одной кнопки")
        buttons = [[button.to_json() for button in row] for row in self.rows]
        return {"type": "inline_keyboard", "payload": {"buttons": buttons}}
