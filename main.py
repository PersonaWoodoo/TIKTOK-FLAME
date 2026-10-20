"""Telegram-бот: аккаунты, подписка, оплата, рефералы, админка."""

import os
import csv
import io
import logging
from datetime import datetime

from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    PreCheckoutQuery, SuccessfulPayment, BufferedInputFile,
)

import database as db
from subscription import (
    is_admin, has_access, is_channel_subscribed, grant,
    SUBSCRIPTION_DAYS, CHANNEL_URL, CHANNEL_USERNAME,
)
import payment as pay
from tiktok_worker import TikTokWorker

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
STARS_PRICE = int(os.getenv("STARS_PRICE", "50"))
REFERRALS_REQUIRED = int(os.getenv("REFERRALS_REQUIRED", "10"))
REFERRAL_REWARD_DAYS = int(os.getenv("REFERRAL_REWARD_DAYS", "7"))
BOT_USERNAME = os.getenv("BOT_USERNAME", "Flame_TT_bot")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN не задан в .env")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

workers: dict[int, TikTokWorker] = {}


class AddAccountState(StatesGroup):
    waiting_username = State()
    waiting_password = State()


class PaymentState(StatesGroup):
    waiting_for_screenshot = State()


# ───────────── Клавиатуры ─────────────

def kb_main(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="👤 Мои аккаунты", callback_data="my_accounts")],
        [InlineKeyboardButton(text="➕ Добавить аккаунт", callback_data="add_account")],
        [InlineKeyboardButton(text="🔗 Рефералы", callback_data="referrals")],
        [InlineKeyboardButton(text="💳 Моя подписка", callback_data="my_subscription")],
        [InlineKeyboardButton(text="❓ Помощь", callback_data="help")],
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton(text="🛠 Админ-панель", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_subscribe_check() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Подписаться на канал", url=CHANNEL_URL)],
        [InlineKeyboardButton(text="✅ Проверить подписку", callback_data="check_sub")],
    ])


def kb_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="👥 Пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="📤 Экспорт", callback_data="admin_export")],
        [InlineKeyboardButton(text="🛑 Остановить всё", callback_data="admin_stop_all")],
        [InlineKeyboardButton(text="⚙️ Настройки", callback_data="admin_settings")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
    ])


# ───────────── Хендлеры ─────────────

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    try:
        await state.clear()
        args = message.text.split(maxsplit=1)
        referrer_id = None
        if len(args) > 1 and args[1].startswith("ref_"):
            try:
                referrer_id = int(args[1][4:])
            except ValueError:
                referrer_id = None

        await db.upsert_user(message.from_user.id, message.from_user.username,
                             message.from_user.first_name, referrer_id)

        # Регистрируем реферала (если новый)
        if referrer_id and referrer_id != message.from_user.id:
            created = await db.add_referral(message.from_user.id, referrer_id)
            if created:
                logger.info("Новый реферал: invited=%s referrer=%s",
                            message.from_user.id, referrer_id)

        if not is_admin(message.from_user.id) and not await is_channel_subscribed(
                message.bot, message.from_user.id):
            await message.answer(
                f"👋 Привет, {message.from_user.first_name}!\n\n"
                f"Для использования бота подпишись на канал:\n{CHANNEL_URL}",
                reply_markup=kb_subscribe_check(),
            )
            return

        # Если подписка на канал есть, но платной нет — подтвердим реферала (если был)
        if not is_admin(message.from_user.id):
            confirmed_now = await db.confirm_referral(message.from_user.id)
            if confirmed_now:
                await _maybe_reward(message.bot, message.from_user.id)

        status = "✅ подписка активна" if await has_access(
            message.bot, message.from_user.id) else "⛔ нет подписки"
        await message.answer(
            f"Привет, {message.from_user.first_name}!\n"
            f"Статус: {status}\n\nВыбери действие:",
            reply_markup=kb_main(message.from_user.id),
        )
    except Exception:
        logger.exception("Ошибка /start")


