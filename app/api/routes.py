import logging

from fastapi import APIRouter, HTTPException

from app.api.auth import UserId
from app.api.schemas import (
    BookingCreate,
    FavoriteCreate,
    ReferralLookup,
    ReferralUpdate,
    ReferralWatch,
    SlotsPreview,
    SubscriptionCreate,
    SubscriptionUpdate,
)
from app.database import (
    add_booking,
    add_favorite,
    add_subscription,
    delete_all_subscriptions,
    delete_favorite,
    delete_referral,
    delete_subscription,
    get_booking,
    get_bookings,
    get_favorites,
    get_referral,
    get_subscription,
    get_user_referrals,
    get_user_subscriptions,
    mark_booking_cancelled,
    save_referral,
    update_referral,
    update_subscription,
)
from app.gorzdrav import GorzdravError, api, now_msk
from app.services.slots import collect_referral_slots, collect_slots, describe_window

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _serialize_subscription(row: dict) -> dict:
    return {
        "id": row["id"],
        "district_name": row["district_name"],
        "lpu_id": row["lpu_id"],
        "lpu_name": row["lpu_name"] or f"ЛПУ #{row['lpu_id']}",
        "speciality_id": row["speciality_id"],
        "speciality_name": row["speciality_name"],
        "doctor_id": row["doctor_id"],
        "doctor_name": row["doctor_name"],
        "time_from": row["time_from"],
        "time_to": row["time_to"],
        "time_label": describe_window(row["time_from"], row["time_to"]),
        "is_active": bool(row["is_active"]),
        "last_notified_at": row["last_notified_at"],
    }


def _serialize_slots(found: list[dict]) -> dict:
    return {
        "checked_at": now_msk().isoformat(),
        "total": sum(len(doctor["slots"]) for doctor in found),
        "doctors": [
            {
                "id": doctor["doctor_id"],
                "name": doctor["doctor_name"],
                "slots": [
                    {
                        "date": slot["start"].strftime("%Y-%m-%d"),
                        "time": slot["start"].strftime("%H:%M"),
                        "room": slot["room"],
                    }
                    for slot in doctor["slots"]
                ],
            }
            for doctor in found
        ],
    }


# ── служебное ───────────────────────────────────────────────────────────────

@router.get("/health")
async def health() -> dict:
    return {"ok": True}


# ── справочники горздрава ───────────────────────────────────────────────────

@router.get("/districts")
async def districts(_: UserId) -> list[dict]:
    return await api.districts()


@router.get("/districts/{district_id}/lpus")
async def lpus(district_id: str, _: UserId) -> list[dict]:
    return await api.lpus(district_id)


@router.get("/lpus/{lpu_id}/specialities")
async def specialities(lpu_id: str, _: UserId) -> list[dict]:
    return await api.specialities(lpu_id)


@router.get("/lpus/{lpu_id}/specialities/{speciality_id}/doctors")
async def doctors(lpu_id: str, speciality_id: str, _: UserId) -> list[dict]:
    return await api.doctors(lpu_id, speciality_id)


# ── подписки ────────────────────────────────────────────────────────────────

@router.get("/subscriptions")
async def list_subscriptions(user_id: UserId) -> list[dict]:
    rows = await get_user_subscriptions(user_id)
    return [_serialize_subscription(row) for row in rows]


@router.post("/subscriptions")
async def create_subscription(body: SubscriptionCreate, user_id: UserId) -> dict:
    subscription_id, created = await add_subscription(user_id, body.model_dump())
    await add_favorite(user_id, body.lpu_id, body.lpu_name)
    return {"id": subscription_id, "created": created}


@router.patch("/subscriptions/{subscription_id}")
async def edit_subscription(
    subscription_id: int, body: SubscriptionUpdate, user_id: UserId
) -> dict:
    changes = body.model_dump(exclude_none=True)
    if "is_active" in changes:
        changes["is_active"] = int(changes["is_active"])

    if not changes:
        raise HTTPException(status_code=400, detail="Нечего обновлять")

    if not await update_subscription(subscription_id, user_id, changes):
        raise HTTPException(status_code=404, detail="Подписка не найдена")

    row = await get_subscription(subscription_id, user_id)
    return _serialize_subscription(row)


@router.delete("/subscriptions/{subscription_id}")
async def remove_subscription(subscription_id: int, user_id: UserId) -> dict:
    if not await delete_subscription(subscription_id, user_id):
        raise HTTPException(status_code=404, detail="Подписка не найдена")
    return {"success": True}


