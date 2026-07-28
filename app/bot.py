import logging
from datetime import date
from html import escape

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from app.config import settings
from app.database import register_user
from app.services.slots import describe_window

logger = logging.getLogger(__name__)

bot = Bot(token=settings.BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
MONTHS = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def open_app_button(text: str = "🩺 Открыть приложение") -> InlineKeyboardButton | None:
    if not settings.WEBAPP_URL:
        return None
    return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=settings.WEBAPP_URL))


def _plural(count: int, one: str, few: str, many: str) -> str:
    if 11 <= count % 100 <= 14:
        return many
    last = count % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def format_day(day: date) -> str:
    today = date.today()
    delta = (day - today).days
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "завтра"
    return f"{WEEKDAYS[day.weekday()]}, {day.day} {MONTHS[day.month - 1]}"


@dp.message(CommandStart())
async def cmd_start(message: Message) -> None:
    user = message.from_user
    await register_user(user.id, user.username, user.first_name)

    if not settings.WEBAPP_URL:
        await message.answer(
            "⚠️ <b>WEBAPP_URL</b> не задан — укажи его в .env и перезапусти бота.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    await message.answer(
        f"Привет, {escape(user.first_name or 'друг')}! 👋\n\n"
        "Я слежу за свободными номерками в поликлиниках Санкт-Петербурга "
        "и пишу, как только появится подходящий.\n\n"
        "Врачей, поликлиники и фильтр по времени приёма настраивай в приложении:",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )
    await message.answer(
        "Нажми кнопку ниже 👇",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[open_app_button()]]),
    )


@dp.message(Command("help"))
async def cmd_help(message: Message) -> None:
    button = open_app_button()
    await message.answer(
        "<b>Как это работает</b>\n\n"
        "1. В приложении выбираешь поликлинику, специальность и врача "
        "(или «любой врач» — тогда слежу за всеми сразу).\n"
        "2. Задаёшь окно времени, например «с 17:00» — номерки на 12:00 "
        "или 15:00 я просто проигнорирую.\n"
        f"3. Каждые {settings.CHECK_INTERVAL // 60} мин проверяю расписание "
        "и пишу, когда появится новый подходящий номерок.\n\n"
        "Записаться нужно на сайте горздрава — я только слежу за расписанием.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button]]) if button else None,
    )


@dp.message(F.text)
async def fallback(message: Message) -> None:
    button = open_app_button()
    if button:
        await message.answer(
            "Настройки живут в приложении 👇",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[button]]),
        )
    else:
        await message.answer("Отправь /start")


async def send_slots_message(subscription: dict, doctors: list[dict]) -> None:
    """Уведомление о новых номерках по подписке."""
    lines = ["🔔 <b>Появились новые номерки!</b>", ""]
    lines.append(f"🏥 {escape(subscription.get('lpu_name') or 'Поликлиника')}")
    lines.append(f"🩺 {escape(subscription['speciality_name'])}")
    if subscription.get("doctor_name"):
        lines.append(f"👤 {escape(subscription['doctor_name'])}")
    lines.append(
        f"⏰ {escape(describe_window(subscription['time_from'], subscription['time_to']))}"
    )

    total = sum(len(doctor["slots"]) for doctor in doctors)
    limit = settings.MAX_SLOTS_IN_MESSAGE
    shown = 0

    for doctor in doctors:
        if shown >= limit:
            break
        lines.append("")
        lines.append(f"👨‍⚕️ <b>{escape(doctor['doctor_name'])}</b>")

        by_day: dict[date, list] = {}
        for slot in doctor["slots"]:
            by_day.setdefault(slot["start"].date(), []).append(slot["start"])

        for day, moments in sorted(by_day.items()):
            if shown >= limit:
                break
            chunk = moments[: limit - shown]
            shown += len(chunk)
            times = ", ".join(moment.strftime("%H:%M") for moment in chunk)
            lines.append(f"   📅 {format_day(day)} — {times}")

    if total > shown:
        rest = total - shown
        word = _plural(rest, "номерок", "номерка", "номерков")
        lines.append("")
        lines.append(f"…и ещё {rest} {word}")

    keyboard: list[list[InlineKeyboardButton]] = []
    button = open_app_button("🩺 Посмотреть в приложении")
    if button:
        keyboard.append([button])
    keyboard.append([
        InlineKeyboardButton(text="🔗 Записаться на gorzdrav.spb.ru", url=settings.GORZDRAV_SITE)
    ])

    await bot.send_message(
        subscription["user_id"],
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
    )
