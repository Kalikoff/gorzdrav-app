from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    BOT_TOKEN: str
    WEBAPP_URL: str = ""

    HOST: str = "0.0.0.0"
    PORT: int = 3002

    DATABASE_PATH: str = "/app/data/subscriptions.db"

    GORZDRAV_BASE: str = "https://gorzdrav.spb.ru/_api/api/v2"
    GORZDRAV_SITE: str = "https://gorzdrav.spb.ru/service-free-schedule"

    CHECK_INTERVAL: int = 300       # пауза между проверками подписок, секунды
    REQUEST_DELAY: float = 0.4      # пауза между запросами к горздраву, секунды
    MAX_SLOTS_IN_MESSAGE: int = 30  # сколько номерков перечислять в уведомлении

    SKIP_AUTH: bool = False

    model_config = {"env_file": ".env"}


settings = Settings()
