import hashlib
import hmac
import json
from typing import Annotated
from urllib.parse import parse_qsl

from fastapi import Depends, Header, HTTPException

from app.config import settings


async def verify_telegram_auth(init_data: str) -> int:
    """Проверяет initData мини-приложения и возвращает user_id."""
    if settings.SKIP_AUTH:
        try:
            params = dict(parse_qsl(init_data, keep_blank_values=True))
            user = json.loads(params.get("user", "{}"))
            return int(user.get("id", 1))
        except Exception:
            return 1

    try:
        params = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = params.pop("hash", "")

        data_check_string = "\n".join(f"{key}={value}" for key, value in sorted(params.items()))

        secret_key = hmac.new(
            b"WebAppData",
            settings.BOT_TOKEN.encode(),
            hashlib.sha256,
        ).digest()

        computed_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            raise HTTPException(status_code=401, detail="Invalid Telegram auth")

        user = json.loads(params.get("user", "{}"))
        return int(user["id"])

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail=f"Auth error: {exc}") from exc


async def current_user(x_telegram_init_data: str = Header(...)) -> int:
    return await verify_telegram_auth(x_telegram_init_data)


UserId = Annotated[int, Depends(current_user)]