@router.delete("/subscriptions")
async def remove_all_subscriptions(user_id: UserId) -> dict:
    return {"success": True, "deleted": await delete_all_subscriptions(user_id)}


# ── номерки ─────────────────────────────────────────────────────────────────

@router.get("/subscriptions/{subscription_id}/slots")
async def subscription_slots(subscription_id: int, user_id: UserId) -> dict:
    row = await get_subscription(subscription_id, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Подписка не найдена")

    found = await collect_slots(
        row["lpu_id"],
        row["speciality_id"],
        row["doctor_id"],
        row["time_from"],
        row["time_to"],
    )
    return _serialize_slots(found)


@router.post("/slots/preview")
async def preview_slots(body: SlotsPreview, _: UserId) -> dict:
    """Показывает номерки до создания подписки — чтобы проверить фильтр."""
    found = await collect_slots(
        body.lpu_id,
        body.speciality_id,
        body.doctor_id,
        body.time_from,
        body.time_to,
    )
    return _serialize_slots(found)


# ── избранные поликлиники ───────────────────────────────────────────────────

@router.get("/favorites")
async def list_favorites(user_id: UserId) -> list[dict]:
    return await get_favorites(user_id)


@router.post("/favorites")
async def create_favorite(body: FavoriteCreate, user_id: UserId) -> dict:
    await add_favorite(user_id, body.lpu_id, body.lpu_name)
    return {"success": True}


@router.delete("/favorites/{lpu_id}")
async def remove_favorite(lpu_id: str, user_id: UserId) -> dict:
    if not await delete_favorite(user_id, lpu_id):
        raise HTTPException(status_code=404, detail="Не найдено в избранном")
    return {"success": True}


# ── направления ─────────────────────────────────────────────────────────────

def _serialize_referral(referral: dict) -> dict:
    return {
        "lpu_id": referral["lpu_id"],
        "lpu_name": referral["lpu_name"],
        "lpu_address": referral["lpu_address"],
        "lpu_phone": referral["lpu_phone"],
        "patient": " ".join(
            part for part in
            (referral["last_name"], referral["first_name"], referral["middle_name"])
            if part
        ),
        "specialities": [
            {
                "id": speciality["id"],
                "name": speciality["name"],
                "doctors": [
                    {
                        "id": doctor["id"],
                        "name": doctor["name"],
                        "note": doctor["note"],
                        "slots": [
                            {
                                "appointment_id": slot["id"],
                                "date": slot["start"].strftime("%Y-%m-%d"),
                                "time": slot["start"].strftime("%H:%M"),
                                "room": slot["room"],
                            }
                            for slot in doctor["slots"]
                        ],
                    }
                    for doctor in speciality["doctors"]
                ],
            }
            for speciality in referral["specialities"]
        ],
    }


async def _fetch_referral(number: str, last_name: str) -> dict:
    try:
        return await api.referral(number, last_name)
    except GorzdravError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _first_speciality_name(referral: dict) -> str | None:
    specialities = referral.get("specialities") or []
    return specialities[0]["name"] if specialities else None


@router.post("/referral/lookup")
async def lookup_referral(body: ReferralLookup, _: UserId) -> dict:
    referral = await _fetch_referral(body.number, body.last_name)
    return _serialize_referral(referral)


@router.get("/referrals")
async def list_referrals(user_id: UserId) -> list[dict]:
    return [
        {
            "id": row["id"],
            "number": row["number"],
            "patient_name": row["patient_name"],
            "lpu_name": row["lpu_name"],
            "speciality_name": row["speciality_name"],
            "time_from": row["time_from"],
            "time_to": row["time_to"],
            "time_label": describe_window(row["time_from"], row["time_to"]),
            "is_active": bool(row["is_active"]),
            "last_notified_at": row["last_notified_at"],
        }
        for row in await get_user_referrals(user_id)
    ]


@router.post("/referrals")
async def watch_referral(body: ReferralWatch, user_id: UserId) -> dict:
    referral = await _fetch_referral(body.number, body.last_name)
    referral_id, created = await save_referral(user_id, {
        "number": body.number,
        "last_name": body.last_name,
        "patient_name": referral["last_name"] + " " + referral["first_name"],
        "lpu_id": referral["lpu_id"],
        "lpu_name": referral["lpu_name"],
        "speciality_name": _first_speciality_name(referral),
        "time_from": body.time_from,
        "time_to": body.time_to,
    })
    return {"id": referral_id, "created": created}


@router.patch("/referrals/{referral_id}")
async def edit_referral(referral_id: int, body: ReferralUpdate, user_id: UserId) -> dict:
    changes = body.model_dump(exclude_none=True)
    if "is_active" in changes:
        changes["is_active"] = int(changes["is_active"])
    if not changes:
        raise HTTPException(status_code=400, detail="Нечего обновлять")

    if not await update_referral(referral_id, user_id, changes):
        raise HTTPException(status_code=404, detail="Направление не найдено")

    row = await get_referral(referral_id, user_id)
    return {
        "id": row["id"],
        "time_from": row["time_from"],
        "time_to": row["time_to"],
        "time_label": describe_window(row["time_from"], row["time_to"]),
        "is_active": bool(row["is_active"]),
    }


@router.delete("/referrals/{referral_id}")
async def remove_referral(referral_id: int, user_id: UserId) -> dict:
    if not await delete_referral(referral_id, user_id):
        raise HTTPException(status_code=404, detail="Направление не найдено")
    return {"success": True}


@router.get("/referrals/{referral_id}/slots")
async def referral_slots(referral_id: int, user_id: UserId) -> dict:
    row = await get_referral(referral_id, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Направление не найдено")

    referral = await _fetch_referral(row["number"], row["last_name"])
    found = collect_referral_slots(referral, row["time_from"], row["time_to"])
    return _serialize_slots(found)


# ── запись к врачу ──────────────────────────────────────────────────────────

@router.post("/referrals/book")
async def book_appointment(body: BookingCreate, user_id: UserId) -> dict:
    """Создаёт запись. Данные пациента берём из свежего ответа горздрава,
    а не из тела запроса — клиенту тут доверять нечего."""
    referral = await _fetch_referral(body.number, body.last_name)

    target = None
    for speciality in referral["specialities"]:
        for doctor in speciality["doctors"]:
            for slot in doctor["slots"]:
                if slot["id"] == body.appointment_id:
                    target = (speciality, doctor, slot)
                    break

    if target is None:
        raise HTTPException(
            status_code=409,
            detail="Этот номерок уже занят — обновите список и выберите другой",
        )

    speciality, doctor, slot = target

    # Поля и их значения повторяют transformSetAppontmentData со страницы
    # gorzdrav.spb.ru/service-referral-schedule: адрес берётся у поликлиники,
    # room и num в сценарии направления остаются пустыми.
    try:
        await api.create_appointment({
            "appointmentId": slot["id"],
            "lpuId": referral["lpu_id"],
            "patientId": referral["patient_id"],
            "referralId": body.number,
            "recipientEmail": "",
            "patientLastName": referral["last_name"],
            "patientFirstName": referral["first_name"],
            "patientMiddleName": referral["middle_name"],
            "patientBirthdate": referral["birthdate"],
            "room": None,
            "num": None,
            "address": referral["lpu_address"],
            "visitDate": slot["raw_start"],
        })
    except GorzdravError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    booking_id = await add_booking(user_id, {
        "referral_number": body.number,
        "appointment_id": slot["id"],
        "lpu_id": referral["lpu_id"],
        "lpu_name": referral["lpu_name"],
        "patient_id": referral["patient_id"],
        "doctor_name": doctor["name"],
        "speciality_name": speciality["name"],
        "visit_start": slot["start"].isoformat(),
        "address": slot["address"],
    })

    logger.info("Пользователь %s записан, booking=%s", user_id, booking_id)
    return {
        "id": booking_id,
        "doctor_name": doctor["name"],
        "speciality_name": speciality["name"],
        "lpu_name": referral["lpu_name"],
        "date": slot["start"].strftime("%Y-%m-%d"),
        "time": slot["start"].strftime("%H:%M"),
        "address": slot["address"],
    }


@router.get("/bookings")
async def list_bookings(user_id: UserId) -> list[dict]:
    rows = await get_bookings(user_id)
    return [
        {
            "id": row["id"],
            "doctor_name": row["doctor_name"],
            "speciality_name": row["speciality_name"],
            "lpu_name": row["lpu_name"],
            "visit_start": row["visit_start"],
            "address": row["address"],
            "status": row["status"],
        }
        for row in rows
    ]


@router.post("/bookings/{booking_id}/cancel")
async def cancel_booking(booking_id: int, user_id: UserId) -> dict:
    row = await get_booking(booking_id, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    if row["status"] != "active":
        raise HTTPException(status_code=409, detail="Запись уже отменена")

    try:
        await api.cancel_appointment(
            row["appointment_id"], row["lpu_id"], row["patient_id"]
        )
    except GorzdravError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await mark_booking_cancelled(booking_id, user_id)
    logger.info("Пользователь %s отменил booking=%s", user_id, booking_id)
    return {"success": True}
