"""Диалог записи: услуга → день → время → телефон → подтверждение (и отмена в два нажатия)."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, timedelta

from maxkit import Button, InlineKeyboard
from maxkit.router import Context, Router
from tyre_bot.schedule import SERVICES, free_slots, label
from tyre_bot.settings import Settings
from tyre_bot.storage import Booking, SlotTakenError, Storage

GREETING = "Здравствуйте! Это шиномонтаж «Колесо» на Садовой, 12. Выберите услугу — подберём удобное время."


def normalize_phone(text: str | None) -> str | None:
    """«8 (900) 123-45-67», «+7 900 1234567», «9001234567» → «+79001234567»."""
    digits = re.sub(r"\D", "", text or "")
    if len(digits) == 11 and digits[0] in "78":
        digits = digits[1:]
    return f"+7{digits}" if len(digits) == 10 and digits[0] == "9" else None


def services_keyboard() -> InlineKeyboard:
    keyboard = InlineKeyboard()
    for service in SERVICES.values():
        keyboard.row(Button.callback(f"{service.title} — {service.price} ₽", f"service:{service.key}"))
    return keyboard


def days_keyboard(today: date, count: int) -> InlineKeyboard:
    days = [today + timedelta(days=offset) for offset in range(count)]
    keyboard = InlineKeyboard()
    for start in range(0, len(days), 3):
        keyboard.row(
            *(Button.callback(label(day), f"day:{day.isoformat()}") for day in days[start : start + 3])
        )
    return keyboard


def slots_keyboard(slots: list[datetime]) -> InlineKeyboard:
    keyboard = InlineKeyboard()
    for start in range(0, len(slots), 4):
        keyboard.row(
            *(
                Button.callback(f"{slot:%H:%M}", f"slot:{slot.isoformat()}")
                for slot in slots[start : start + 4]
            )
        )
    return keyboard.row(Button.callback("← Другой день", "back"))


def contact_keyboard() -> InlineKeyboard:
    return InlineKeyboard().row(Button.request_contact("Поделиться номером"))


def describe(booking: Booking) -> str:
    return f"{booking.title}, {label(booking.starts_at.date())} в {booking.starts_at:%H:%M}"


def build_router(storage: Storage, settings: Settings, now: Callable[[], datetime]) -> Router:
    router = Router()

    def show_days(ctx: Context, text: str) -> None:
        ctx.reply(text, days_keyboard(now().date(), settings.days_ahead))

    def show_slots(ctx: Context, service_key: str, day: date) -> bool:
        slots = free_slots(
            day,
            SERVICES[service_key],
            storage.day_bookings(day),
            opens=settings.opens,
            closes=settings.closes,
            posts=settings.posts,
            now=now(),
        )
        if not slots:
            return False
        ctx.reply(f"Свободное время, {label(day)}:", slots_keyboard(slots))
        return True

    @router.started()
    def started(ctx: Context) -> None:
        ctx.reply(GREETING, services_keyboard())

    router.command("/start")(started)

    @router.callback("service")
    def choose_service(ctx: Context) -> None:
        service = SERVICES.get(ctx.argument)
        if service is None:
            ctx.notify("Эта услуга больше не оказывается")
            ctx.reply(GREETING, services_keyboard())
            return
        storage.save_draft(ctx.user_id, service=service.key)
        ctx.notify(service.title)
        show_days(
            ctx,
            f"{service.title}: около {service.minutes} минут, {service.price} ₽.\nНа какой день записать?",
        )

    @router.callback("day")
    def choose_day(ctx: Context) -> None:
        draft = storage.draft(ctx.user_id)
        if draft.service is None:
            ctx.reply("Сначала выберите услугу.", services_keyboard())
            return
        day = date.fromisoformat(ctx.argument)
        storage.save_draft(ctx.user_id, service=draft.service, day=day)
        ctx.notify(label(day))
        if not show_slots(ctx, draft.service, day):
            show_days(ctx, f"{label(day)} свободного времени нет — выберите другой день.")

    @router.callback("back")
    def back(ctx: Context) -> None:
        show_days(ctx, "На какой день записать?")

    @router.callback("slot")
    def choose_slot(ctx: Context) -> None:
        draft = storage.draft(ctx.user_id)
        if draft.service is None:
            ctx.reply("Сначала выберите услугу.", services_keyboard())
            return
        slot = datetime.fromisoformat(ctx.argument)
        storage.save_draft(ctx.user_id, service=draft.service, day=slot.date(), slot=slot)
        ctx.notify(f"{slot:%H:%M}")
        ctx.reply(
            "Оставьте номер телефона — пришлём подтверждение и позвоним, если что-то изменится.\n"
            "Нажмите «Поделиться номером» или напишите его сообщением.",
            contact_keyboard(),
        )

    @router.message()
    def on_message(ctx: Context) -> None:
        draft = storage.draft(ctx.user_id)
        if draft.service is None or draft.slot is None:
            ctx.reply("Чтобы записаться, выберите услугу.", services_keyboard())
            return
        phone = normalize_phone(ctx.update.phone or ctx.update.text)
        if phone is None:
            ctx.reply(
                "Не похоже на номер телефона. Пришлите его в виде +7 900 123-45-67.", contact_keyboard()
            )
            return
        user = ctx.update.user
        try:
            booking = storage.book(
                user_id=ctx.user_id,
                name=user.name if user else "Клиент",
                phone=phone,
                service=draft.service,
                starts_at=draft.slot,
                posts=settings.posts,
            )
        except SlotTakenError:
            ctx.reply("Это время только что заняли. Выберите другое:")
            if not show_slots(ctx, draft.service, draft.slot.date()):
                show_days(ctx, "На этот день мест больше нет — выберите другой.")
            return
        storage.clear_draft(ctx.user_id)
        ctx.reply(
            f"Готово! Вы записаны: {describe(booking)}.\nАдрес: Садовая, 12. Ждём вас!",
            InlineKeyboard().row(Button.callback("Отменить запись", f"cancel:{booking.id}")),
        )
        if settings.admin_chat_id is not None:
            ctx.api.send_message(
                chat_id=settings.admin_chat_id,
                text=f"Новая запись: {describe(booking)}. {booking.name}, {booking.phone}",
            )

    @router.command("/my")
    def my_bookings(ctx: Context) -> None:
        bookings = storage.upcoming(ctx.user_id, now())
        if not bookings:
            ctx.reply("Предстоящих записей нет.", services_keyboard())
            return
        keyboard = InlineKeyboard()
        for booking in bookings:
            keyboard.row(
                Button.callback(
                    f"Отменить: {label(booking.starts_at.date())} {booking.starts_at:%H:%M}",
                    f"cancel:{booking.id}",
                )
            )
        ctx.reply("Ваши записи:\n" + "\n".join(f"• {describe(booking)}" for booking in bookings), keyboard)

    @router.callback("cancel")
    def cancel(ctx: Context) -> None:
        booking = storage.cancel(int(ctx.argument), ctx.user_id)
        if booking is None:
            ctx.notify("Запись уже отменена")
            return
        ctx.notify("Запись отменена")
        ctx.reply(f"Запись отменена: {describe(booking)}. Будем рады видеть вас в другой раз!")
        if settings.admin_chat_id is not None:
            ctx.api.send_message(
                chat_id=settings.admin_chat_id,
                text=f"Отмена: {describe(booking)}. {booking.name}, {booking.phone}",
            )

    return router
