"""Клиент неофициального API gorzdrav.spb.ru.

Горздрав отдаёт наивные даты по московскому времени, а идентификаторы —
то строками, то числами. Клиент приводит и то и другое к единому виду,
чтобы остальной код об этом не думал.
"""

from __future__ import annotations

import logging
from datetime import datetime

import httpx
import pytz

from app.config import settings

logger = logging.getLogger(__name__)

MSK = pytz.timezone("Europe/Moscow")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://gorzdrav.spb.ru/service-free-schedule",
}


def now_msk() -> datetime:
    return datetime.now(MSK)


def parse_visit(raw: str | None) -> datetime | None:
    """Разбирает visitStart. Дата без таймзоны считается московской."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        return MSK.localize(dt)
    return dt.astimezone(MSK)


class GorzdravAPI:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=settings.GORZDRAV_BASE,
                headers=HEADERS,
                timeout=httpx.Timeout(15.0),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

    async def _get(self, path: str) -> list[dict]:
        try:
            response = await self.client.get(path)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Запрос %s не удался: %s", path, exc)
            return []

        if not payload.get("success", True):
            logger.warning("Горздрав вернул ошибку на %s: %s", path, payload.get("message"))
        return payload.get("result") or []

    async def districts(self) -> list[dict]:
        return [
            {"id": str(item["id"]), "name": item.get("name") or "Без названия"}
            for item in await self._get("/shared/districts")
            if item.get("id") is not None
        ]

    async def lpus(self, district_id: str) -> list[dict]:
        result = []
        for item in await self._get(f"/shared/district/{district_id}/lpus"):
            if item.get("id") is None or item.get("isActive") is False:
                continue
            name = item.get("lpuShortName") or item.get("lpuFullName") or f"ЛПУ #{item['id']}"
            result.append({
                "id": str(item["id"]),
                "name": name.strip(),
                "full_name": (item.get("lpuFullName") or "").strip(),
                "address": (item.get("address") or "").strip(),
            })
        return result

    async def specialities(self, lpu_id: str) -> list[dict]:
        result = []
        for item in await self._get(f"/schedule/lpu/{lpu_id}/specialties"):
            if not item.get("id"):
                continue
            result.append({
                "id": str(item["id"]),
                "name": (item.get("name") or "Без названия").strip(),
                "free_tickets": item.get("countFreeTicket") or 0,
                "nearest_date": item.get("nearestDate"),
            })
        return result

    async def doctors(self, lpu_id: str, speciality_id: str) -> list[dict]:
        result = []
        path = f"/schedule/lpu/{lpu_id}/speciality/{speciality_id}/doctors"
        for item in await self._get(path):
            if not item.get("id"):
                continue
            result.append({
                "id": str(item["id"]),
                "name": (item.get("name") or "Врач").strip(),
                "free_tickets": item.get("freeTicketCount") or 0,
                "nearest_date": item.get("nearestDate"),
                "room": item.get("ariaNumber") or "",
            })
        return result

    async def appointments(self, lpu_id: str, doctor_id: str) -> list[dict]:
        """Свободные номерки врача. Эндпоинт отдаёт только доступные слоты."""
        result = []
        path = f"/schedule/lpu/{lpu_id}/doctor/{doctor_id}/appointments"
        for item in await self._get(path):
            start = parse_visit(item.get("visitStart"))
            if start is None:
                continue
            result.append({
                "id": str(item.get("id") or ""),
                "start": start,
                "room": item.get("room") or "",
            })
        result.sort(key=lambda slot: slot["start"])
        return result


api = GorzdravAPI()
