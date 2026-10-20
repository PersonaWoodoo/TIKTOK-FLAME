"""Проверка подписки: канал + платная подписка."""

import os
import logging
from functools import wraps

from aiogram import Bot
from aiogram.types import Message, CallbackQuery

import database as db

logger = logging.getLogger(__name__)

ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
SUBSCRIPTION_DAYS = int(os.getenv("SUBSCRIPTION_DAYS", "30"))
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME", "@flameTT_INFO")
CHANNEL_URL = os.getenv("CHANNEL_URL", "https://t.me/flameTT_INFO")


def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


async def is_channel_subscribed(bot: Bot, user_id: int) -> bool:
    """Проверяет подписку на канал. При ошибке (бот не админ и т.п.) — False."""
    if is_admin(user_id):
        return True
    try:
        member = await bot.get_chat_member(CHANNEL_USERNAME, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception:
        logger.exception("Ошибка проверки подписки на канал для %s", user_id)
        return False


async def has_access(bot: Bot, user_id: int) -> bool:
    """Админ — всегда; остальные — подписка на канал + платная подписка."""
    if is_admin(user_id):
        return True
    if not await is_channel_subscribed(bot, user_id):
        return False
    try:
        return await db.is_subscription_active(user_id)
    except Exception:
        logger.exception("Ошибка проверки платной подписки для %s", user_id)
        return False


def require_subscription(handler):
    """Для Message-хендлеров. Бот берём из message.bot."""
    @wraps(handler)
    async def wrapper(message: Message, *args, **kwargs):
        if not await has_access(message.bot, message.from_user.id):
            await message.answer(
                "⛔ Нужна подписка на канал и активная платная подписка.\n"
                "Открой /start."
            )
            return
        return await handler(message, *args, **kwargs)
    return wrapper


def require_subscription_cb(handler):
    @wraps(handler)
    async def wrapper(callback: CallbackQuery, *args, **kwargs):
        if not await has_access(callback.bot, callback.from_user.id):
            await callback.answer("⛔ Требуется подписка на канал и платная подписка",
                                  show_alert=True)
            return
        return await handler(callback, *args, **kwargs)
    return wrapper


async def grant(user_id: int, days: int | None = None) -> str:
    d = days if days is not None else SUBSCRIPTION_DAYS
    expires = await db.extend_subscription(user_id, d)
    logger.info("Подписка продлена user=%s на %s дн. до %s", user_id, d, expires)
    return expires
