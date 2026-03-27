"""
Telegram-бот: Fiesta — Карнавал мёртвых.
Кооперативная игра с ассоциациями.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from game import GameEngine, GameError
from models import CardSource, GameState, Player, RoomSettings
from store import FiestaStore

_log_fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
logging.basicConfig(level=logging.INFO, format=_log_fmt)
# Файловый хендлер — один раз, без дублирования
_fh = logging.FileHandler("fiesta.log", encoding="utf-8")
_fh.setFormatter(logging.Formatter(_log_fmt))
logging.getLogger().addHandler(_fh)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("FIESTA_BOT_TOKEN", "8265764394:AAHji-WSZ7wmq92TOFv1FD2vRXobMMksv9c")
bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

engine = GameEngine()
store = FiestaStore()

# ═══════════════════════════════════════
#  Калавера — персонаж-ведущий
# ═══════════════════════════════════════
# Калавера — скелет-распорядитель карнавала мёртвых.
# Весёлый, немного зловещий, говорит с мексиканским колоритом.
# Его работа — проводить карнавал, следить за порядком и веселить гостей.

CALAVERA_PHRASES = {
    "welcome": [
        "Bienvenidos al Carnaval de los Muertos! Я Калавера — распорядитель карнавала. При жизни торговал перцем чили в Оахаке, а теперь вот... развлекаю живых и мёртвых!",
        "Hola, amigos! Калавера к вашим услугам. Четыреста лет на этой работе, и поверьте — каждый карнавал как первый!",
        "Добро пожаловать! Мёртвых помнят не по датам, а по словам. Скажи 'сила' — и кто-то вспомнит Геркулеса. Так работает карнавал. Так работает память.",
    ],
    "game_start": [
        "Музыка играет, маски надеты! Каждому — карточку с персонажем. Одно слово, amigos, всего одно — но пусть оно будет таким, чтобы мертвец улыбнулся!",
        "Карнавал начинается! Запомни персонажа и придумай слово. Хорошее слово — как хороший перец: маленькое, но запоминается навсегда.",
        "Ну что, готовы? Черепа пустые, зубы не закрашены. Пора это исправить! Вперёд!",
    ],
    "first_card": [
        "Тебе достался персонаж! Посмотри на него внимательно. Теперь придумай одно слово — такое, чтобы по нему можно было вспомнить этого героя. Только одно!",
        "Вот твой персонаж. Подумай — какое слово первым приходит в голову? Не мудри, amigo. Лучшие ассоциации — самые простые.",
    ],
    "new_tooth": [
        "Новый круг! Прочитай слово на черепе, сотри его, напиши своё. Одно слово — пусть оно будет ярким!",
        "Черепа передаются дальше! Не пытайся угадать персонажа — просто напиши, что приходит в голову от увиденного слова.",
        "Ещё один зуб закрашен! Цепочка растёт. Интересно, узнают ли мертвеца по последнему слову...",
        "Дальше, дальше! Прочитал — стёр — написал своё. Так память работает: одно слово цепляет другое.",
    ],
    "guessing_start": [
        "Hora de adivinar! Черепа на столе, персонажи перемешаны. Теперь тишина — думай молча, amigo. Кто за каким словом прячется?",
        "А теперь — самое интересное! Перед тобой последние слова и список персонажей. Вспомни, что ты стирал. Вспомни цепочки. Кто есть кто?",
    ],
    "all_rested": [
        "Todos en paz! Все мёртвые упокоены! Миктлантекутли доволен, скелеты танцуют, а я плачу от счастья. Ну, если бы мог плакать. Buena suerte!",
        "Невероятно! Все угаданы! За четыреста лет такое бывало... ну, пару раз. Вы — настоящие гости карнавала!",
    ],
    "good_result": [
        "Muy bien! Почти все мёртвые спят спокойно. Пара беспокойных — ничего, жетоны кости помогут.",
        "Отличная работа, amigos! Карнавал удался. Не идеально, но мертвецы не привередливы.",
    ],
    "ok_result": [
        "No está mal! Кое-кто ещё беспокоится, но это нормально — не все цепочки ведут куда надо. Попробуем ещё?",
        "Половина упокоена — уже праздник! Но я знаю, вы можете лучше. Ещё раунд?",
    ],
    "bad_result": [
        "Ay, caramba! Мертвецы недовольны... Но знаете что? На карнавале нет проигравших — есть те, кто ещё не станцевал. Давайте ещё раз!",
        "Hmm, цепочки запутались. Бывает! Когда-то я перепутал весь склад перца с паприкой — и ничего, выжил. Ну, фигурально.",
    ],
    "timeout": [
        "Время вышло! На карнавале нельзя опаздывать — мертвецы не ждут, amigo!",
        "Тик-так! Калавера ждал, но всё имеет предел. Даже терпение четырёхсотлетнего скелета.",
    ],
    "player_joined": [
        "Nuevo amigo на карнавале! Чем больше гостей, тем громче музыка!",
        "Ещё один гость! Добро пожаловать. Надевай маску, бери маркер — скоро начинаем!",
        "О, свежая кровь! В хорошем смысле, amigo, в хорошем. Добро пожаловать!",
    ],
    "bone_token": [
        "Жетон кости! Все угадали — мертвец так доволен, что делится косточкой. Пригодится!",
        "Perfecto! Все правильно — держите жетон кости. Он поможет упокоить кого-нибудь другого.",
    ],
    "word_accepted": [
        "Записано! Зуб закрашен.",
        "Принято, amigo!",
        "Есть! Хорошее слово.",
    ],
    "waiting": [
        "Ждём остальных гостей...",
        "Некоторые ещё думают. Терпение, amigo!",
    ],
    "farewell": [
        "Hasta la proxima fiesta! До следующего карнавала!",
        "Спасибо за игру! Мертвецы довольны. Увидимся через год... или через пять минут, если нажмёте 'Ещё раз'!",
    ],
}

import random as _rnd

from llm import calavera_llm, unload_ollama_model, is_model_loaded


def calavera(key: str) -> str:
    """Случайная СТАТИЧЕСКАЯ фраза Калаверы (синхронный фоллбэк)."""
    phrases = CALAVERA_PHRASES.get(key, ["..."])
    return _rnd.choice(phrases)


async def cal(key: str, context: str = "") -> str:
    """Асинхронная фраза Калаверы через LLM с фоллбэком на статические."""
    fallback = CALAVERA_PHRASES.get(key, ["..."])
    return await calavera_llm(key, context=context, fallback_phrases=fallback)

# user_id -> room_id
player_rooms: dict[int, str] = {}
# user_id -> состояние
user_state: dict[int, Optional[str]] = {}
# Кэш угадывания: user_id -> данные
guessing_cache: dict[int, dict] = {}
# Таймеры
step_timers: dict[str, asyncio.Task] = {}


# ═══════════════════════════════════════
#  Хелперы
# ═══════════════════════════════════════

def make_player(msg_or_cb) -> Player:
    user = msg_or_cb.from_user
    return Player(
        user_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "Аноним",
    )


async def check_dm(user_id: int) -> bool:
    try:
        msg = await bot.send_message(user_id, "Связь установлена!")
        await bot.delete_message(user_id, msg.message_id)
        return True
    except Exception:
        return False


async def send_dm(user_id: int, text: str, reply_markup=None):
    try:
        await bot.send_message(user_id, text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Не удалось ЛС {user_id}: {e}")


async def safe_cb_answer(cb: CallbackQuery, text: str = "", **kwargs):
    """cb.answer() который не падает на 'query is too old'."""
    try:
        await cb.answer(text, **kwargs)
    except Exception as e:
        logger.debug(f"cb.answer failed (expected): {e}")


async def update_group_status(room):
    if not room.group_chat_id:
        return

    if room.state == GameState.WRITING:
        pending = [
            room.players[uid].first_name
            for uid in room.players if uid not in room.tooth_submitted
        ]
        constraint_text = ""
        if room.active_constraints:
            constraint_text = "\nОграничения: у каждого своё!"
        text = (
            f"Зуб {room.current_tooth + 1}/{room.total_teeth}{constraint_text}\n"
            f"Ждём: {', '.join(pending) if pending else 'все готовы!'}"
        )
    elif room.state == GameState.GUESSING:
        done = len(room.guessing_done)
        pending = [
            room.players[uid].first_name
            for uid in room.players if uid not in room.guessing_done
        ]
        text = (
            f"Угадывание: {done}/{room.num_players}\n"
            f"Ждём: {', '.join(pending) if pending else 'все!'}"
        )
    elif room.state == GameState.LOBBY:
        players_list = ", ".join(p.first_name for p in room.players.values())
        text = (
            f"Комната {room.room_id}\n"
            f"Игроки ({room.num_players}): {players_list}\n"
            f"Минимум для старта: {room.settings.min_players}"
        )
    else:
        return

    try:
        if room.status_message_id:
            try:
                await bot.edit_message_text(text, room.group_chat_id, room.status_message_id)
                return
            except Exception:
                pass
        msg = await bot.send_message(room.group_chat_id, text)
        room.status_message_id = msg.message_id
    except Exception as e:
        logger.error(f"Ошибка статуса группы: {e}")


# ═══════════════════════════════════════
#  Клавиатуры
# ═══════════════════════════════════════

def lobby_kb(room_id: str, is_host: bool) -> InlineKeyboardMarkup:
    buttons = []
    if is_host:
        buttons.append([InlineKeyboardButton(
            text="Начать игру", callback_data=f"start:{room_id}")])
        buttons.append([InlineKeyboardButton(
            text="Свои персонажи", callback_data=f"collect:{room_id}")])
        buttons.append([
            InlineKeyboardButton(text="Книги", callback_data=f"cat:{room_id}:books"),
            InlineKeyboardButton(text="Фильмы", callback_data=f"cat:{room_id}:movies"),
            InlineKeyboardButton(text="Сериалы", callback_data=f"cat:{room_id}:series"),
            InlineKeyboardButton(text="Всё", callback_data=f"cat:{room_id}:mixed"),
        ])
        buttons.append([
            InlineKeyboardButton(text="Ур.0", callback_data=f"lvl:{room_id}:0"),
            InlineKeyboardButton(text="Ур.1", callback_data=f"lvl:{room_id}:1"),
            InlineKeyboardButton(text="Ур.2", callback_data=f"lvl:{room_id}:2"),
            InlineKeyboardButton(text="Ур.3", callback_data=f"lvl:{room_id}:3"),
        ])
    buttons.append([InlineKeyboardButton(
        text="Покинуть", callback_data=f"leave:{room_id}")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def join_kb(room_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Присоединиться", callback_data=f"join:{room_id}")
    ]])


def guess_chars_kb(room_id: str, characters: list[str],
                   used: set, skull_id: str) -> InlineKeyboardMarkup:
    buttons = []
    for i, ch in enumerate(characters):
        if ch in used:
            continue
        # callback_data макс 64 байт: g:ROOM:SKULL:idx
        buttons.append([InlineKeyboardButton(
            text=ch, callback_data=f"g:{room_id}:{skull_id}:{i}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ═══════════════════════════════════════
#  Таймеры
# ═══════════════════════════════════════

async def tooth_timeout(room_id: str, tooth: int, timeout: int):
    await asyncio.sleep(timeout)
    room = engine.rooms.get(room_id)
    if not room or room.state != GameState.WRITING or room.current_tooth != tooth:
        return
    logger.warning(f"Комната {room_id}: таймаут зуба {tooth}")
    pending = [uid for uid in room.players if uid not in room.tooth_submitted]
    for uid in pending:
        engine.skip_player(room_id, uid)
        await send_dm(uid, "Время вышло! Слово пропущено.")

    room.current_tooth += 1
    room.tooth_submitted.clear()
    if room.current_tooth >= room.total_teeth:
        room.state = GameState.GUESSING
        room.guesses.clear()
        room.guessing_done.clear()
        room.guessing_progress.clear()
        await start_guessing_phase(room)
    else:
        await send_writing_tasks(room)


async def guessing_timeout_fn(room_id: str, timeout: int):
    await asyncio.sleep(timeout)
    room = engine.rooms.get(room_id)
    if not room or room.state != GameState.GUESSING:
        return
    engine.force_finish_guessing(room_id)
    await show_results(room)


def start_timer(room_id: str, key: str, coro):
    full_key = f"{room_id}:{key}"
    if full_key in step_timers:
        step_timers[full_key].cancel()
    step_timers[full_key] = asyncio.create_task(coro)


# ═══════════════════════════════════════
#  Игровые фазы
# ═══════════════════════════════════════

async def send_writing_tasks(room):
    room.status_message_id = None
    await update_group_status(room)

    # Собираем задачи и генерим LLM-фразы параллельно
    tasks_data = []
    for uid in room.players:
        task = engine.get_current_task(room, uid)
        if not task:
            continue
        tasks_data.append((uid, task))

    if not tasks_data:
        return

    # Параллельная генерация фраз для всех игроков
    async def gen_phrase(uid, task):
        player_name = room.players[uid].first_name
        if task["is_character"]:
            return await cal('first_card', context=f'Игрок: {player_name}')
        else:
            return await cal('new_tooth', context=f'Игрок: {player_name}, зуб {room.current_tooth + 1}')

    phrases = await asyncio.gather(*[gen_phrase(uid, task) for uid, task in tasks_data])

    # Рассылка (быстрая, без LLM)
    for (uid, task), cal_phrase in zip(tasks_data, phrases):
        if task["is_character"]:
            char_name = task['visible']
            text = (
                f"{cal_phrase}\n\n"
                f"Твой персонаж: {char_name}\n\n"
                f"Напиши ОДНО слово:"
            )
        else:
            text = (
                f"{cal_phrase}\n\n"
                f"Зуб {room.current_tooth + 1}/{room.total_teeth}\n\n"
                f"На черепе написано:\n\n"
                f"\"{task['visible']}\"\n\n"
                f"Стираешь это слово. Пиши ОДНО слово-ассоциацию:"
            )

        if task.get("constraint"):
            text += f"\n\nОграничение: {task['constraint'].value}"

        user_state[uid] = "writing"
        await send_dm(uid, text)

    start_timer(
        room.room_id, f"tooth_{room.current_tooth}",
        tooth_timeout(room.room_id, room.current_tooth, room.settings.association_timeout),
    )


async def start_guessing_phase(room):
    room.status_message_id = None
    data = engine.get_guessing_data(room)

    for uid in room.players:
        guessing_cache[uid] = {
            "skulls": data["skulls"],
            "characters": data["characters"],
            "room_id": room.room_id,
            "used_chars": set(),
            "current_idx": 0,
        }
        user_state[uid] = "guessing"
        await send_next_guess(uid)

    await update_group_status(room)
    start_timer(
        room.room_id, "guessing",
        guessing_timeout_fn(room.room_id, room.settings.guessing_timeout),
    )


async def send_next_guess(user_id: int):
    cache = guessing_cache.get(user_id)
    if not cache:
        return

    idx = cache["current_idx"]
    skulls = cache["skulls"]

    if idx >= len(skulls):
        await send_dm(user_id, "Все черепа сопоставлены! Ждём остальных...")
        return

    skull = skulls[idx]
    intro = (await cal('guessing_start')) + "\n\n" if idx == 0 else ""
    text = (
        f"{intro}"
        f"Череп {idx + 1}/{len(skulls)}\n\n"
        f"Последнее слово: \"{skull['last_word']}\"\n\n"
        f"Какой это персонаж?"
    )

    kb = guess_chars_kb(
        cache["room_id"], cache["characters"],
        cache["used_chars"], skull["skull_id"],
    )
    await send_dm(user_id, text, reply_markup=kb)


async def show_results(room):
    results = engine.calculate_results(room.room_id)

    lines = [f"ДЕНЬ МЁРТВЫХ — РЕЗУЛЬТАТЫ\n"]
    lines.append(f"Упокоено: {results['rested_count']}/{results['total_skulls']}")
    lines.append(
        f"Жетоны кости: {results['initial_bones']} стартовых "
        f"+ {results['earned_bones']} заработано, "
        f"использовано: {results['bones_used']}"
    )
    lines.append(f"Порог упокоения: {results['threshold']} из {room.num_players} правильных\n")

    # Оценка от Калаверы
    ratio = results['rested_count'] / max(1, results['total_skulls'])
    if ratio >= 1.0:
        lines.append(await cal('all_rested'))
    elif ratio >= 0.75:
        lines.append(await cal('good_result'))
    elif ratio >= 0.5:
        lines.append(await cal('ok_result'))
    else:
        lines.append(await cal('bad_result'))

    lines.append("")

    for s in results["skulls"]:
        status = "УПОКОЕН" if s["rested"] else "БЕСПОКОЕН"
        bones_info = f" (+{s.get('bones_used', 0)} кость)" if s.get('bones_used', 0) > 0 else ""
        lines.append(f"--- {s['character']} [{status}{bones_info}]")
        lines.append(f"  Последнее слово: \"{s['last_word']}\"")
        lines.append(f"  Угадали: {s['correct_count']}/{room.num_players}")

        # Цепочка ассоциаций
        chain_words = " -> ".join(step["word"] for step in s["chain"])
        lines.append(f"  Цепочка: {s['character']} -> {chain_words}")
        lines.append("")

    full_text = "\n".join(lines)

    # Всем в ЛС
    for uid in room.players:
        user_state[uid] = None
        await send_dm(uid, full_text)

    # В группу
    if room.group_chat_id:
        room.status_message_id = None
        try:
            await bot.send_message(room.group_chat_id, full_text)
        except Exception as e:
            logger.error(f"Ошибка отправки результатов в группу: {e}")

    # Сохраняем в БД
    store.save_room(room)
    for uid in room.players:
        store.save_result(
            room.room_id, uid,
            results['rested_count'], results['total_skulls'],
        )

    # Кнопка "ещё раз"
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Ещё раз!", callback_data=f"restart:{room.room_id}")
    ]])
    farewell_text = await cal("farewell")
    if room.group_chat_id:
        try:
            await bot.send_message(room.group_chat_id, farewell_text, reply_markup=kb)
        except Exception:
            pass
    # В ЛС хосту (на случай если нет группового чата)
    if not room.group_chat_id:
        await send_dm(room.host_id, farewell_text, reply_markup=kb)

    # Очистка — только игроков этой комнаты
    for uid in list(room.players.keys()):
        player_rooms.pop(uid, None)
        guessing_cache.pop(uid, None)


# ═══════════════════════════════════════
#  Команды
# ═══════════════════════════════════════

@router.message(CommandStart())
async def cmd_start(message: Message):
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("join_"):
        room_id = args[1].replace("join_", "").upper()
        await _join_room(message, room_id)
        return

    await message.answer(
        f"{await cal('welcome')}\n\n"
        "/create — создать комнату\n"
        "/join КОД — присоединиться\n"
        "/rules — правила\n"
        "/stats — статистика"
    )


@router.message(Command("rules"))
async def cmd_rules(message: Message):
    await message.answer(
        "Правила Fiesta:\n\n"
        "1. Каждый берёт карточку с персонажем (тайно)\n"
        "2. Пишешь ОДНО слово-ассоциацию с персонажем\n"
        "3. Передаёшь череп соседу — он видит только твоё слово\n"
        "4. Он стирает и пишет своё слово (ассоциацию на твоё)\n"
        "5. Так несколько кругов (зависит от числа игроков)\n"
        "6. В конце видны только последние слова + 8 персонажей\n"
        "7. Все вместе сопоставляют слова с персонажами\n\n"
        "Цель — упокоить как можно больше мёртвых!\n"
        "Порог: нужно N-1 правильных ответов из N игроков.\n"
        "Если все угадали — бонусный жетон кости!"
    )


@router.message(Command("create"))
async def cmd_create(message: Message):
    user_id = message.from_user.id
    if user_id in player_rooms:
        await message.answer("Ты уже в комнате. Сначала /leave")
        return

    player = make_player(message)
    dm_ok = await check_dm(user_id)
    player.dm_available = dm_ok

    group_chat_id = message.chat.id if message.chat.type != "private" else None
    room = engine.create_room(player, group_chat_id=group_chat_id)
    player_rooms[user_id] = room.room_id

    bot_info = await bot.get_me()
    join_link = f"https://t.me/{bot_info.username}?start=join_{room.room_id}"

    text = (
        f"Комната создана! Код: {room.room_id}\n\n"
        f"Ссылка: {join_link}\n\n"
        f"Или: /join {room.room_id}\n\n"
        f"Игроки: 1/{room.settings.max_players}\n"
        f"Минимум: {room.settings.min_players}"
    )

    if not dm_ok:
        text += "\n\nНапиши мне /start в личку!"

    kb = lobby_kb(room.room_id, is_host=True)
    if group_chat_id:
        await message.answer(text, reply_markup=join_kb(room.room_id))
        await send_dm(user_id, f"Ты создал комнату {room.room_id}", reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.message(Command("join"))
async def cmd_join(message: Message):
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Укажи код: /join ABCD")
        return
    await _join_room(message, args[1].upper())


async def _join_room(message: Message, room_id: str):
    user_id = message.from_user.id
    if user_id in player_rooms:
        if player_rooms[user_id] == room_id:
            await message.answer("Ты уже в этой комнате!")
            return
        await message.answer("Ты в другой комнате. Сначала /leave")
        return

    player = make_player(message)
    dm_ok = await check_dm(user_id)
    player.dm_available = dm_ok

    try:
        room = engine.join_room(room_id, player)
    except GameError as e:
        await message.answer(str(e))
        return

    player_rooms[user_id] = room.room_id

    join_msg = f"{await cal('player_joined', context=f'Игрок: {player.first_name}')} {player.first_name} ({room.num_players} игроков)"

    if room.group_chat_id:
        try:
            await bot.send_message(room.group_chat_id, join_msg)
        except Exception:
            pass
    else:
        # Нет группового чата — рассылаем в ЛС остальным игрокам
        for uid in room.players:
            if uid != user_id:
                try:
                    await send_dm(uid, join_msg)
                except Exception:
                    pass

    is_host = user_id == room.host_id
    await send_dm(user_id, f"Ты в комнате {room.room_id}!",
                  reply_markup=lobby_kb(room.room_id, is_host))

    if not dm_ok:
        bot_info = await bot.get_me()
        await message.answer(f"{player.first_name}, напиши мне: t.me/{bot_info.username}")

    await update_group_status(room)


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    stats = store.get_player_stats(message.from_user.id)
    if stats["games"] == 0:
        await message.answer("Ты ещё не играл!")
        return
    await message.answer(
        f"Статистика:\n"
        f"Игр: {stats['games']}\n"
        f"Упокоено: {stats['total_score']}/{stats['total_possible']}\n"
        f"Средний %: {int(stats['avg_rate'] * 100)}%"
    )


@router.message(Command("feedback"))
async def cmd_feedback(message: Message):
    text = message.text.replace("/feedback", "", 1).strip()
    if not text:
        await message.answer("Напиши: /feedback твоё сообщение")
        return
    user = message.from_user
    try:
        await bot.send_message(
            ADMIN_CHAT_ID,
            f"[Fiesta feedback]\n"
            f"От: {user.first_name} (@{user.username}, id={user.id})\n"
            f"Текст: {text}"
        )
    except Exception as e:
        logger.error(f"Ошибка пересылки фидбека: {e}")
    await message.answer("Передано! Спасибо за обратную связь.")


@router.message(Command("leave"))
async def cmd_leave(message: Message):
    user_id = message.from_user.id
    room_id = player_rooms.get(user_id)
    if not room_id:
        await message.answer("Ты не в комнате.")
        return
    try:
        room, destroyed = engine.leave_room(room_id, user_id)
    except GameError as e:
        await message.answer(str(e))
        return
    del player_rooms[user_id]
    user_state.pop(user_id, None)
    await message.answer("Ты вышел.")
    if not destroyed:
        await update_group_status(room)


# ═══════════════════════════════════════
#  Callbacks
# ═══════════════════════════════════════

@router.callback_query(F.data.startswith("join:"))
async def cb_join(cb: CallbackQuery):
    room_id = cb.data.split(":")[1]
    user_id = cb.from_user.id

    if user_id in player_rooms:
        await safe_cb_answer(cb, "Ты уже в комнате!" if player_rooms[user_id] == room_id
                        else "Ты в другой комнате")
        return

    player = make_player(cb)
    dm_ok = await check_dm(user_id)
    player.dm_available = dm_ok

    try:
        room = engine.join_room(room_id, player)
    except GameError as e:
        await safe_cb_answer(cb, str(e), show_alert=True)
        return

    player_rooms[user_id] = room.room_id
    await safe_cb_answer(cb, "Ты в комнате!")

    if not dm_ok:
        bot_info = await bot.get_me()
        if cb.message:
            await cb.message.reply(f"{player.first_name}, напиши мне: t.me/{bot_info.username}")

    await send_dm(user_id, f"Ты в комнате {room.room_id}!",
                  reply_markup=lobby_kb(room.room_id, False))
    await update_group_status(room)


@router.callback_query(F.data.startswith("start:"))
async def cb_start(cb: CallbackQuery):
    room_id = cb.data.split(":")[1]
    try:
        room = engine.start_game(room_id, cb.from_user.id)
    except GameError as e:
        await safe_cb_answer(cb, str(e), show_alert=True)
        return

    save_room_state(room)
    await safe_cb_answer(cb, "Игра началась!")

    if room.group_chat_id:
        room.status_message_id = None
        constraint_text = ""
        if room.active_constraints:
            names = ", ".join(c.value for c in room.active_constraints)
            constraint_text = f"\nОграничения: {names}"
        try:
            await bot.send_message(
                room.group_chat_id,
                f"{await cal('game_start')}\n\n"
                f"Игроков: {room.num_players}, зубов: {room.total_teeth}{constraint_text}\n"
                f"Жетоны кости: {room.initial_bone_tokens}\n"
                f"Проверяйте личные сообщения!"
            )
        except Exception:
            pass

    await send_writing_tasks(room)


@router.callback_query(F.data.startswith("collect:"))
async def cb_collect(cb: CallbackQuery):
    room_id = cb.data.split(":")[1]
    try:
        room = engine.start_collecting(room_id, cb.from_user.id)
    except GameError as e:
        await safe_cb_answer(cb, str(e), show_alert=True)
        return

    room.settings.card_source = CardSource.MIXED
    await safe_cb_answer(cb, "Режим сбора!")

    for uid in room.players:
        user_state[uid] = "adding_character"
        await send_dm(uid,
            f"Добавь своих персонажей! Отправь имя текстом.\n"
            f"/done когда закончишь.\n"
            f"Уже: {len(room.custom_characters)}")


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(cb: CallbackQuery):
    parts = cb.data.split(":")
    room_id, category = parts[1], parts[2]
    room = engine.rooms.get(room_id)
    if not room or cb.from_user.id != room.host_id:
        await safe_cb_answer(cb, "Только хост")
        return
    room.settings.category = category
    names = {"books": "Книги", "movies": "Фильмы", "series": "Сериалы", "mixed": "Всё"}
    await safe_cb_answer(cb, f"Категория: {names.get(category, category)}")


@router.callback_query(F.data.startswith("lvl:"))
async def cb_level(cb: CallbackQuery):
    parts = cb.data.split(":")
    room_id, level = parts[1], int(parts[2])
    room = engine.rooms.get(room_id)
    if not room or cb.from_user.id != room.host_id:
        await safe_cb_answer(cb, "Только хост")
        return
    room.settings.difficulty_level = level
    await safe_cb_answer(cb, f"Уровень сложности: {level}")


@router.callback_query(F.data.startswith("leave:"))
async def cb_leave(cb: CallbackQuery):
    room_id = cb.data.split(":")[1]
    user_id = cb.from_user.id
    try:
        room, destroyed = engine.leave_room(room_id, user_id)
    except GameError as e:
        await safe_cb_answer(cb, str(e), show_alert=True)
        return
    player_rooms.pop(user_id, None)
    user_state.pop(user_id, None)
    await safe_cb_answer(cb, "Ты вышел")
    if not destroyed:
        await update_group_status(room)


@router.callback_query(F.data.startswith("g:"))
async def cb_guess(cb: CallbackQuery):
    """g:ROOM:SKULL_ID:char_idx"""
    parts = cb.data.split(":")
    if len(parts) != 4:
        await safe_cb_answer(cb, "Ошибка")
        return

    room_id, skull_id, char_idx = parts[1], parts[2], int(parts[3])
    user_id = cb.from_user.id

    cache = guessing_cache.get(user_id)
    if not cache:
        await safe_cb_answer(cb, "Нет данных")
        return

    character = cache["characters"][char_idx]

    try:
        result = engine.submit_guess(room_id, user_id, skull_id, character)
    except GameError as e:
        await safe_cb_answer(cb, str(e), show_alert=True)
        return

    cache["used_chars"].add(character)
    cache["current_idx"] += 1

    room = engine.rooms.get(room_id)
    if room:
        save_room_state(room)

    await safe_cb_answer(cb, f"Выбрано: {character}")

    try:
        await cb.message.delete()
    except Exception:
        pass

    if result["guess_count"] < result["total"]:
        await send_next_guess(user_id)
    else:
        await send_dm(user_id, "Готово! Ждём остальных...")

    if result["all_done"]:
        room = engine.rooms.get(room_id)
        if room:
            await show_results(room)
    else:
        room = engine.rooms.get(room_id)
        if room:
            await update_group_status(room)


@router.callback_query(F.data.startswith("restart:"))
async def cb_restart(cb: CallbackQuery):
    room_id = cb.data.split(":")[1]
    room = engine.rooms.get(room_id)
    if not room:
        await safe_cb_answer(cb, "Комната закрыта. /create")
        return

    room.state = GameState.LOBBY
    room.skulls.clear()
    room.custom_characters.clear()
    room.tooth_submitted.clear()
    room.guesses.clear()
    room.guessing_done.clear()
    room.guessing_progress.clear()
    room.skull_scores.clear()
    room.decoy_characters.clear()
    room.all_characters.clear()
    room.active_constraints.clear()
    room.current_tooth = 0
    room.bone_tokens = 0
    room.status_message_id = None

    for uid in room.players:
        player_rooms[uid] = room.room_id

    await safe_cb_answer(cb, "Новый раунд!")
    await update_group_status(room)
    await send_dm(room.host_id, f"Комната {room.room_id} готова!",
                  reply_markup=lobby_kb(room.room_id, True))


# ═══════════════════════════════════════
#  Текстовые сообщения
# ═══════════════════════════════════════

@router.message(Command("done"))
async def cmd_done(message: Message):
    user_id = message.from_user.id
    if user_state.get(user_id) != "adding_character":
        return
    user_state[user_id] = None
    room_id = player_rooms.get(user_id)
    room = engine.rooms.get(room_id) if room_id else None
    if room:
        await message.answer(f"Готово! Персонажей: {len(room.custom_characters)}")


@router.message(F.chat.type == "private")
async def handle_private(message: Message):
    user_id = message.from_user.id
    state = user_state.get(user_id)

    if state == "writing":
        await _handle_word(message)
    elif state == "adding_character":
        await _handle_add_char(message)
    else:
        if user_id in player_rooms:
            await message.answer("Жди свою очередь!")
        else:
            await message.answer("/create или /join КОД")


async def _handle_word(message: Message):
    user_id = message.from_user.id
    room_id = player_rooms.get(user_id)
    if not room_id:
        await message.answer("Ты не в комнате")
        return

    text = message.text.strip() if message.text else ""
    if not text:
        await message.answer("Отправь одно слово")
        return

    try:
        result = engine.submit_word(room_id, user_id, text)
    except GameError as e:
        await message.answer(f"{e}\nПопробуй другое слово:")
        return

    user_state[user_id] = None
    await message.answer(await cal("word_accepted"))

    room = engine.rooms.get(room_id)
    if not room:
        return

    save_room_state(room)

    await update_group_status(room)

    if result["game_phase_changed"]:
        await start_guessing_phase(room)
    elif result["tooth_complete"]:
        await send_writing_tasks(room)


async def _handle_add_char(message: Message):
    user_id = message.from_user.id
    room_id = player_rooms.get(user_id)
    if not room_id:
        return
    try:
        room = engine.add_custom_character(room_id, user_id, message.text)
        await message.answer(f"Добавлено: {message.text.strip()}\nВсего: {len(room.custom_characters)}\nЕщё? /done")
    except GameError as e:
        await message.answer(str(e))


# ═══════════════════════════════════════
#  Групповые сообщения (не команды)
# ═══════════════════════════════════════

ADMIN_CHAT_ID = 500390885  # Пересылка баг-репортов

CALAVERA_GROUP_REPLIES = {
    "баг": "Ay! Баг на карнавале? Я передам это нашему техническому скелету. Спасибо что сообщил!",
    "ошибка": "Ошибка? Hmm, я записал. Наш костяной инженер разберётся!",
    "не работает": "No funciona? Сейчас разберёмся, amigo!",
    "сломал": "Сломать карнавал непросто, но ты смог! Передаю мастеру...",
    "помощь": "Нужна помощь? /rules — правила, /create — создать комнату, /join КОД — присоединиться!",
    "help": "Need help, amigo? /rules for rules, /create to start!",
    "привет": "Hola! Я Калавера, распорядитель карнавала мёртвых. Готов играть? /create!",
    "спасибо": "De nada! На карнавале мы все друзья!",
}


@router.message(F.chat.type.in_({"group", "supergroup"}))
async def handle_group_message(message: Message):
    """Обработка некомандных сообщений в группах."""
    if not message.text:
        return

    text_lower = message.text.lower()
    logger.info(f"Группа {message.chat.id}: {message.from_user.first_name}: {message.text[:100]}")

    # Калавера отвечает на ключевые слова
    for keyword, reply in CALAVERA_GROUP_REPLIES.items():
        if keyword in text_lower:
            await message.reply(reply)

            # Пересылаем баг-репорты админу
            if keyword in ("баг", "ошибка", "не работает", "сломал"):
                try:
                    await bot.send_message(
                        ADMIN_CHAT_ID,
                        f"[Fiesta баг-репорт]\n"
                        f"От: {message.from_user.first_name} (@{message.from_user.username})\n"
                        f"Чат: {message.chat.title}\n"
                        f"Текст: {message.text}"
                    )
                except Exception:
                    pass
            return


# ═══════════════════════════════════════
#  Запуск
# ═══════════════════════════════════════

def save_room_state(room):
    """Сохранить комнату в БД. Вызывать после каждого изменения."""
    try:
        store.save_room(room)
    except Exception as e:
        logger.error(f"Ошибка сохранения комнаты {room.room_id}: {e}")


def _save_all_active():
    """Сохранить все активные комнаты."""
    for room in engine.rooms.values():
        save_room_state(room)


def _restore_rooms():
    """Восстановить активные комнаты из БД после рестарта."""
    rooms = store.load_active_rooms()
    restored = 0
    for room in rooms:
        # Пропускаем слишком старые (>2 часов)
        if time.time() - room.last_activity > 7200:
            logger.info(f"Комната {room.room_id} слишком старая, пропускаем")
            continue
        engine.rooms[room.room_id] = room
        for uid in room.players:
            player_rooms[uid] = room.room_id
            # Восстанавливаем user_state
            if room.state == GameState.WRITING:
                if uid not in room.tooth_submitted:
                    user_state[uid] = "writing"
            elif room.state == GameState.GUESSING:
                if uid not in room.guessing_done:
                    user_state[uid] = "guessing"
        restored += 1
    if restored:
        logger.info(f"Восстановлено {restored} комнат из БД")


async def main():
    logger.info("Fiesta Bot запускается...")

    # Восстанавливаем комнаты из предыдущей сессии
    _restore_rooms()

    async def save_loop():
        while True:
            await asyncio.sleep(30)
            _save_all_active()

    async def cleanup_loop():
        while True:
            await asyncio.sleep(600)
            engine.cleanup_stale_rooms()

    async def ollama_unload_loop():
        """Выгружает модель из памяти если нет активных игр 30 минут."""
        IDLE_TIMEOUT = 30 * 60  # 30 минут
        while True:
            await asyncio.sleep(300)  # проверяем каждые 5 минут
            if not is_model_loaded():
                continue
            # Есть ли активные игры?
            active = any(
                r.state not in (GameState.LOBBY, GameState.FINISHED)
                for r in engine.rooms.values()
            )
            if active:
                continue
            # Когда последняя игра завершилась?
            last_activity = max(
                (r.last_activity for r in engine.rooms.values()),
                default=0,
            )
            if last_activity and time.time() - last_activity >= IDLE_TIMEOUT:
                logger.info("Нет активных игр 30 мин — выгружаю модель ollama")
                await unload_ollama_model()

    asyncio.create_task(save_loop())
    asyncio.create_task(cleanup_loop())
    asyncio.create_task(ollama_unload_loop())
    await dp.start_polling(bot, drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
