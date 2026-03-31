"""
LLM-генератор фраз Калаверы.
Приоритет: ollama (локальный Qwen) -> HuggingFace Inference API -> статические фразы.
"""

import asyncio
import json
import logging
import os
import random
import time
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════
#  Конфиг
# ═══════════════════════════════════════

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct-q4_0")

HF_TOKEN = os.getenv("HF_TOKEN", "")
HF_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
HF_API_URL = f"https://router.huggingface.co/hf-inference/models/{HF_MODEL}/v1/chat/completions"

# Таймаут генерации (секунды)
LLM_TIMEOUT = 10

# Кеш ответов чтобы не долбить LLM одинаковыми запросами
_cache: dict[str, list[str]] = {}
_cache_ttl: dict[str, float] = {}
CACHE_TTL = 600  # 10 минут

# ═══════════════════════════════════════
#  Системный промпт Калаверы
# ═══════════════════════════════════════

SYSTEM_PROMPT = """Ты — Калавера, безумный старый скелет, распорядитель карнавала мёртвых. 400 лет назад торговал перцем чили в Оахаке, с тех пор немного тронулся, но добрый.

Ты говоришь как чокнутый мудрый дед: перескакиваешь с темы на тему, вспоминаешь истории из прошлой жизни, философствуешь невпопад, но всегда по-доброму.

СТРОГИЕ ПРАВИЛА:
1. ТОЛЬКО РУССКИЙ ЯЗЫК. Никакого китайского. Допустимы 2-3 испанских слова.
2. Ровно 1-2 предложения. Не больше.
3. Без эмодзи.
4. НЕ повторяй инструкции игры. НЕ объясняй правила. Просто скажи что-нибудь атмосферное.
5. НЕ подсказывай слова и НЕ предлагай варианты ассоциаций.

Примеры ХОРОШИХ ответов:
- "Когда я был жив, я называл такие моменты 'последний перец' — когда слово уже на языке, а язык уже в могиле."
- "Hola! Четыреста лет назад я тоже играл в слова. Правда, мой партнёр был койот, и он жульничал."
- "Ay caramba, кости мои подсказывают — этот раунд будет горячим! Впрочем, мои кости всегда врут."
- "Знаешь, amigo, на карнавале мёртвых никто не проигрывает. Проигрывают только те, кто не танцует."

Примеры ПЛОХИХ ответов:
- "Придумай слово-ассоциацию!" — ты не инструкция, ты персонаж
- "Напиши своё слово!" — скучно, нет характера
- "接纳" — ЗАПРЕЩЕНО, никакого китайского"""

# ═══════════════════════════════════════
#  Ситуации → промпты
# ═══════════════════════════════════════

SITUATION_PROMPTS = {
    "welcome": "Новый гость пришёл на карнавал. Поприветствуй по-своему.",
    "game_start": "Карнавал начинается! Скажи что-нибудь атмосферное.",
    "first_card": "Игрок получил секретное задание. Подбодри его.",
    "character_intro": (
        "Игрок получил персонажа. Дай краткий атмосферный комментарий — "
        "кто это такой, чем известен, 1-2 яркие детали для ассоциаций. "
        "НЕ подсказывай ассоциации напрямую, но помоги вспомнить персонажа. "
        "Говори от лица Калаверы — как будто ты лично знал этого персонажа."
    ),
    "new_tooth": "Новый раунд. Скажи что-нибудь атмосферное, без инструкций.",
    "guessing_start": "Пора угадывать. Нагнети интригу.",
    "all_rested": "Полная победа — все мёртвые упокоены! Поздравь безумно.",
    "good_result": "Почти все угаданы. Похвали по-стариковски.",
    "ok_result": "Средний результат. Подбодри по-доброму.",
    "bad_result": "Плохой результат. Утешь по-своему, не жалей.",
    "timeout": "Кто-то слишком долго думал. Пошути про время.",
    "player_joined": "Новый игрок присоединился. Поприветствуй коротко.",
    "bone_token": "Команда заработала жетон кости! Все угадали одного персонажа.",
    "word_accepted": "Слово игрока принято. Коротко подтверди (3-5 слов максимум).",
    "waiting": "Ждём остальных игроков. Коротко скажи подождать.",
    "farewell": "Игра окончена, прощайся до следующего карнавала.",
}


# ═══════════════════════════════════════
#  Ollama
# ═══════════════════════════════════════