@router.callback_query(F.data == "check_sub")
async def cb_check_sub(callback: CallbackQuery):
    try:
        ok = await is_channel_subscribed(callback.bot, callback.from_user.id)
        if not ok:
            await callback.answer("❌ Подписка не найдена. Подпишись и повтори.", show_alert=True)
            return
        confirmed_now = await db.confirm_referral(callback.from_user.id)
        if confirmed_now:
            await _maybe_reward(callback.bot, callback.from_user.id)
        await callback.message.edit_text(
            "✅ Подписка подтверждена!", reply_markup=kb_main(callback.from_user.id)
        )
        await callback.answer()
    except Exception:
        logger.exception("Ошибка check_sub")
        await callback.answer("Ошибка", show_alert=True)


async def _maybe_reward(bot_: Bot, user_id: int):
    """Начисляет +7 дней, если подтверждённых рефералов >= порога и ещё не начисляли."""
    try:
        if await db.is_reward_granted(user_id):
            return
        cnt = await db.count_confirmed_referrals(user_id)
        if cnt >= REFERRALS_REQUIRED:
            await db.mark_reward_granted(user_id)
            await grant(user_id, REFERRAL_REWARD_DAYS)
            await bot_.send_message(
                user_id,
                f"🎉 У вас {cnt} подтверждённых рефералов! Начислено +{REFERRAL_REWARD_DAYS} дней подписки.",
            )
            logger.info("Реферальная награда выдана user=%s", user_id)
    except Exception:
        logger.exception("Ошибка начисления реферальной награды для %s", user_id)


@router.callback_query(F.data == "referrals")
async def cb_referrals(callback: CallbackQuery):
    try:
        uid = callback.from_user.id
        link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
        cnt = await db.count_confirmed_referrals(uid)
        granted = await db.is_reward_granted(uid)
        progress = "✅ награда получена" if granted else f"{cnt}/{REFERRALS_REQUIRED}"
        text = (
            f"🔗 Ваша реферальная ссылка:\n<code>{link}</code>\n\n"
            f"Приглашено (подписались на канал): <b>{cnt}</b>\n"
            f"До награды: <b>{progress}</b>\n\n"
            f"За {REFERRALS_REQUIRED} подтверждённых рефералов — +{REFERRAL_REWARD_DAYS} дней подписки."
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
        ])
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        logger.exception("Ошибка referrals")
    await callback.answer()


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer("Команды: /start, /help. Управление — через кнопки.")


@router.callback_query(F.data == "back_to_main")
async def cb_back(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Главное меню:", reply_markup=kb_main(callback.from_user.id))
    await callback.answer()


@router.callback_query(F.data == "help")
async def cb_help(callback: CallbackQuery):
    await callback.message.edit_text(
        "❓ Помощь\n\n"
        "Добавь аккаунт, запусти воркер. Без подписки функции заблокированы.\n"
        f"Канал: {CHANNEL_URL}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main"),
        ]]),
    )
    await callback.answer()


@router.callback_query(F.data == "my_subscription")
async def cb_my_sub(callback: CallbackQuery):
    try:
        sub = await db.get_subscription(callback.from_user.id)
        active = await has_access(callback.bot, callback.from_user.id)
        text = "✅ Подписка активна" if active else "⛔ Подписка не активна"
        if sub:
            text += f"\nДо: {sub['expires_at'][:10]}"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⭐ Оплатить Stars", callback_data="pay_stars")],
            [InlineKeyboardButton(text="💳 Оплатить картой", callback_data="pay_card")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
        ])
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        logger.exception("Ошибка my_subscription")
    await callback.answer()


@router.callback_query(F.data == "pay_stars")
async def cb_pay_stars(callback: CallbackQuery):
    try:
        await pay.send_stars_invoice(callback.bot, callback.from_user.id)
        await callback.answer("Инвойс отправлен")
    except Exception:
        logger.exception("Ошибка pay_stars")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data == "pay_card")
async def cb_pay_card(callback: CallbackQuery):
    await pay.send_card_details(callback.message)
    await callback.answer()


@router.pre_checkout_query()
async def on_pre_checkout(q: PreCheckoutQuery):
    try:
        await bot.answer_pre_checkout_query(q.id, ok=True)
        logger.info("PreCheckout OK user=%s", q.from_user.id)
    except Exception:
        logger.exception("PreCheckout error")


@router.message(F.successful_payment)
async def on_paid(message: Message):
    await pay.handle_successful_payment(message)


