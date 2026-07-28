from pydantic import BaseModel, Field

TIME_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"


class SubscriptionCreate(BaseModel):
    district_id: str | None = None
    district_name: str | None = None
    lpu_id: str = Field(..., min_length=1)
    lpu_name: str = Field(..., min_length=1)
    speciality_id: str = Field(..., min_length=1)
    speciality_name: str = Field(..., min_length=1)
    doctor_id: str | None = None
    doctor_name: str | None = None
    time_from: str = Field("00:00", pattern=TIME_PATTERN)
    time_to: str = Field("23:59", pattern=TIME_PATTERN)


class SubscriptionUpdate(BaseModel):
    time_from: str | None = Field(None, pattern=TIME_PATTERN)
    time_to: str | None = Field(None, pattern=TIME_PATTERN)
    is_active: bool | None = None


class SlotsPreview(BaseModel):
    lpu_id: str = Field(..., min_length=1)
    speciality_id: str = Field(..., min_length=1)
    doctor_id: str | None = None
    time_from: str = Field("00:00", pattern=TIME_PATTERN)
    time_to: str = Field("23:59", pattern=TIME_PATTERN)


class FavoriteCreate(BaseModel):
    lpu_id: str = Field(..., min_length=1)
    lpu_name: str = Field(..., min_length=1)
