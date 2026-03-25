"""
Telegram-бот для игры Fiesta.
Транспортный слой: aiogram 3.x
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)

from game import GameEngine, GameError
from models import CardSource, GameState, Player, RoomSettings
from store import FiestaStore

# ─── Настройка логгирования ───
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ─── Инициализация ───
TOKEN = os.getenv("FIESTA_BOT_TOKEN", "8265764394:AAHji-WSZ7wmq92TOFv1FD2vRXobMMksv9c")
bot = Bot(token=TOKEN)
dp = Dispatcher()
router = Router()
dp.include_router(router)

engine = GameEngine()
store = FiestaStore()

# user_id -> room_id (в какой комнате сейчас игрок)
player_rooms: dict[int, str] = {}

# user_id -> состояние ввода ("writing" / "adding_character" / None)
user_state: dict[int, Optional[str]] = {}

# Кэш данных для угадывания: user_id -> {"associations": [...], "characters": [...]}
guessing_cache: dict[int, dict] = {}

# Таймеры
step_timers: dict[str, asyncio.Task] = {}


# ═══════════════════════════════════════════
#  Хелперы
# ═══════════════════════════════════════════

def make_player(msg_or_user) -> Player:
    """Создать Player из Message или User."""
    if hasattr(msg_or_user, 'from_user'):
        user = msg_or_user.from_user
    else:
        user = msg_or_user
    return Player(
        user_id=user.id,
        username=user.username or "",
        first_name=user.first_name or "Аноним",
        dm_available=False,
    )


async def check_dm(user_id: int) -> bool:
    """Проверить, можно ли писать в ЛС."""
    try:
        msg = await bot.send_message(user_id, "Проверка связи... Всё ок, я могу писать тебе в ЛС!")
        await bot.delete_message(user_id, msg.message_id)
        return True
    except Exception:
        return False


async def send_dm(user_id: int, text: str, reply_markup=None):
    """Отправить сообщение в ЛС."""
    try:
        await bot.send_message(user_id, text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Не удалось отправить ЛС {user_id}: {e}")


async def update_group_status(room):
    """Обновить статус в групповом чате."""
    if not room.group_chat_id:
        return

    if room.state == GameState.WRITING:
        pending = [
            room.players[uid].first_name
            for uid in room.players
            if uid not in room.step_submitted
        ]
        text = (
            f"Раунд ассоциаций: шаг {room.current_step + 1}/{room.total_steps}\n"
            f"Ждём: {', '.join(pending) if pending else 'все готовы!'}"
        )
    elif room.state == GameState.GUESSING:
        done = len(room.guessing_done)
        total = room.num_players
        pending = [
            room.players[uid].first_name
            for uid in room.players
            if uid not in room.guessing_done
        ]
        text = (
            f"Угадывание: {done}/{total} готовы\n"
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
                await bot.edit_message_text(
                    text, room.group_chat_id, room.status_message_id,
                )
                return
            except Exception:
                pass
        msg = await bot.send_message(room.group_chat_id, text)
        room.status_message_id = msg.message_id
    except Exception as e:
        logger.error(f"Ошибка обновления статуса в группе: {e}")


# ═══════════════════════════════════════════
#  Клавиатуры
# ═══════════════════════════════════════════

def lobby_keyboard(room_id: str, is_host: bool) -> InlineKeyboardMarkup:
    buttons = []
    if is_host:
        buttons.append([InlineKeyboardButton(
            text="Начать игру", callback_data=f"start:{room_id}"
        )])
        buttons.append([InlineKeyboardButton(
            text="Добавить своих персонажей", callback_data=f"collect:{room_id}"
        )])
        buttons.append([
            InlineKeyboardButton(
                text="Книги", callback_data=f"cat:{room_id}:books"
            ),
            InlineKeyboardButton(
                text="Фильмы", callback_data=f"cat:{room_id}:movies"
            ),
            InlineKeyboardButton(
                text="Сериалы", callback_data=f"cat:{room_id}:series"
            ),
            InlineKeyboardButton(
                text="Всё", callback_data=f"cat:{room_id}:mixed"
            ),
        ])
    buttons.append([InlineKeyboardButton(
        text="Покинуть", callback_data=f"leave:{room_id}"
    )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def join_keyboard(room_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Присоединиться", callback_data=f"join:{room_id}")
    ]])


def guessing_characters_keyboard(
    room_id: str, characters: list[dict], used_card_ids: set, assoc_card_id: str,
) -> InlineKeyboardMarkup:
    """Клавиатура с персонажами для угадывания."""
    buttons = []
    for ch in characters:
        if ch["card_id"] in used_card_ids:
            continue
        buttons.append([InlineKeyboardButton(
            text=ch["name"],
            callback_data=f"g:{room_id}:{assoc_card_id}:{ch['card_id']}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ═══════════════════════════════════════════
#  Таймеры
# ═══════════════════════════════════════════

async def association_timeout(room_id: str, step: int, timeout: int):
    """Таймер на шаг ассоциаций."""
    await asyncio.sleep(timeout)
    room = engine.rooms.get(room_id)
    if not room or room.state != GameState.WRITING or room.current_step != step:
        return

    logger.warning(f"Комната {room_id}: таймаут шага {step}")

    # Пропускаем всех кто не ответил
    pending = [uid for uid in room.players if uid not in room.step_submitted]
    for uid in pending:
        engine.skip_player(room_id, uid)
        await send_dm(uid, "Время вышло! Твоя ассоциация пропущена.")

    # Проверяем переход
    room.current_step += 1
    room.step_submitted.clear()

    if room.current_step >= room.total_steps:
        room.state = GameState.GUESSING
        room.guesses.clear()
        room.guessing_done.clear()
        room.guessing_progress.clear()
        await start_guessing_phase(room)
    else:
        await send_writing_tasks(room)


async def guessing_timeout(room_id: str, timeout: int):
    """Таймер на угадывание."""
    await asyncio.sleep(timeout)
    room = engine.rooms.get(room_id)
    if not room or room.state != GameState.GUESSING:
        return

    logger.warning(f"Комната {room_id}: таймаут угадывания")
    engine.force_finish_guessing(room_id)
    await show_results(room)


def start_timer(room_id: str, timer_key: str, coro):
    """Запустить таймер, отменив предыдущий."""
    key = f"{room_id}:{timer_key}"
    if key in step_timers:
        step_timers[key].cancel()
    step_timers[key] = asyncio.create_task(coro)


# ═══════════════════════════════════════════
#  Игровые фазы
# ═══════════════════════════════════════════

async def send_writing_tasks(room):
    """Разослать задания на текущий шаг ассоциаций."""
    room.status_message_id = None
    await update_group_status(room)

    for uid in room.players:
        task = engine.get_current_task(room, uid)
        if not task:
            continue

        if task["is_character"]:
            text = (
                f"Тебе досталась карточка с персонажем:\n\n"
                f"{task['visible_text']}\n\n"
                f"Напиши ассоциацию с этим персонажем:"
            )
        else:
            text = (
                f"Шаг {room.current_step + 1}/{room.total_steps}\n\n"
                f"Тебе передали карточку. На ней написано:\n\n"
                f"\"{task['visible_text']}\"\n\n"
                f"Напиши свою ассоциацию:"
            )
        user_state[uid] = "writing"
        await send_dm(uid, text)

    # Таймер
    start_timer(
        room.room_id, f"step_{room.current_step}",
        association_timeout(room.room_id, room.current_step, room.settings.association_timeout),
    )


async def start_guessing_phase(room):
    """Начать фазу угадывания."""
    room.status_message_id = None

    # Одинаковый порядок для всех
    data = engine.get_guessing_data(room)

    for uid in room.players:
        guessing_cache[uid] = {
            "associations": data["associations"],
            "characters": data["characters"],
            "room_id": room.room_id,
            "used": set(),  # card_id персонажей уже использованных
            "current_idx": 0,
        }
        user_state[uid] = "guessing"
        await send_next_guess(uid)

    await update_group_status(room)

    # Таймер
    start_timer(
        room.room_id, "guessing",
        guessing_timeout(room.room_id, room.settings.guessing_timeout),
    )


async def send_next_guess(user_id: int):
    """Отправить следующую ассоциацию для угадывания."""
    cache = guessing_cache.get(user_id)
    if not cache:
        return

    idx = cache["current_idx"]
    associations = cache["associations"]
    characters = cache["characters"]

    if idx >= len(associations):
        await send_dm(user_id, "Ты сопоставил все карточки! Ждём остальных...")
        return

    assoc = associations[idx]
    text = (
        f"Сопоставь ассоциацию с персонажем ({idx + 1}/{len(associations)}):\n\n"
        f"\"{assoc['text']}\"\n\n"
        f"Кто это?"
    )

    kb = guessing_characters_keyboard(
        cache["room_id"], characters, cache["used"], assoc["card_id"],
    )
    await send_dm(user_id, text, reply_markup=kb)


async def show_results(room):
    """Показать результаты."""
    results = engine.calculate_results(room.room_id)

    # Формируем текст результатов
    lines = ["РЕЗУЛЬТАТЫ\n"]

    # Счёт
    sorted_scores = sorted(
        results["scores"].items(),
        key=lambda x: x[1],
        reverse=True,
    )
    lines.append("Очки:")
    for i, (uid, score) in enumerate(sorted_scores):
        medal = ["1.", "2.", "3."][i] if i < 3 else f"{i+1}."
        player = room.players.get(uid)
        name = player.first_name if player else "???"
        lines.append(f"  {medal} {name}: {score}/{room.num_players}")

    text_scores = "\n".join(lines)

    # Цепочки ассоциаций
    chain_texts = []
    for card_id, chain in results["chains"].items():
        correct = results["correct_answers"][card_id]
        chain_lines = [f"Персонаж: {correct}"]
        for i, step in enumerate(chain):
            chain_lines.append(f"  {i+1}. {step['author']}: \"{step['text']}\"")
        last_assoc = chain[-1]["text"] if chain else "?"
        chain_lines.append(f"  Финальная ассоциация: \"{last_assoc}\"")
        chain_texts.append("\n".join(chain_lines))

    text_chains = "\n\n".join(chain_texts)
    full_text = f"{text_scores}\n\n{'='*30}\nЦепочки ассоциаций:\n\n{text_chains}"

    # Отправляем всем в ЛС
    for uid in room.players:
        user_state[uid] = None
        await send_dm(uid, full_text)

    # В группу — только счёт
    if room.group_chat_id:
        room.status_message_id = None
        try:
            await bot.send_message(room.group_chat_id, full_text)
        except Exception as e:
            logger.error(f"Ошибка отправки результатов в группу: {e}")

    # Предложить новую игру
    if room.group_chat_id:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Ещё раз!", callback_data=f"restart:{room.room_id}")
        ]])
        try:
            await bot.send_message(room.group_chat_id, "Сыграем ещё?", reply_markup=kb)
        except Exception:
            pass

    # Сохраняем результаты в БД
    for uid, score in results["scores"].items():
        store.save_result(room.room_id, uid, score, room.num_players)
    store.save_room(room)
    logger.info(f"Результаты комнаты {room.room_id} сохранены в БД")

    # Очистка
    for uid in list(player_rooms.keys()):
        if player_rooms.get(uid) == room.room_id:
            del player_rooms[uid]
    guessing_cache.clear()


# ═══════════════════════════════════════════
#  Команды
# ═══════════════════════════════════════════

@router.message(CommandStart())
async def cmd_start(message: Message):
    """Обработка /start — приветствие или присоединение к комнате."""
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].startswith("join_"):
        # Deep link: /start join_ABCD
        room_id = args[1].replace("join_", "").upper()
        await _join_room(message, room_id)
        return

    await message.answer(
        "Привет! Я бот для игры Fiesta — цепочки ассоциаций.\n\n"
        "Команды:\n"
        "/create — создать комнату\n"
        "/join КОД — присоединиться к комнате\n"
        "/help — правила игры"
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "Правила Fiesta:\n\n"
        "1. Каждый игрок получает карточку с персонажем\n"
        "2. Ты пишешь ассоциацию с этим персонажем\n"
        "3. Карточка передаётся дальше — следующий видит только твою ассоциацию "
        "и пишет свою\n"
        "4. Так по кругу, пока все не напишут\n"
        "5. В конце видны только финальные ассоциации — нужно угадать, "
        "какой персонаж за какой ассоциацией\n\n"
        "Чем больше совпадений — тем больше очков!"
    )


@router.message(Command("create"))
async def cmd_create(message: Message):
    """Создать комнату."""
    user_id = message.from_user.id

    if user_id in player_rooms:
        await message.answer("Ты уже в комнате. Сначала выйди (/leave).")
        return

    player = make_player(message)

    # Проверяем ЛС
    dm_ok = await check_dm(user_id)
    player.dm_available = dm_ok

    group_chat_id = message.chat.id if message.chat.type != "private" else None
    room = engine.create_room(player, group_chat_id=group_chat_id)
    player_rooms[user_id] = room.room_id

    bot_info = await bot.get_me()
    join_link = f"https://t.me/{bot_info.username}?start=join_{room.room_id}"

    text = (
        f"Комната создана! Код: {room.room_id}\n\n"
        f"Ссылка для друзей:\n{join_link}\n\n"
        f"Или команда: /join {room.room_id}\n\n"
        f"Игроки: 1/{room.settings.max_players}\n"
        f"Минимум для старта: {room.settings.min_players}"
    )

    if not dm_ok:
        text += "\n\nНапиши мне /start в личку, чтобы я мог отправлять тебе карточки!"

    kb = lobby_keyboard(room.room_id, is_host=True)
    if group_chat_id:
        kb2 = join_keyboard(room.room_id)
        await message.answer(text, reply_markup=kb2)
        await send_dm(user_id, f"Ты создал комнату {room.room_id}", reply_markup=kb)
    else:
        await message.answer(text, reply_markup=kb)


@router.message(Command("join"))
async def cmd_join(message: Message):
    """Присоединиться по команде /join КОД."""
    args = message.text.split()
    if len(args) < 2:
        await message.answer("Укажи код комнаты: /join ABCD")
        return
    await _join_room(message, args[1].upper())


async def _join_room(message: Message, room_id: str):
    """Общая логика присоединения."""
    user_id = message.from_user.id

    if user_id in player_rooms:
        if player_rooms[user_id] == room_id:
            await message.answer("Ты уже в этой комнате!")
            return
        await message.answer("Ты уже в другой комнате. Сначала выйди (/leave).")
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

    text = f"{player.first_name} присоединился! Игроков: {room.num_players}"
    if not dm_ok:
        text += f"\n\n{player.first_name}, напиши мне /start в личку!"

    # Уведомить группу
    if room.group_chat_id:
        try:
            await bot.send_message(room.group_chat_id, text)
        except Exception:
            pass

    # Уведомить в ЛС
    is_host = user_id == room.host_id
    kb = lobby_keyboard(room.room_id, is_host=is_host)
    await send_dm(user_id, f"Ты в комнате {room.room_id}!", reply_markup=kb)

    await update_group_status(room)


@router.message(Command("stats"))
async def cmd_stats(message: Message):
    """Статистика игрока."""
    stats = store.get_player_stats(message.from_user.id)
    if stats["games"] == 0:
        await message.answer("Ты ещё не играл! Создай комнату: /create")
        return
    await message.answer(
        f"Твоя статистика:\n"
        f"Игр: {stats['games']}\n"
        f"Очков: {stats['total_score']}/{stats['total_possible']}\n"
        f"Средний процент: {int(stats['avg_rate'] * 100)}%"
    )


@router.message(Command("leave"))
async def cmd_leave(message: Message):
    """Покинуть комнату."""
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
    await message.answer("Ты вышел из комнаты.")

    if not destroyed:
        await update_group_status(room)


# ═══════════════════════════════════════════
#  Callback queries
# ═══════════════════════════════════════════

@router.callback_query(F.data.startswith("join:"))
async def cb_join(callback: CallbackQuery):
    room_id = callback.data.split(":")[1]
    user_id = callback.from_user.id

    if user_id in player_rooms:
        if player_rooms[user_id] == room_id:
            await callback.answer("Ты уже в комнате!")
            return
        await callback.answer("Ты уже в другой комнате")
        return

    player = make_player(callback)
    dm_ok = await check_dm(user_id)
    player.dm_available = dm_ok

    try:
        room = engine.join_room(room_id, player)
    except GameError as e:
        await callback.answer(str(e), show_alert=True)
        return

    player_rooms[user_id] = room.room_id
    await callback.answer(f"Ты в комнате {room_id}!")

    if not dm_ok:
        bot_info = await bot.get_me()
        await callback.message.reply(
            f"{player.first_name}, напиши мне в личку: t.me/{bot_info.username}"
        )

    # ЛС
    is_host = user_id == room.host_id
    kb = lobby_keyboard(room.room_id, is_host=is_host)
    await send_dm(user_id, f"Ты в комнате {room.room_id}!", reply_markup=kb)

    await update_group_status(room)


@router.callback_query(F.data.startswith("start:"))
async def cb_start_game(callback: CallbackQuery):
    room_id = callback.data.split(":")[1]
    user_id = callback.from_user.id

    try:
        room = engine.start_game(room_id, user_id)
    except GameError as e:
        await callback.answer(str(e), show_alert=True)
        return

    await callback.answer("Игра началась!")

    # Уведомляем группу
    if room.group_chat_id:
        room.status_message_id = None
        try:
            await bot.send_message(
                room.group_chat_id,
                f"Игра началась! {room.num_players} игроков.\n"
                f"Проверяйте личные сообщения!"
            )
        except Exception:
            pass

    # Рассылаем первые задания
    await send_writing_tasks(room)


@router.callback_query(F.data.startswith("collect:"))
async def cb_collect(callback: CallbackQuery):
    room_id = callback.data.split(":")[1]
    user_id = callback.from_user.id

    try:
        room = engine.start_collecting(room_id, user_id)
    except GameError as e:
        await callback.answer(str(e), show_alert=True)
        return

    room.settings.card_source = CardSource.MIXED
    await callback.answer("Режим сбора персонажей!")

    # Уведомляем всех в ЛС
    for uid in room.players:
        user_state[uid] = "adding_character"
        await send_dm(
            uid,
            f"Добавь своих персонажей! Отправь имя персонажа текстом.\n"
            f"Когда закончишь — напиши /done\n\n"
            f"Уже добавлено: {len(room.custom_characters)}"
        )


@router.callback_query(F.data.startswith("cat:"))
async def cb_category(callback: CallbackQuery):
    parts = callback.data.split(":")
    room_id = parts[1]
    category = parts[2]

    room = engine.rooms.get(room_id)
    if not room:
        await callback.answer("Комната не найдена")
        return
    if callback.from_user.id != room.host_id:
        await callback.answer("Только хост может менять категорию")
        return

    room.settings.category = category
    names = {"books": "Книги", "movies": "Фильмы", "series": "Сериалы", "mixed": "Всё подряд"}
    await callback.answer(f"Категория: {names.get(category, category)}")


@router.callback_query(F.data.startswith("leave:"))
async def cb_leave(callback: CallbackQuery):
    room_id = callback.data.split(":")[1]
    user_id = callback.from_user.id

    try:
        room, destroyed = engine.leave_room(room_id, user_id)
    except GameError as e:
        await callback.answer(str(e), show_alert=True)
        return

    player_rooms.pop(user_id, None)
    user_state.pop(user_id, None)
    await callback.answer("Ты вышел из комнаты")

    if not destroyed:
        await update_group_status(room)


@router.callback_query(F.data.startswith("g:"))
async def cb_guess(callback: CallbackQuery):
    """Обработка угадывания: g:ROOM:assoc_card_id:char_card_id"""
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Ошибка данных")
        return

    room_id = parts[1]
    assoc_card_id = parts[2]
    char_card_id = parts[3]
    user_id = callback.from_user.id

    try:
        result = engine.submit_guess(room_id, user_id, assoc_card_id, char_card_id)
    except GameError as e:
        await callback.answer(str(e), show_alert=True)
        return

    # Обновляем кэш
    cache = guessing_cache.get(user_id)
    if cache:
        cache["used"].add(char_card_id)
        cache["current_idx"] += 1

    # Получаем имя персонажа для подтверждения
    room = engine.rooms.get(room_id)
    char_card = room.cards.get(char_card_id) if room else None
    char_name = char_card.character if char_card else "???"

    await callback.answer(f"Выбрано: {char_name}")

    # Удаляем старое сообщение с кнопками
    try:
        await callback.message.delete()
    except Exception:
        pass

    if result["guess_count"] < result["total"]:
        # Следующая ассоциация
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
async def cb_restart(callback: CallbackQuery):
    """Перезапуск игры в той же комнате."""
    room_id = callback.data.split(":")[1]
    room = engine.rooms.get(room_id)
    if not room:
        # Воссоздаём комнату
        await callback.answer("Комната закрыта. Создай новую: /create")
        return

    room.state = GameState.LOBBY
    room.cards.clear()
    room.custom_characters.clear()
    room.step_submitted.clear()
    room.guesses.clear()
    room.guessing_done.clear()
    room.guessing_progress.clear()
    room.current_step = 0
    room.status_message_id = None

    # Все игроки снова в комнате
    for uid in room.players:
        player_rooms[uid] = room.room_id

    await callback.answer("Новый раунд!")
    await update_group_status(room)

    # Хосту — панель
    kb = lobby_keyboard(room.room_id, is_host=True)
    await send_dm(room.host_id, f"Комната {room.room_id} готова к новой игре!", reply_markup=kb)


# ═══════════════════════════════════════════
#  Текстовые сообщения (ассоциации / персонажи)
# ═══════════════════════════════════════════

@router.message(Command("done"))
async def cmd_done(message: Message):
    """Завершить добавление персонажей."""
    user_id = message.from_user.id
    if user_state.get(user_id) != "adding_character":
        return

    user_state[user_id] = None
    room_id = player_rooms.get(user_id)
    room = engine.rooms.get(room_id) if room_id else None
    if room:
        await message.answer(
            f"Готово! Добавлено персонажей: {len(room.custom_characters)}"
        )


@router.message(F.chat.type == "private")
async def handle_private_text(message: Message):
    """Обработка текста в ЛС — ассоциации или добавление персонажей."""
    user_id = message.from_user.id
    state = user_state.get(user_id)

    if state == "writing":
        await _handle_association(message)
    elif state == "adding_character":
        await _handle_add_character(message)
    else:
        # Если игрок не в активной фазе — обычное сообщение
        if user_id in player_rooms:
            await message.answer("Сейчас не твой ход. Жди свою очередь!")
        else:
            await message.answer("Создай комнату (/create) или присоединись (/join КОД)")


async def _handle_association(message: Message):
    """Обработка ассоциации от игрока."""
    user_id = message.from_user.id
    room_id = player_rooms.get(user_id)
    if not room_id:
        await message.answer("Ты не в комнате")
        return

    text = message.text.strip()
    if not text:
        await message.answer("Отправь текстовую ассоциацию")
        return

    try:
        result = engine.submit_association(room_id, user_id, text)
    except GameError as e:
        await message.answer(str(e))
        return

    user_state[user_id] = None
    await message.answer("Ассоциация принята!")

    room = engine.rooms.get(room_id)
    if not room:
        return

    await update_group_status(room)

    if result["game_phase_changed"]:
        # Переход к угадыванию
        await start_guessing_phase(room)
    elif result["step_complete"]:
        # Следующий шаг
        await send_writing_tasks(room)


async def _handle_add_character(message: Message):
    """Обработка добавления персонажа."""
    user_id = message.from_user.id
    room_id = player_rooms.get(user_id)
    if not room_id:
        return

    try:
        room = engine.add_custom_character(room_id, user_id, message.text)
        await message.answer(
            f"Добавлено: {message.text.strip()}\n"
            f"Всего: {len(room.custom_characters)}\n"
            f"Ещё? Или /done"
        )
    except GameError as e:
        await message.answer(str(e))


# ═══════════════════════════════════════════
#  Запуск
# ═══════════════════════════════════════════

async def main():
    logger.info("Fiesta Bot запускается...")

    # Очистка старых комнат каждые 10 минут
    async def cleanup_loop():
        while True:
            await asyncio.sleep(600)
            engine.cleanup_stale_rooms()

    asyncio.create_task(cleanup_loop())

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