@router.callback_query(F.data == "add_account")
async def cb_add(callback: CallbackQuery, state: FSMContext):
    if not await has_access(callback.bot, callback.from_user.id):
        await callback.answer("⛔ Требуется подписка", show_alert=True)
        return
    await state.set_state(AddAccountState.waiting_username)
    await callback.message.edit_text("Введите username TikTok:")
    await callback.answer()


@router.message(AddAccountState.waiting_username)
async def add_username(message: Message, state: FSMContext):
    await state.update_data(username=message.text.strip())
    await state.set_state(AddAccountState.waiting_password)
    await message.answer("Введите пароль TikTok:")


@router.message(AddAccountState.waiting_password)
async def add_password(message: Message, state: FSMContext):
    data = await state.get_data()
    await state.clear()
    try:
        await db.add_account(message.from_user.id, data["username"], message.text.strip())
        await message.answer("✅ Аккаунт добавлен.", reply_markup=kb_main(message.from_user.id))
    except Exception:
        logger.exception("Ошибка добавления аккаунта")
        await message.answer("⚠️ Ошибка.")


@router.callback_query(F.data == "my_accounts")
async def cb_my_accounts(callback: CallbackQuery):
    try:
        accounts = await db.get_user_accounts(callback.from_user.id)
        if not accounts:
            await callback.message.edit_text(
                "У вас нет аккаунтов.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main"),
                ]]),
            )
            await callback.answer()
            return
        rows = []
        for a in accounts:
            status = "🟢" if a["is_running"] else "⚪"
            rows.append([InlineKeyboardButton(
                text=f"{status} @{a['username']}",
                callback_data=f"account_{a['id']}",
            )])
        rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")])
        await callback.message.edit_text("Ваши аккаунты:",
                                         reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    except Exception:
        logger.exception("Ошибка my_accounts")
    await callback.answer()


@router.callback_query(F.data.startswith("account_"))
async def cb_account(callback: CallbackQuery):
    try:
        acc_id = int(callback.data.split("_", 1)[1])
        acc = await db.get_account(acc_id)
        if not acc or acc["user_id"] != callback.from_user.id:
            await callback.answer("Не найдено", show_alert=True)
            return
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="▶️ Старт", callback_data=f"start_{acc_id}"),
             InlineKeyboardButton(text="⏹ Стоп", callback_data=f"stop_{acc_id}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_{acc_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="my_accounts")],
        ])
        await callback.message.edit_text(
            f"Аккаунт @{acc['username']}\nСтатус: {'запущен' if acc['is_running'] else 'остановлен'}",
            reply_markup=kb,
        )
    except Exception:
        logger.exception("Ошибка account_")
    await callback.answer()


@router.callback_query(F.data.startswith("start_"))
async def cb_start(callback: CallbackQuery):
    if not await has_access(callback.bot, callback.from_user.id):
        await callback.answer("⛔ Требуется подписка", show_alert=True)
        return
    try:
        acc_id = int(callback.data.split("_", 1)[1])
        acc = await db.get_account(acc_id)
        if not acc or acc["user_id"] != callback.from_user.id:
            await callback.answer("Не найдено", show_alert=True)
            return
        worker = workers.get(acc_id) or TikTokWorker(acc_id, acc["username"], acc["password"])
        workers[acc_id] = worker
        await worker.start()
        await db.set_account_running(acc_id, True)
        await callback.answer("Запущен", show_alert=True)
    except Exception:
        logger.exception("Ошибка start_")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("stop_"))
async def cb_stop(callback: CallbackQuery):
    try:
        acc_id = int(callback.data.split("_", 1)[1])
        acc = await db.get_account(acc_id)
        if not acc or acc["user_id"] != callback.from_user.id:
            await callback.answer("Не найдено", show_alert=True)
            return
        worker = workers.get(acc_id)
        if worker:
            await worker.stop()
            workers.pop(acc_id, None)
        await db.set_account_running(acc_id, False)
        await callback.answer("Остановлен", show_alert=True)
    except Exception:
        logger.exception("Ошибка stop_")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data.startswith("delete_"))
async def cb_delete(callback: CallbackQuery):
    try:
        acc_id = int(callback.data.split("_", 1)[1])
        worker = workers.pop(acc_id, None)
        if worker:
            await worker.stop()
        ok = await db.delete_account(acc_id, callback.from_user.id)
        await callback.answer("Удалён" if ok else "Не найдено", show_alert=True)
        await cb_my_accounts(callback)
    except Exception:
        logger.exception("Ошибка delete_")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data == "status")
