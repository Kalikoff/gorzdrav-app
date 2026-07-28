import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router
from app.bot import bot, dp
from app.config import settings
from app.database import init_db
from app.gorzdrav import api as gorzdrav
from app.services.checker import run_checker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "app" / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    await init_db()
    logger.info("База готова")

    bot_task = asyncio.create_task(dp.start_polling(bot, handle_signals=False))
    checker_task = asyncio.create_task(run_checker())
    logger.info("Бот и мониторинг запущены")

    yield

    for task in (bot_task, checker_task):
        task.cancel()
    for task in (bot_task, checker_task):
        try:
            await task
        except asyncio.CancelledError:
            pass

    await gorzdrav.close()
    await bot.session.close()
    logger.info("Остановлено")


app = FastAPI(title="Gorzdrav Tickets", lifespan=lifespan)
app.include_router(api_router)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    uvicorn.run(app, host=settings.HOST, port=settings.PORT)
