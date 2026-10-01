"""Клиент MAX Bot API против эмулятора: запросы, повторы, темп, загрузка файлов."""

from __future__ import annotations

import pytest

from maxkit import Button, InlineKeyboard, MaxApi, MaxApiError
from maxkit.client import TokenBucket
from maxkit.testing import UPLOAD_HOST, MaxEmulator


def test_message_with_keyboard_goes_to_the_user(api: MaxApi, emulator: MaxEmulator) -> None:
    api.send_message(
        user_id=42, text="Здравствуйте", keyboard=InlineKeyboard().row(Button.callback("Записаться", "book"))
    )

    [sent] = emulator.sent
    assert (sent["user_id"], sent["text"]) == (42, "Здравствуйте")
    assert sent["attachments"] == [
        {
            "type": "inline_keyboard",
            "payload": {"buttons": [[{"type": "callback", "text": "Записаться", "payload": "book"}]]},
        }
    ]
    request = emulator.requests[-1]
    assert request.headers["Authorization"] == emulator.token
    assert "access_token" not in str(request.url)  # токен — только в заголовке


@pytest.mark.parametrize("recipients", [{}, {"user_id": 1, "chat_id": 2}])
def test_exactly_one_recipient_is_required(api: MaxApi, recipients: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="ровно одно"):
        api.send_message(text="Привет", **recipients)


def test_callback_is_answered_with_a_notification(api: MaxApi, emulator: MaxEmulator) -> None:
    api.answer_callback("cb.7", notification="Готово")

    assert emulator.answers == [{"callback_id": "cb.7", "notification": "Готово"}]


def test_overload_is_retried(api: MaxApi, emulator: MaxEmulator) -> None:
    emulator.fail_next = [429, 503]

    assert api.me()["username"] == "koleso_bot"


def test_retries_are_limited(emulator: MaxEmulator) -> None:
    emulator.fail_next = [503] * 3
    with (
        MaxApi(emulator.token, transport=emulator.transport(), retries=2, sleep=lambda _: None) as api,
        pytest.raises(MaxApiError, match="MAX API 503"),
    ):
        api.me()


def test_wrong_token_is_reported(emulator: MaxEmulator) -> None:
    with (
        MaxApi("wrong-token", transport=emulator.transport(), sleep=lambda _: None) as api,
        pytest.raises(MaxApiError) as error,
    ):
        api.me()

    assert (error.value.status, error.value.code) == (401, "verify.token")


def test_token_with_cyrillic_letters_is_rejected_clearly() -> None:
    with pytest.raises(ValueError, match="латинских букв"):
        MaxApi("токен-с-кириллицей")


def test_pace_is_kept_at_the_rate() -> None:
    now = [0.0]
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    bucket = TokenBucket(30, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        bucket.acquire()

    assert slept == [pytest.approx(1 / 30), pytest.approx(1 / 30)]  # первый сразу, дальше — через 1/30 с


def test_upload_is_two_step_and_the_token_stays_home(api: MaxApi, emulator: MaxEmulator) -> None:
    attachment = api.upload("image", "схема проезда.png", b"\x89PNG")

    assert attachment == {"type": "image", "payload": {"token": "file-up.1"}}
    upload = emulator.requests[-1]
    assert upload.url.host == UPLOAD_HOST
    assert "Authorization" not in upload.headers
