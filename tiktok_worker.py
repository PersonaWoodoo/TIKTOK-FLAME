"""
Каркас воркера TikTok.

ВНИМАНИЕ:
- _has_streak и _send_stickers_to_streak_chats НЕ реализуются.
  Автоматическая рассылка стикеров в Streak-чаты нарушает ToS TikTok
  и создаёт риск блокировки аккаунтов пользователей.
- Остальные методы реализованы как безопасный каркас.
"""

import logging
import asyncio
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


class TikTokWorker:
    def __init__(self, account_id: int, username: str, password: str):
        self.account_id = account_id
        self.username = username
        self.password = password
        self.is_running = False
        self._task: asyncio.Task | None = None
        self.last_sticker_time: datetime | None = None

    async def start(self) -> None:
        if self.is_running:
            logger.info("Воркер %s уже запущен", self.account_id)
            return
        self.is_running = True
        self._task = asyncio.create_task(self._run())
        logger.info("Воркер %s запущен", self.account_id)

    async def stop(self) -> None:
        self.is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("Воркер %s остановлен", self.account_id)

    async def _run(self) -> None:
        try:
            ok = await self._login()
            if not ok:
                logger.warning("Воркер %s: вход не удался", self.account_id)
                self.is_running = False
                return
            while self.is_running:
                try:
                    await self._check_and_reply_messages()
                    now = datetime.utcnow()
                    if (self.last_sticker_time is None or
                            now - self.last_sticker_time >= timedelta(hours=11)):
                        try:
                            await self._send_stickers_to_streak_chats()
                        except NotImplementedError as e:
                            logger.warning("Воркер %s: %s", self.account_id, e)
                        self.last_sticker_time = now
                except Exception:
                    logger.exception("Воркер %s: ошибка в цикле", self.account_id)
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info("Воркер %s отменён", self.account_id)
            raise
        except Exception:
            logger.exception("Воркер %s: критическая ошибка", self.account_id)
        finally:
            self.is_running = False

    async def _login(self) -> bool:
        logger.info("Воркер %s: попытка входа (каркас)", self.account_id)
        await asyncio.sleep(0.1)
        return True

    async def _has_streak(self, chat) -> bool:
        raise NotImplementedError(
            "Автоматизация Streak нарушает ToS TikTok — не реализуется."
        )

    async def _is_chat_active(self, chat) -> bool:
        raise NotImplementedError(
            "Требует Playwright-интеграции с TikTok — не реализуется."
        )

    async def _check_and_reply_messages(self) -> None:
        logger.debug("Воркер %s: проверка сообщений (каркас)", self.account_id)

    async def _send_stickers_to_streak_chats(self) -> None:
        raise NotImplementedError(
            "Автоматическая рассылка стикеров в Streak-чаты нарушает ToS TikTok "
            "и создаёт риск блокировки аккаунтов пользователей — не реализуется."
        )
