"""Сбор номерков по подписке с фильтром по времени приёма.

Фильтр применяется до сравнения с историей: номерки вне выбранного окна
не считаются найденными вообще, поэтому и уведомлений по ним не будет.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import time

from app.config import settings
from app.gorzdrav import api, now_msk

DAY_START = time(0, 0)
DAY_END = time(23, 59)


def parse_time(raw: str | None, fallback: time) -> time:
    try:
        hours, minutes = str(raw).split(":")
        return time(int(hours), int(minutes))
    except (ValueError, AttributeError):
        return fallback


def in_window(moment: time, start: time, end: time) -> bool:
    """Окно через полночь (например 22:00–06:00) тоже поддерживается."""
    if start <= end:
        return start <= moment <= end
    return moment >= start or moment <= end


def describe_window(time_from: str, time_to: str) -> str:
    start = parse_time(time_from, DAY_START)
    end = parse_time(time_to, DAY_END)

    if start == DAY_START and end == DAY_END:
        return "любое время"
    if start == DAY_START:
        return f"до {end:%H:%M}"
    if end == DAY_END:
        return f"с {start:%H:%M}"
    return f"с {start:%H:%M} до {end:%H:%M}"


async def _cached(
    cache: dict | None,
    key: tuple,
    factory: Callable[[], Awaitable[list[dict]]],
) -> list[dict]:
    """Один ответ горздрава на цикл проверки — подписки часто пересекаются."""
    if cache is None:
        return await factory()
    if key not in cache:
        cache[key] = await factory()
        await asyncio.sleep(settings.REQUEST_DELAY)
    return cache[key]


async def collect_slots(
    lpu_id: str,
    speciality_id: str,
    doctor_id: str | None,
    time_from: str,
    time_to: str,
    cache: dict | None = None,
) -> list[dict]:
    """Свободные номерки, попадающие в окно времени, сгруппированные по врачам."""
    start = parse_time(time_from, DAY_START)
    end = parse_time(time_to, DAY_END)
    now = now_msk()

    doctors = await _cached(
        cache,
        ("doctors", lpu_id, speciality_id),
        lambda: api.doctors(lpu_id, speciality_id),
    )

    if doctor_id:
        doctors = [doc for doc in doctors if doc["id"] == doctor_id]
    else:
        doctors = [doc for doc in doctors if doc["free_tickets"] > 0]

    found = []
    for doctor in doctors:
        appointments = await _cached(
            cache,
            ("appointments", lpu_id, doctor["id"]),
            lambda doc_id=doctor["id"]: api.appointments(lpu_id, doc_id),
        )

        slots = [
            {
                "key": appointment["start"].strftime("%Y-%m-%dT%H:%M"),
                "start": appointment["start"],
                "room": appointment["room"],
            }
            for appointment in appointments
            if appointment["start"] > now and in_window(appointment["start"].time(), start, end)
        ]

        if slots:
            found.append({
                "doctor_id": doctor["id"],
                "doctor_name": doctor["name"],
                "slots": slots,
            })

    return found


def collect_referral_slots(referral: dict, time_from: str, time_to: str) -> list[dict]:
    """То же окно времени, но для направления — слоты приходят вложенными в ответ.

    Форма результата совпадает с collect_slots, поэтому уведомления и
    сериализация для мини-приложения работают без изменений.
    """
    start = parse_time(time_from, DAY_START)
    end = parse_time(time_to, DAY_END)
    now = now_msk()

    found = []
    for speciality in referral["specialities"]:
        for doctor in speciality["doctors"]:
            slots = [
                {
                    "key": slot["start"].strftime("%Y-%m-%dT%H:%M"),
                    "start": slot["start"],
                    "room": slot["room"],
                    "appointment_id": slot["id"],
                    "address": slot["address"],
                    "number": slot["number"],
                }
                for slot in doctor["slots"]
                if slot["start"] > now and in_window(slot["start"].time(), start, end)
            ]
            if slots:
                found.append({
                    "doctor_id": doctor["id"],
                    "doctor_name": doctor["name"],
                    "speciality_name": speciality["name"],
                    "slots": slots,
                })

    return found
