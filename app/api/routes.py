import logging

from fastapi import APIRouter, HTTPException

from app.api.auth import UserId
from app.api.schemas import (
    FavoriteCreate,
    SlotsPreview,
    SubscriptionCreate,
    SubscriptionUpdate,
)
from app.database import (
    add_favorite,
    add_subscription,
    delete_all_subscriptions,
    delete_favorite,
    delete_subscription,
    get_favorites,
    get_subscription,
    get_user_subscriptions,
    update_subscription,
)
from app.gorzdrav import api, now_msk
from app.services.slots import collect_slots, describe_window

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
