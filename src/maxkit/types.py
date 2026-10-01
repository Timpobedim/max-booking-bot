"""События MAX Bot API в удобном виде: кто, в каком чате, что написал или нажал."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

PHONE_IN_VCARD = re.compile(r"^TEL[^:]*:(.+)$", re.MULTILINE)


@dataclass(frozen=True)
class User:
    user_id: int
    name: str


@dataclass(frozen=True)
class Update:
    kind: str  # update_type: message_created, message_callback, bot_started, …
    user: User | None = None
    chat_id: int | None = None
    text: str = ""
    payload: str | None = None  # payload нажатой кнопки или ссылки на запуск бота
    callback_id: str | None = None
    message_id: str | None = None
    phone: str | None = None  # из вложения-контакта (кнопка «Поделиться номером»)
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


def _user(data: dict[str, Any] | None) -> User | None:
    if not data or "user_id" not in data:
        return None
    name = data.get("name") or " ".join(filter(None, [data.get("first_name"), data.get("last_name")]))
    return User(int(data["user_id"]), str(name or data["user_id"]))


def _phone(attachments: list[dict[str, Any]]) -> str | None:
    for attachment in attachments:
        if attachment.get("type") == "contact":
            vcard = str(attachment.get("payload", {}).get("vcf_info", ""))
            if match := PHONE_IN_VCARD.search(vcard):
                return match.group(1).strip()
    return None


def parse_update(data: dict[str, Any]) -> Update:
    kind = str(data.get("update_type", ""))
    message = data.get("message") or {}
    body = message.get("body") or {}
    chat_id = (message.get("recipient") or {}).get("chat_id") or data.get("chat_id")
    common: dict[str, Any] = {"kind": kind, "chat_id": chat_id, "message_id": body.get("mid"), "raw": data}
    if kind == "message_created":
        return Update(
            **common,
            user=_user(message.get("sender")),
            text=str(body.get("text") or ""),
            phone=_phone(body.get("attachments") or []),
        )
    if kind == "message_callback":
        callback = data.get("callback") or {}
        return Update(
            **common,
            user=_user(callback.get("user")),
            payload=callback.get("payload"),
            callback_id=callback.get("callback_id"),
        )
    if kind == "bot_started":
        return Update(**common, user=_user(data.get("user")), payload=data.get("payload"))
    return Update(**common, user=_user(data.get("user")))
