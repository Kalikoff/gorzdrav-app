"""Фоновый мониторинг подписок."""

from __future__ import annotations

import asyncio
import json
import logging

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from app.bot import send_slots_message
from app.config import settings
from app.database import (
    deactivate_user_subscriptions,
    get_active_subscriptions,
    save_check_result,
)
from app.services.slots import collect_slots

logger = logging.getLogger(__name__)


async def check_once() -> None:
    subscriptions = await get_active_subscriptions()
    if not subscriptions:
        return

    logger.info("Проверяю подписок: %d", len(subscriptions))
    cache: dict = {}

    for subscription in subscriptions:
        try:
            await _check_subscription(subscription, cache)
        except Exception:
            logger.exception("Подписка %s: проверка не удалась", subscription["id"])


async def _check_subscription(subscription: dict, cache: dict) -> None:
    found = await collect_slots(
        subscription["lpu_id"],
        subscription["speciality_id"],
        subscription["doctor_id"],
        subscription["time_from"],
        subscription["time_to"],
        cache,
    )

    try:
        history: dict = json.loads(subscription["last_check_data"] or "{}")
    except ValueError:
        history = {}

    fresh: list[dict] = []
    snapshot: dict[str, list[str]] = {}

    for doctor in found:
        snapshot[doctor["doctor_id"]] = [slot["key"] for slot in doctor["slots"]]
        known = set(history.get(doctor["doctor_id"]) or [])
        new_slots = [slot for slot in doctor["slots"] if slot["key"] not in known]
        if new_slots:
            fresh.append({**doctor, "slots": new_slots})

    if not fresh:
        await save_check_result(subscription["id"], json.dumps(snapshot), notified=False)
        return

    if not await _notify(subscription, fresh):
        # Снимок не сохраняем: иначе номерки осядут в истории как уже
        # показанные и о них никто не узнает. Повторим на следующем круге.
        return

    await save_check_result(subscription["id"], json.dumps(snapshot), notified=True)


async def _notify(subscription: dict, doctors: list[dict]) -> bool:
    try:
        await send_slots_message(subscription, doctors)
        return True
    except TelegramRetryAfter as exc:
        logger.warning("Телеграм просит подождать %s c", exc.retry_after)
        await asyncio.sleep(exc.retry_after)
        try:
            await send_slots_message(subscription, doctors)
            return True
        except Exception:
            logger.exception("Повторная отправка пользователю %s не удалась", subscription["user_id"])
    except TelegramForbiddenError:
        logger.warning("Пользователь %s заблокировал бота — отключаю его подписки", subscription["user_id"])
        await deactivate_user_subscriptions(subscription["user_id"])
    except Exception:
        logger.exception("Не смог отправить уведомление пользователю %s", subscription["user_id"])
    return False


async def run_checker() -> None:
    logger.info("Мониторинг запущен, интервал %d с", settings.CHECK_INTERVAL)
    while True:
        try:
            await check_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Цикл проверки завершился ошибкой")
        await asyncio.sleep(settings.CHECK_INTERVAL)
