"""Фоновый мониторинг подписок и направлений."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from app.bot import referral_header, send_slots_message, subscription_header
from app.config import settings
from app.database import (
    deactivate_user_referrals,
    deactivate_user_subscriptions,
    get_active_referrals,
    get_active_subscriptions,
    save_check_result,
    save_referral_check,
)
from app.gorzdrav import GorzdravError, api
from app.services.slots import collect_referral_slots, collect_slots, describe_window

logger = logging.getLogger(__name__)


async def check_once() -> None:
    cache: dict = {}
    await _check_subscriptions(cache)
    await _check_referrals()


# ── подписки на расписание ──────────────────────────────────────────────────

async def _check_subscriptions(cache: dict) -> None:
    subscriptions = await get_active_subscriptions()
    if not subscriptions:
        return

    logger.info("Проверяю подписок: %d", len(subscriptions))
    for subscription in subscriptions:
        try:
            found = await collect_slots(
                subscription["lpu_id"],
                subscription["speciality_id"],
                subscription["doctor_id"],
                subscription["time_from"],
                subscription["time_to"],
                cache,
            )
            await _diff_and_notify(
                entity=subscription,
                found=found,
                header=subscription_header(subscription),
                save=save_check_result,
                deactivate=deactivate_user_subscriptions,
            )
        except Exception:
            logger.exception("Подписка %s: проверка не удалась", subscription["id"])


# ── направления ─────────────────────────────────────────────────────────────

async def _check_referrals() -> None:
    referrals = await get_active_referrals()
    if not referrals:
        return

    logger.info("Проверяю направлений: %d", len(referrals))
    for referral in referrals:
        try:
            data = await api.referral(referral["number"], referral["last_name"])
        except GorzdravError as exc:
            # Направление могло быть погашено или отозвано — не спамим стектрейсом.
            logger.info("Направление %s недоступно: %s", referral["id"], exc)
            continue
        except Exception:
            logger.exception("Направление %s: проверка не удалась", referral["id"])
            continue

        try:
            found = collect_referral_slots(data, referral["time_from"], referral["time_to"])
            await _diff_and_notify(
                entity=referral,
                found=found,
                header=referral_header(referral),
                save=save_referral_check,
                deactivate=deactivate_user_referrals,
            )
        except Exception:
            logger.exception("Направление %s: обработка не удалась", referral["id"])

        await asyncio.sleep(settings.REQUEST_DELAY)


# ── общая часть ─────────────────────────────────────────────────────────────

async def _diff_and_notify(
    entity: dict,
    found: list[dict],
    header: list[str],
    save: Callable[[int, str, bool], Awaitable[None]],
    deactivate: Callable[[int], Awaitable[None]],
) -> None:
    try:
        history: dict = json.loads(entity["last_check_data"] or "{}")
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
        await save(entity["id"], json.dumps(snapshot), False)
        return

    window = describe_window(entity["time_from"], entity["time_to"])
    if not await _notify(entity["user_id"], header, window, fresh, deactivate):
        # Снимок не сохраняем: иначе номерки осядут в истории как уже
        # показанные и о них никто не узнает. Повторим на следующем круге.
        return

    await save(entity["id"], json.dumps(snapshot), True)


async def _notify(
    user_id: int,
    header: list[str],
    window: str,
    doctors: list[dict],
    deactivate: Callable[[int], Awaitable[None]],
) -> bool:
    try:
        await send_slots_message(user_id, header, window, doctors)
        return True
    except TelegramRetryAfter as exc:
        logger.warning("Телеграм просит подождать %s c", exc.retry_after)
        await asyncio.sleep(exc.retry_after)
        try:
            await send_slots_message(user_id, header, window, doctors)
            return True
        except Exception:
            logger.exception("Повторная отправка пользователю %s не удалась", user_id)
    except TelegramForbiddenError:
        logger.warning("Пользователь %s заблокировал бота — отключаю наблюдение", user_id)
        await deactivate(user_id)
    except Exception:
        logger.exception("Не смог отправить уведомление пользователю %s", user_id)
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
