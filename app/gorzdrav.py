"""Клиент неофициального API gorzdrav.spb.ru.

Горздрав отдаёт наивные даты по московскому времени, а идентификаторы —
то строками, то числами. Клиент приводит и то и другое к единому виду,
чтобы остальной код об этом не думал.
"""

from __future__ import annotations

import asyncio
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


class GorzdravError(Exception):
    """Горздрав отказал. Текст пригоден для показа пользователю."""


# МИС поликлиники не отвечает. Сообщение горздрава на этот код длинное и
# звучит как проблема приложения, хотя сайт горздрава отказывает так же.
MIS_UNAVAILABLE = 616


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
        # Одноразовый токен, который горздрав кладёт в заголовок ответа на
        # обычные GET и требует обратно при создании и отмене записи.
        self._token: str = ""

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

    async def _request(self, method: str, path: str, **kwargs) -> dict | None:
        # Горздрав изредка обрывает соединение под нагрузкой. Одна повторная
        # попытка убирает почти все такие сбои. Создание записи не повторяем:
        # запрос мог дойти, и второй попыткой можно занять два номерка.
        attempts = 1 if method == "POST" else 2

        for attempt in range(1, attempts + 1):
            try:
                response = await self.client.request(method, path, **kwargs)
                response.raise_for_status()
                token = response.headers.get("token")
                if token:
                    self._token = token
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                if attempt == attempts:
                    logger.warning("Запрос %s %s не удался: %s", method, path, exc)
                    return None
                logger.debug("Повтор %s %s после ошибки: %s", method, path, exc)
                await asyncio.sleep(0.7)
        return None

    async def _get(self, path: str) -> list[dict]:
        payload = await self._request("GET", path)
        if payload is None:
            return []

        # success=false горздрав отдаёт и на штатное «свободных талонов нет»,
        # так что это не повод шуметь в логах — уровень отладочный.
        if not payload.get("success", True):
            logger.debug("Горздрав на %s: %s", path, payload.get("message"))
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

    # ── Направления ─────────────────────────────────────────────────────────

    async def referral(self, number: str, last_name: str) -> dict:
        """Направление по номеру и фамилии.

        В отличие от свободного расписания, слоты приходят вложенными в ответ,
        а visitStart — в UTC с суффиксом Z.
        """
        payload = await self._request(
            "GET", f"/referral/{number}", params={"lastName": last_name}
        )
        if payload is None:
            raise GorzdravError("Горздрав не отвечает, попробуйте позже")

        if not payload.get("success", True) or not payload.get("result"):
            raise GorzdravError(
                payload.get("message") or "Направление не найдено — проверьте номер и фамилию"
            )

        data = _normalize_referral(payload["result"])

        # Горздрав игнорирует lastName и отдаёт карточку пациента по одному лишь
        # номеру направления. Сверяем сами, чтобы приложение не облегчало доступ
        # к чужим персональным данным.
        if _fold(last_name) != _fold(data["last_name"]):
            raise GorzdravError("Направление не найдено — проверьте номер и фамилию")
        return data

    async def create_appointment(self, body: dict) -> None:
        """Создаёт запись к врачу. Молча возвращается при успехе.

        Токен обязателен и одноразовый, поэтому вызову должен предшествовать
        свежий запрос направления — он и кладёт токен в заголовок ответа.
        """
        payload = await self._request(
            "POST", "/appointment/create", json=body, headers={"token": self._token}
        )
        if payload is None:
            raise GorzdravError("Горздрав не отвечает — запись не создана")
        if not payload.get("success", True):
            if payload.get("errorCode") == MIS_UNAVAILABLE:
                raise GorzdravError(
                    "Поликлиника не принимает записи — сбой в её системе. "
                    "На сайте горздрава запись сейчас тоже не проходит."
                )
            raise GorzdravError(payload.get("message") or "Горздрав отклонил запись")
        self._token = ""  # токен погашен, следующий придёт со следующим GET

    async def cancel_appointment(self, appointment_id: str, lpu_id: str, patient_id: str) -> None:
        payload = await self._request("POST", "/appointment/cancel", json={
            "appointmentId": appointment_id,
            "lpuId": lpu_id,
            "patientId": patient_id,
            "esiaId": "",
        }, headers={"token": self._token})
        if payload is None:
            raise GorzdravError("Горздрав не отвечает — запись не отменена")
        if not payload.get("success", True):
            raise GorzdravError(payload.get("message") or "Горздрав отклонил отмену")
        self._token = ""


def _fold(name: str) -> str:
    """Фамилии для сравнения: без регистра, пробелов и разницы ё/е."""
    return (name or "").strip().lower().replace("ё", "е")


def _normalize_referral(result: dict) -> dict:
    specialities = []
    for speciality in result.get("specialities") or []:
        doctors = []
        for doctor in speciality.get("doctors") or []:
            slots = []
            for appointment in doctor.get("appointments") or []:
                start = parse_visit(appointment.get("visitStart"))
                if start is None:
                    continue
                slots.append({
                    "id": str(appointment.get("id") or ""),
                    "start": start,
                    # Сырое значение нужно для visitDate при создании записи —
                    # отдаём горздраву ровно то, что он прислал.
                    "raw_start": appointment.get("visitStart"),
                    "room": appointment.get("room"),
                    "number": appointment.get("number") or 0,
                    "address": (appointment.get("address") or "").strip(),
                })
            slots.sort(key=lambda slot: slot["start"])
            doctors.append({
                "id": str(doctor.get("id") or ""),
                "name": (doctor.get("name") or "Врач").strip(),
                "note": (doctor.get("description") or "").strip(),
                "slots": slots,
            })
        specialities.append({
            "id": str(speciality.get("id") or ""),
            "name": (speciality.get("name") or "Специальность").strip(),
            "doctors": doctors,
        })

    return {
        "lpu_id": str(result.get("lpuId") or ""),
        "lpu_name": (result.get("lpuShortName") or result.get("lpuFullName") or "").strip(),
        "lpu_address": (result.get("lpuAddress") or "").strip(),
        "lpu_phone": (result.get("lpuPhone") or "").strip(),
        "patient_id": str(result.get("patId") or ""),
        "last_name": (result.get("lastName") or "").strip(),
        "first_name": (result.get("firstName") or "").strip(),
        "middle_name": (result.get("middleName") or "").strip(),
        "birthdate": result.get("birthDate") or "",
        "specialities": specialities,
    }


api = GorzdravAPI()