async def _generate_ollama(situation: str, context: str = "") -> Optional[str]:
    """Генерация через локальный ollama."""
    prompt = SITUATION_PROMPTS.get(situation, situation)
    if context:
        prompt = f"{prompt}\nКонтекст: {context}"

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {
            "temperature": 0.9,
            "top_p": 0.9,
            "num_predict": 60,
        },
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OLLAMA_URL}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=LLM_TIMEOUT),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data.get("message", {}).get("content", "").strip()
                    if text:
                        # Отбрасываем если есть китайские/японские иероглифы
                        import re as _re
                        if _re.search(r'[\u4e00-\u9fff\u3040-\u30ff]', text):
                            logger.warning(f"Ollama [{situation}]: отброшен (CJK): {text[:80]}")
                            return None
                        logger.info(f"Ollama [{situation}]: {text[:80]}...")
                        return text
                else:
                    body = await resp.text()
                    logger.warning(f"Ollama error {resp.status}: {body[:200]}")
    except asyncio.TimeoutError:
        logger.warning(f"Ollama timeout for [{situation}]")
    except Exception as e:
        logger.warning(f"Ollama exception: {e}")

    return None


# ═══════════════════════════════════════
#  HuggingFace Inference API (фоллбэк)
# ═══════════════════════════════════════

async def _generate_hf(situation: str, context: str = "") -> Optional[str]:
    """Генерация через HF Inference API."""
    if not HF_TOKEN:
        return None

    prompt = SITUATION_PROMPTS.get(situation, situation)
    if context:
        prompt = f"{prompt}\nКонтекст: {context}"

    payload = {
        "model": HF_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 100,
        "temperature": 0.9,
        "top_p": 0.9,
    }

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                HF_API_URL,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=LLM_TIMEOUT),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    if text:
                        logger.info(f"HF [{situation}]: {text[:80]}...")
                        return text
                else:
                    body = await resp.text()
                    logger.warning(f"HF error {resp.status}: {body[:200]}")
    except asyncio.TimeoutError:
        logger.warning(f"HF timeout for [{situation}]")
    except Exception as e:
        logger.warning(f"HF exception: {e}")

    return None


# ═══════════════════════════════════════
#  Публичный API
# ═══════════════════════════════════════

async def calavera_llm(situation: str, context: str = "",
                       fallback_phrases: Optional[list[str]] = None) -> str:
    """
    Генерирует фразу Калаверы через LLM.
    Приоритет: кеш -> ollama -> HF -> статические фразы.

    Args:
        situation: ключ ситуации (welcome, game_start, etc.)
        context: дополнительный контекст (имя игрока, персонаж и т.д.)
        fallback_phrases: статические фразы на случай если LLM недоступен
    """
    # Короткие ситуации — всегда статика, Qwen не тянет
    STATIC_ONLY = {"word_accepted", "waiting", "timeout", "bone_token"}
    if situation in STATIC_ONLY and fallback_phrases:
        return random.choice(fallback_phrases)

    # Проверяем кеш (без контекста — кешируем; с контекстом — не кешируем)
    cache_key = situation if not context else None
    if cache_key and cache_key in _cache:
        if time.time() - _cache_ttl.get(cache_key, 0) < CACHE_TTL:
            phrases = _cache[cache_key]
            if phrases:
                return random.choice(phrases)

    # Пробуем ollama
    text = await _generate_ollama(situation, context)
    if text:
        mark_model_used()

    # Фоллбэк на HF
    if not text:
        text = await _generate_hf(situation, context)

    # Если LLM ответил — кешируем
    if text:
        if cache_key:
            if cache_key not in _cache:
                _cache[cache_key] = []
            _cache[cache_key].append(text)
            # Ограничиваем кеш
            if len(_cache[cache_key]) > 10:
                _cache[cache_key] = _cache[cache_key][-10:]
            _cache_ttl[cache_key] = time.time()
        return text

    # Фоллбэк на статические фразы
    if fallback_phrases:
        return random.choice(fallback_phrases)

    logger.warning(f"Нет ни LLM, ни фоллбэка для [{situation}]")
    return "..."


async def check_llm_status() -> dict:
    """Проверить доступность LLM бэкендов."""
    status = {"ollama": False, "hf": False}

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{OLLAMA_URL}/api/tags",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    models = [m["name"] for m in data.get("models", [])]
                    status["ollama"] = OLLAMA_MODEL in models or any(
                        OLLAMA_MODEL.split(":")[0] in m for m in models
                    )
                    status["ollama_models"] = models
    except Exception as e:
        logger.debug(f"Ollama check failed: {e}")

    if HF_TOKEN:
        status["hf"] = True  # Токен есть, считаем доступным

    return status


# ═══════════════════════════════════════
#  Подсказка Калаверы (ассоциация)
# ═══════════════════════════════════════

