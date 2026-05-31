import asyncio
import json
import logging
from datetime import datetime
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import BOT_TOKEN, CHECK_INTERVAL
from database import (
    init_db, add_subscription, get_user_subscriptions, delete_subscription_by_id,
    remove_all_subscriptions, add_favorite, get_favorites, 
    get_all_subscriptions_for_scheduler, update_subscription_history
)
from gorzdrav_api import GorzdravAPI

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
api = GorzdravAPI()
scheduler = AsyncIOScheduler()

class SubscriptionState(StatesGroup):
    selecting_district = State()
    selecting_lpu = State()
    selecting_speciality = State()
    selecting_doctor = State()
    confirming = State()

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="➕ Новая подписка"), KeyboardButton(text="📋 Мои подписки")],
        [KeyboardButton(text="❌ Удалить все")]
    ],
    resize_keyboard=True
)

def get_districts_keyboard(districts):
    kb = []
    for d in districts:
        if d.get('id') and d.get('name'):
            kb.append([InlineKeyboardButton(text=d['name'], callback_data=f"dist_{d['id']}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_lpus_keyboard(lpus, favorites):
    kb = []
    fav_ids = [str(f[0]) for f in favorites]
    if favorites: kb.append([InlineKeyboardButton(text="⭐ Перейти к избранному", callback_data="show_favs_menu")])
    for l in lpus:
        name = l.get('lpuShortName') or l.get('lpuFullName') or "Поликлиника"
        lid = str(l.get('id'))
        if lid:
            kb.append([InlineKeyboardButton(text=f"{'⭐ ' if lid in fav_ids else ''}{name}", callback_data=f"lpu_{lid}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_favorites_list_keyboard(favorites):
    kb = [[InlineKeyboardButton(text=name, callback_data=f"lpu_{lid}")] for lid, name in favorites]
    kb.append([InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_all_lpus")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_specialities_keyboard(specs):
    kb = []
    for s in specs:
        if s.get('id') and s.get('name'):
            kb.append([InlineKeyboardButton(text=s['name'][:27]+"..." if len(s['name'])>30 else s['name'], callback_data=f"s:{s['id']}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_doctors_keyboard_simple(doctors):
    kb = [[InlineKeyboardButton(text="👨‍⚕️ Любой врач", callback_data="doc_any")]]
    for doc in doctors:
        name = doc.get('fullName')
        did = doc.get('id')
        if did and name:
            has = " ✅" if doc.get('freeTicketCount', 0) > 0 else ""
            kb.append([InlineKeyboardButton(text=f"{name[:37]+'...' if len(name)>40 else name}{has}", callback_data=f"d:{did}")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_confirm_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_sub")]])

@dp.message(Command("start"))
@dp.message(F.text == "➕ Новая подписка")
async def start_flow(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("Выбери район:", reply_markup=types.ReplyKeyboardRemove())
    districts = await api.get_districts()
    if not districts:
        await msg.answer("❌ Ошибка загрузки.", reply_markup=MAIN_MENU)
        return
    await msg.answer("Выберите район:", reply_markup=get_districts_keyboard(districts))
    await state.set_state(SubscriptionState.selecting_district)

@dp.message(F.text == "📋 Мои подписки")
async def show_subs(msg: types.Message):
    subs = await get_user_subscriptions(msg.from_user.id)
    if not subs:
        await msg.answer("Нет активных подписок.", reply_markup=MAIN_MENU)
        return
    text = "📋 <b>Ваши подписки:</b>\n\n"
    kb = []
    for sid, lpu, spec, doc, sname, dname in subs:
        doc_info = f" ({dname})" if dname else " (Любой)"
        text += f"🏥 <b>{sname}</b>{doc_info}\n   ЛПУ: {lpu}\n\n"
        kb.append([InlineKeyboardButton(text=f"❌ Удалить", callback_data=f"del_sub_{sid}")])
    await msg.answer(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

@dp.message(F.text == "❌ Удалить все")
async def del_all(msg: types.Message):
    await remove_all_subscriptions(msg.from_user.id)
    await msg.answer("🗑 Все подписки удалены.", reply_markup=MAIN_MENU)

@dp.callback_query(F.data.startswith("dist_"), SubscriptionState.selecting_district)
async def proc_dist(cb: types.CallbackQuery, state: FSMContext):
    await state.update_data(district_id=cb.data.split("_")[1])
    await cb.message.edit_text("Выберите поликлинику:")
    lpus = await api.get_lpus_by_district(cb.data.split("_")[1])
    favs = await get_favorites(cb.from_user.id)
    await cb.message.answer("Выберите организацию:", reply_markup=get_lpus_keyboard(lpus, favs))
    await state.set_state(SubscriptionState.selecting_lpu)

@dp.callback_query(F.data == "show_favs_menu", SubscriptionState.selecting_lpu)
async def proc_favs(cb: types.CallbackQuery, state: FSMContext):
    favs = await get_favorites(cb.from_user.id)
    if not favs: return await cb.answer("Пусто")
    await cb.message.edit_text("Избранное:", reply_markup=get_favorites_list_keyboard(favs))

@dp.callback_query(F.data == "back_to_all_lpus", SubscriptionState.selecting_lpu)
async def proc_back(cb: types.CallbackQuery, state: FSMContext):
    did = (await state.get_data()).get('district_id')
    lpus = await api.get_lpus_by_district(did)
    favs = await get_favorites(cb.from_user.id)
    await cb.message.edit_text("Выберите поликлинику:", reply_markup=get_lpus_keyboard(lpus, favs))

@dp.callback_query(F.data.startswith("lpu_"), SubscriptionState.selecting_lpu)
async def proc_lpu(cb: types.CallbackQuery, state: FSMContext):
    lid = cb.data.split("_")[1]
    lpus = await api.get_lpus_by_district((await state.get_data()).get('district_id'))
    lname = next((l.get('lpuShortName') or l.get('lpuFullName') for l in lpus if str(l.get('id'))==lid), "Неизвестно")
    await state.update_data(lpu_id=lid, lpu_name=lname)
    await add_favorite(cb.from_user.id, lid, lname)
    await cb.message.edit_text(f"Поликлиника: {lname}\nСпец:")
    specs = await api.get_specialities(lid)
    if not specs: return await cb.message.answer("Нет специальностей.")
    await cb.message.answer("Выберите специальность:", reply_markup=get_specialities_keyboard(specs))
    await state.set_state(SubscriptionState.selecting_speciality)

@dp.callback_query(F.data.startswith("s:"), SubscriptionState.selecting_speciality)
async def proc_spec(cb: types.CallbackQuery, state: FSMContext):
    sid = cb.data[2:]
    lpu = (await state.get_data()).get('lpu_id')
    specs = await api.get_specialities(lpu)
    sname = next((s['name'] for s in specs if s.get('id')==sid), "Неизвестно")
    await state.update_data(speciality_id=sid, speciality_name=sname)
    
    docs = await api.get_doctors_list(lpu, sid)
    if not docs: return await cb.message.answer("Врачи не найдены.")
    
    dmap = {str(d['id']): d['fullName'] for d in docs}
    await state.update_data(doctors_map=dmap)
    await cb.message.edit_text(f"Спец: {sname}\nВрач:")
    await cb.message.answer("Выберите врача:", reply_markup=get_doctors_keyboard_simple(docs))
    await state.set_state(SubscriptionState.selecting_doctor)

@dp.callback_query(F.data.startswith("d:"), SubscriptionState.selecting_doctor)
@dp.callback_query(F.data == "doc_any", SubscriptionState.selecting_doctor)
async def proc_doc(cb: types.CallbackQuery, state: FSMContext):
    dmap = (await state.get_data()).get('doctors_map', {})
    if cb.data == "doc_any":
        await state.update_data(doctor_id=None, doctor_name=None, doctor_display="Любой врач")
        disp = "Любой врач"
    else:
        did = cb.data[2:]
        dname = dmap.get(did, "Врач")
        await state.update_data(doctor_id=did, doctor_name=dname, doctor_display=dname)
        disp = dname
        
    data = await state.get_data()
    text = f"📝 <b>Новая подписка:</b>\n\n🏥 {data.get('lpu_name')}\n👨‍⚕️ {data.get('speciality_name')}\n👤 {disp}\n"
    await cb.message.edit_text(text, parse_mode="HTML", reply_markup=get_confirm_keyboard())
    await state.set_state(SubscriptionState.confirming)

@dp.callback_query(F.data == "confirm_sub", SubscriptionState.confirming)
async def proc_confirm(cb: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    await add_subscription(cb.from_user.id, data['lpu_id'], data['speciality_id'], data['speciality_name'], data.get('doctor_id'), data.get('doctor_name'))
    await cb.message.edit_text("✅ <b>Подписка добавлена!</b>", parse_mode="HTML")
    await state.clear()
    await cb.message.answer("Меню:", reply_markup=MAIN_MENU)

@dp.callback_query(F.data.startswith("del_sub_"))
async def proc_del(cb: types.CallbackQuery):
    sid = int(cb.data.split("_")[2])
    await delete_subscription_by_id(sid, cb.from_user.id)
    await cb.message.edit_text("✅ Удалено.")
    await asyncio.sleep(1)
    await cb.message.answer("Меню:", reply_markup=MAIN_MENU)

async def parse_timetable_slots(timetable_data: list) -> list:
    """Извлекает доступные слоты из расписания врача"""
    slots = []
    now = datetime.now()
    for day in timetable_data:
        for appt in day.get('appointments', []):
            if appt.get('isAvailable', True) and appt.get('visitStart'):
                try:
                    dt = datetime.fromisoformat(appt['visitStart'].replace('Z', '+00:00'))
                    if dt > now:
                        slots.append(dt.strftime("%Y-%m-%d %H:%M"))
                except:
                    continue
    return slots

async def check_subscriptions_job():
    subscriptions = await get_all_subscriptions_for_scheduler()
    if not subscriptions: return

    logging.info(f"🔄 Проверка {len(subscriptions)} подписок...")
    
    for sub in subscriptions:
        sub_id, user_id, lpu_id, spec_id, doc_id, spec_name, doc_name, last_json = sub
        
        is_first_run = (last_json in ['{}', None, ''])
        try:
            history = json.loads(last_json) if last_json else {}
        except:
            history = {}

        current_history = {}
        new_slots = []
        
        doctors_to_check = []
        if doc_id:
            doctors_to_check = await api.get_doctors_list(lpu_id, spec_id)
            doctors_to_check = [d for d in doctors_to_check if str(d.get('id')) == doc_id]
        else:
            doctors_to_check = await api.get_doctors_with_slots(lpu_id, spec_id)

        for doc in doctors_to_check:
            did = str(doc.get('id'))
            dname = doc.get('fullName') or doc.get('name') or "Врач"
            
            timetable = await api.get_timetable(lpu_id, did)
            await asyncio.sleep(0.5)
            
            available_times = await parse_timetable_slots(timetable)
            
            prev_entry = history.get(did, {})
            prev_times = prev_entry.get("times", []) if isinstance(prev_entry, dict) else []
            
            for t in available_times:
                if t not in prev_times:
                    new_slots.append({"doc_name": dname, "time": t})

            current_history[did] = {"name": dname, "times": available_times}

        if new_slots and not is_first_run:
            msg = f"🔔 <b>Появились новые талоны!</b>\n🏥 {spec_name}\n\n"
            for s in new_slots:
                msg += f"👨‍⚕️ {s['doc_name']}\n⏰ {s['time']}\n\n"
            try:
                await bot.send_message(user_id, msg, parse_mode="HTML")
            except Exception as e:
                logging.error(f"Ошибка отправки {user_id}: {e}")
        elif is_first_run:
            logging.info(f"Подписка {sub_id}: Базовая линия установлена. Уведомлений нет.")

        await update_subscription_history(sub_id, json.dumps(current_history))

async def on_startup():
    await init_db()
    scheduler.add_job(check_subscriptions_job, 'interval', seconds=CHECK_INTERVAL)
    scheduler.start()
    print("🤖 Бот запущен. Ожидание новых талонов...")

async def main():
    await on_startup()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())