"""Оплата Stars и картой."""

import os
import logging

from aiogram import Bot
from aiogram.types import (
    Message, CallbackQuery,
    LabeledPrice, PreCheckoutQuery, SuccessfulPayment,
)

import database as db
from subscription import grant, is_admin

logger = logging.getLogger(__name__)

STARS_PRICE = int(os.getenv("STARS_PRICE", "50"))


async def send_stars_invoice(bot: Bot, chat_id: int) -> None:
    """Отправка инвойса Telegram Stars."""
    try:
        await bot.send_invoice(
            chat_id=chat_id,
            title="Подписка TikTok Bot",
            description="Доступ к функциям бота на 30 дней",
            payload="subscription_stars",
            provider_token="",
            currency="XTR",
            prices=[LabeledPrice(label="Подписка", amount=STARS_PRICE)],
        )
        logger.info("Инвойс Stars отправлен chat_id=%s", chat_id)
    except Exception:
        logger.exception("Ошибка отправки инвойса Stars")


async def handle_pre_checkout(pre_checkout: PreCheckoutQuery) -> None:
    try:
        from main import bot
        await bot.answer_pre_checkout_query(pre_checkout.id, ok=True)
        logger.info("PreCheckout OK user=%s", pre_checkout.from_user.id)
    except Exception:
        logger.exception("Ошибка PreCheckout")


async def handle_successful_payment(message: Message) -> None:
    sp: SuccessfulPayment = message.successful_payment
    user_id = message.from_user.id
    try:
        if sp.currency != "XTR":
            logger.warning("Неожиданная валюта: %s", sp.currency)
        await db.create_payment(user_id, "stars", sp.total_amount, "confirmed")
        expires = await grant(user_id)
        await message.answer(f"✅ Оплата Stars получена. Подписка до {expires[:10]}.")
        logger.info("Оплата Stars user=%s amount=%s", user_id, sp.total_amount)
    except Exception:
        logger.exception("Ошибка обработки успешной оплаты")
        await message.answer("⚠️ Оплата прошла, но произошла ошибка. Напишите админу.")


async def send_card_details(message: Message) -> None:
    """Показ реквизитов и создание заявки."""
    try:
        card = await db.get_setting("card_number", os.getenv("DEFAULT_CARD_NUMBER", ""))
        holder = await db.get_setting("card_holder", os.getenv("DEFAULT_CARD_HOLDER", ""))
        if not card:
            await message.answer("⚠️ Реквизиты пока не заданы админом.")
            return
        await db.create_payment(message.from_user.id, "card", STARS_PRICE, "pending")
        await message.answer(
            f"💳 Реквизиты для оплаты:\n\n"
            f"Номер карты: <code>{card}</code>\n"
            f"Получатель: {holder}\n"
            f"Сумма: {STARS_PRICE} ⭐ (эквивалент)\n\n"
            f"После оплаты отправьте скриншот чека сюда.\n"
            f"Админ проверит и подтвердит подписку."
        )
        logger.info("Реквизиты отправлены user=%s", message.from_user.id)
    except Exception:
        logger.exception("Ошибка отправки реквизитов")
        await message.answer("⚠️ Ошибка. Попробуйте позже.")


async def handle_screenshot(message: Message, file_id: str) -> None:
    """Сохраняет file_id скриншота и уведомляет админа."""
    from main import bot, ADMIN_ID
    user_id = message.from_user.id
    try:
        payment_id = await db.create_payment(user_id, "card", STARS_PRICE, "pending", file_id)
        await message.answer("📥 Скриншот получен. Ожидайте подтверждения админа.")
        logger.info("Скриншот user=%s payment=%s", user_id, payment_id)
        if is_admin(user_id):
            await message.answer("(вы админ — подтвердите себе через админку)")
        else:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"admin_confirm_{payment_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"admin_reject_{payment_id}"),
            ]])
            await bot.send_photo(
                ADMIN_ID, photo=file_id,
                caption=f"Чек от user <code>{user_id}</code>\npayment_id={payment_id}\nСумма: {STARS_PRICE}",
                reply_markup=kb,
            )
    except Exception:
        logger.exception("Ошибка обработки скриншота")
        await message.answer("⚠️ Ошибка. Попробуйте позже.")


async def confirm_payment(callback: CallbackQuery, payment_id: int) -> None:
    try:
        p = await db.get_payment(payment_id)
        if not p or p["status"] != "pending":
            await callback.answer("Платёж не найден или уже обработан", show_alert=True)
            return
        await db.set_payment_status(payment_id, "confirmed")
        expires = await grant(p["user_id"])
        from main import bot
        await bot.send_message(
            p["user_id"],
            f"✅ Ваш платёж подтверждён. Подписка до {expires[:10]}."
        )
        try:
            await callback.message.edit_caption(caption="✅ Подтверждено")
        except Exception:
            pass
        logger.info("Платёж %s подтверждён админом", payment_id)
    except Exception:
        logger.exception("Ошибка подтверждения платежа")
        await callback.answer("Ошибка", show_alert=True)


async def reject_payment(callback: CallbackQuery, payment_id: int) -> None:
    try:
        p = await db.get_payment(payment_id)
        if not p or p["status"] != "pending":
            await callback.answer("Платёж не найден", show_alert=True)
            return
        await db.set_payment_status(payment_id, "rejected")
        from main import bot
        await bot.send_message(p["user_id"], "❌ Платёж отклонён. Свяжитесь с админом.")
        try:
            await callback.message.edit_caption(caption="❌ Отклонено")
        except Exception:
            pass
        logger.info("Платёж %s отклонён", payment_id)
    except Exception:
        logger.exception("Ошибка отклонения платежа")
        await callback.answer("Ошибка", show_alert=True)
