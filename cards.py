"""
Банк персонажей для игры Fiesta.
~580 персонажей по 8 категориям.
"""

import json
import logging
import random
import re
from pathlib import Path

logger = logging.getLogger(__name__)

CHARACTERS_FILE = Path(__file__).parent / "characters.json"

_characters_cache: dict[str, list[str]] | None = None


def _load_characters() -> dict[str, list[str]]:
    global _characters_cache
    if _characters_cache is not None:
        return _characters_cache

    try:
        with open(CHARACTERS_FILE, 'r', encoding='utf-8') as f:
            _characters_cache = json.load(f)
        total = sum(len(v) for v in _characters_cache.values())
        logger.info(f"Загружено {total} персонажей из {len(_characters_cache)} категорий")
    except Exception as e:
        logger.error(f"Ошибка загрузки characters.json: {e}")
        _characters_cache = {}
    return _characters_cache


def _normalize(text: str) -> str:
    text = text.strip()
    text = re.sub(r'\s+', ' ', text)
    text = text.strip('"\'«»')
    return text


def _validate_character(text: str) -> bool:
    if not text or len(text) < 2 or len(text) > 100:
        return False
    if not re.search(r'[a-zA-Zа-яА-ЯёЁ]', text):
        return False
    return True


# Маппинг категорий на ключи в JSON
CATEGORY_MAP = {
    "mixed": None,           # все
    "books": ["books"],
    "movies": ["movies"],
    "series": ["series"],
    "cartoons": ["cartoons"],
    "anime": ["anime"],
    "games": ["games"],
    "mythology": ["mythology"],
    "history": ["history"],
    "fiction": ["books", "movies", "series", "cartoons", "anime", "games"],
    "real": ["history", "mythology"],
}


def get_characters(category: str = "mixed", count: int = 8,
                   custom: list[str] | None = None,
                   source: str = "default") -> list[str]:
    """
    Получить список уникальных персонажей.

    Args:
        category: ключ из CATEGORY_MAP или "mixed"
        count: сколько нужно
        custom: пользовательские персонажи
        source: "default", "custom", "mixed"
    """
    pool: list[str] = []
    chars_db = _load_characters()

    if source in ("default", "mixed"):
        cats = CATEGORY_MAP.get(category)
        if cats is None:
            # Все категории
            for chars in chars_db.values():
                pool.extend(chars)
        else:
            for cat_key in cats:
                pool.extend(chars_db.get(cat_key, []))

    if source in ("custom", "mixed") and custom:
        for c in custom:
            normalized = _normalize(c)
            if _validate_character(normalized):
                pool.append(normalized)

    # Дедупликация
    seen = set()
    unique = []
    for c in pool:
        key = c.lower().strip()
        if key not in seen:
            seen.add(key)
            unique.append(c)

    if len(unique) < count:
        logger.warning(f"Мало персонажей: {len(unique)}, нужно {count}. Добираем из всех.")
        for chars in chars_db.values():
            for c in chars:
                key = c.lower().strip()
                if key not in seen:
                    seen.add(key)
                    unique.append(c)

    random.shuffle(unique)
    result = unique[:count]
    logger.info(f"Выдано {len(result)} персонажей (source={source}, category={category})")
    return result


def get_all_categories() -> list[str]:
    """Список доступных категорий."""
    return list(_load_characters().keys())


def get_category_count(category: str = "mixed") -> int:
    """Сколько персонажей в категории."""
    chars_db = _load_characters()
    cats = CATEGORY_MAP.get(category)
    if cats is None:
        return sum(len(v) for v in chars_db.values())
    return sum(len(chars_db.get(k, [])) for k in cats)