async def cb_status(callback: CallbackQuery):
    running = sum(1 for w in workers.values() if w.is_running)
    await callback.answer(f"Активных воркеров: {running}", show_alert=True)


# ───────────── Админка ─────────────

@router.callback_query(F.data == "admin_panel")
async def cb_admin(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    await callback.message.edit_text("🛠 Админ-панель", reply_markup=kb_admin())
    await callback.answer()


@router.callback_query(F.data == "admin_stats")
async def cb_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    try:
        users = await db.count_users()
        accs = await db.count_accounts()
        pend = await db.count_payments("pending")
        await callback.message.edit_text(
            f"📊 Статистика\nПользователей: {users}\nАккаунтов: {accs}\n"
            f"Ожидают оплаты: {pend}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel"),
            ]]),
        )
    except Exception:
        logger.exception("Ошибка stats")
    await callback.answer()


@router.callback_query(F.data == "admin_users")
async def cb_users(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    try:
        users = await db.get_all_users()
        lines = [f"<code>{u['user_id']}</code> @{u['username'] or '-'}" for u in users[:30]]
        text = "👥 Пользователи:\n" + ("\n".join(lines) if lines else "нет")
        await callback.message.edit_text(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel"),
            ]]),
        )
    except Exception:
        logger.exception("Ошибка users")
    await callback.answer()


@router.callback_query(F.data == "admin_export")
async def cb_export(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    try:
        users = await db.get_all_users()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["user_id", "username", "first_name", "referrer_id", "created_at"])
        for u in users:
            w.writerow([u["user_id"], u["username"], u["first_name"],
                        u.get("referrer_id"), u["created_at"]])
        data = buf.getvalue().encode("utf-8")
        file = BufferedInputFile(data, filename=f"users_{datetime.utcnow():%Y%m%d}.csv")
        await callback.bot.send_document(callback.from_user.id, file)
        await callback.answer("Отправлено")
    except Exception:
        logger.exception("Ошибка export")
        await callback.answer("Ошибка", show_alert=True)


@router.callback_query(F.data == "admin_stop_all")
async def cb_stop_all(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    try:
        for w in list(workers.values()):
            await w.stop()
        workers.clear()
        await db.stop_all_accounts()
        await callback.answer("Все воркеры остановлены", show_alert=True)
    except Exception:
        logger.exception("Ошибка stop_all")


@router.callback_query(F.data == "admin_settings")
async def cb_settings(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    card = await db.get_setting("card_number", "—")
    holder = await db.get_setting("card_holder", "—")
    await callback.message.edit_text(
        f"⚙️ Настройки\n\nКарта: <code>{card}</code>\nПолучатель: {holder}\n\n"
        f"Изменить: /card &lt;номер&gt; или /holder &lt;имя&gt;",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel"),
        ]]),
    )
    await callback.answer()


@router.message(Command("card"))
async def cmd_set_card(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /card 0000 0000 0000 0000")
        return
    await db.set_setting("card_number", parts[1].strip())
    await message.answer("✅ Номер карты сохранён.")


@router.message(Command("holder"))
async def cmd_set_holder(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /holder Иван И.")
        return
    await db.set_setting("card_holder", parts[1].strip())
    await message.answer("✅ Получатель сохранён.")


@router.callback_query(F.data.startswith("admin_confirm_"))
async def cb_confirm(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    pid = int(callback.data.rsplit("_", 1)[1])
    await pay.confirm_payment(callback, pid)


@router.callback_query(F.data.startswith("admin_reject_"))
async def cb_reject(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    pid = int(callback.data.rsplit("_", 1)[1])
    await pay.reject_payment(callback, pid)


@router.message(PaymentState.waiting_for_screenshot, F.photo)
async def receive_screenshot(message: Message, state: FSMContext):
    try:
        await pay.handle_screenshot(message, message.photo[-1].file_id)
        await state.clear()
    except Exception:
        logger.exception("Ошибка приёма скриншота")
        await message.answer("⚠️ Ошибка.")


# ───────────── Запуск ─────────────

async def main():
    await db.init_db()
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info("Бот стартует")
    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
