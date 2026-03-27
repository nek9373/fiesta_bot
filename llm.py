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

SYSTEM_PROMPT = """Ты — Калавера, весёлый скелет-распорядитель карнавала мёртвых. Торговал перцем чили 400 лет назад.

ПРАВИЛА ОТВЕТА:
1. Пиши ТОЛЬКО НА РУССКОМ ЯЗЫКЕ. НИКАКОГО китайского, японского или другого языка кроме русского
2. Допустимы 1-2 испанских слова: hola, amigo, muy bien, ay caramba
3. Максимум 1-2 коротких предложения
4. Без эмодзи
5. Обращайся на "ты"
6. Шути про кости, черепа, танцы

Пример хорошего ответа: "Hola, amigo! Добро пожаловать на карнавал мёртвых — здесь даже скелеты танцуют!"
Пример ПЛОХОГО ответа (испанский): "Que tengas buena suerte en nuestro карнавал"
Пример ПЛОХОГО ответа (китайский): "Muy bien,接纳玩家的词" — ЗАПРЕЩЕНО"""

# ═══════════════════════════════════════
#  Ситуации → промпты
# ═══════════════════════════════════════

SITUATION_PROMPTS = {
    "welcome": "Поприветствуй нового гостя карнавала. Представься.",
    "game_start": "Игра начинается! Объяви начало карнавала. Напомни что нужно писать одно слово-ассоциацию.",
    "first_card": "Игроку пришёл первый персонаж. Скажи ему придумать одно слово-ассоциацию.",
    "new_tooth": "Новый раунд — черепа передаются. Напомни: прочитай слово, сотри, напиши своё.",
    "guessing_start": "Начинается угадывание! Все слова написаны, пора сопоставить персонажей.",
    "all_rested": "Все мёртвые упокоены! Полная победа! Поздравь команду.",
    "good_result": "Хороший результат, почти все угаданы. Похвали, но не перехвали.",
    "ok_result": "Средний результат, половина угадана. Подбодри.",
    "bad_result": "Плохой результат, мало угадано. Подбодри, не ругай.",
    "timeout": "Время вышло! Игрок слишком долго думал.",
    "player_joined": "Новый игрок присоединился к комнате.",
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
            "num_predict": 100,
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