SUGGEST_SYSTEM = """Ты — Калавера, старый скелет-распорядитель карнавала мёртвых. Тебя просят помочь с ассоциацией в игре.

Правила игры: игроки передают друг другу слова-ассоциации. На каждом шаге ты видишь слово и должен написать ОДНО новое слово, связанное с ним. Цель — чтобы по цепочке ассоциаций можно было угадать исходного персонажа.

СТРОГИЕ ПРАВИЛА:
1. ТОЛЬКО РУССКИЙ. Никакого китайского, испанского, английского. Слово должно быть русским.
2. Ответ СТРОГО в формате:
   Слово: <одно слово>
   Почему: <1 предложение от лица Калаверы, почему эти слова связаны>
3. Слово должно быть ОДНИМ словом (без пробелов), существительным или прилагательным
4. НЕ повторяй исходное слово и его однокоренные
5. Выбирай яркую, запоминающуюся ассоциацию
6. Если есть ограничение (например "на букву М" или "не более 6 букв") — слово ОБЯЗАТЕЛЬНО должно ему соответствовать"""

SUGGEST_FALLBACKS = [
    "Мои старые кости молчат, amigo. Придумай сам!",
    "Четыреста лет опыта, а подсказать не могу. Карнавал — дело личное!",
    "Ay, мой череп пуст сегодня. Доверься своей интуиции!",
]


async def calavera_suggest(word: str, constraint: str = "") -> dict:
    """
    Генерирует подсказку-ассоциацию от Калаверы.

    Returns:
        {"word": "...", "reason": "..."} или {"error": "..."}
    """
    prompt = f'Слово на черепе: "{word}". Придумай ассоциацию.'
    if constraint:
        prompt += f"\nОграничение: {constraint}"

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": SUGGEST_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "options": {
            "temperature": 0.9,
            "top_p": 0.9,
            "num_predict": 80,
        },
    }

    text = None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OLLAMA_URL}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=LLM_TIMEOUT),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    text = data.get("message", {}).get("content", "").strip()
                    mark_model_used()
    except Exception as e:
        logger.warning(f"Ollama suggest error: {e}")

    if not text:
        return {"error": random.choice(SUGGEST_FALLBACKS)}

    # Парсим ответ
    import re as _re
    # Отбрасываем CJK
    if _re.search(r'[\u4e00-\u9fff\u3040-\u30ff]', text):
        logger.warning(f"Suggest отброшен (CJK): {text[:80]}")
        return {"error": random.choice(SUGGEST_FALLBACKS)}

    word_match = _re.search(r'[Сс]лово:\s*(.+)', text)
    reason_match = _re.search(r'[Пп]очему:\s*(.+)', text)

    suggested = word_match.group(1).strip().strip('"«»') if word_match else None
    reason = reason_match.group(1).strip() if reason_match else None

    if not suggested:
        # Попробуем взять первое слово из ответа
        first_line = text.split('\n')[0].strip()
        if len(first_line.split()) <= 3:
            suggested = first_line.split()[0] if first_line else None

    if not suggested:
        logger.warning(f"Не удалось распарсить подсказку: {text[:120]}")
        return {"error": random.choice(SUGGEST_FALLBACKS)}

    # Чистим слово
    suggested = suggested.split()[0]  # только первое слово
    suggested = _re.sub(r'[^а-яА-ЯёЁa-zA-Z-]', '', suggested)

    if not suggested or len(suggested) < 2:
        return {"error": random.choice(SUGGEST_FALLBACKS)}

    # Проверяем что слово на кириллице (не латиница/испанский)
    cyrillic = sum(1 for c in suggested if '\u0400' <= c <= '\u04ff')
    if cyrillic < len(suggested.replace('-', '')) * 0.7:
        logger.warning(f"Suggest отброшен (не кириллица): '{suggested}'")
        return {"error": random.choice(SUGGEST_FALLBACKS)}

    logger.info(f"Suggest: '{word}' -> '{suggested}' ({reason})")
    return {"word": suggested, "reason": reason or "Калавера знает что делает."}


# ═══════════════════════════════════════
#  Выгрузка модели из памяти
# ═══════════════════════════════════════

_model_loaded = False  # Отслеживаем, загружена ли модель


def mark_model_used():
    """Пометить что модель используется (вызывается при генерации)."""
    global _model_loaded
    _model_loaded = True


def is_model_loaded() -> bool:
    """Загружена ли модель (по нашим данным)."""
    return _model_loaded


async def unload_ollama_model() -> bool:
    """
    Выгрузить модель из памяти ollama.
    Отправляет запрос с keep_alive=0 чтобы ollama освободил RAM/VRAM.
    Возвращает True если успешно.
    """
    global _model_loaded
    payload = {
        "model": OLLAMA_MODEL,
        "keep_alive": 0,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OLLAMA_URL}/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    _model_loaded = False
                    logger.info(f"Модель {OLLAMA_MODEL} выгружена из памяти")
                    return True
                else:
                    body = await resp.text()
                    logger.warning(f"Не удалось выгрузить модель: {resp.status} {body[:200]}")
    except Exception as e:
        logger.warning(f"Ошибка при выгрузке модели: {e}")
    return False
