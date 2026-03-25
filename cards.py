"""
Банк персонажей для игры Fiesta.
"""

import json
import logging
import os
import random
import re
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CHARACTERS = {
    "books": [
        "Шерлок Холмс", "Гарри Поттер", "Дон Кихот", "Анна Каренина",
        "Родион Раскольников", "Гэндальф", "Фродо Бэггинс", "Остап Бендер",
        "Мастер", "Маргарита", "Воланд", "Кот Бегемот",
        "Онегин", "Татьяна Ларина", "Печорин", "Ромео",
        "Джульетта", "Гамлет", "Дракула", "Франкенштейн",
        "Алиса (Страна чудес)", "Винни-Пух", "Карлсон", "Незнайка",
        "Д'Артаньян", "Граф Монте-Кристо", "Робинзон Крузо", "Маленький принц",
        "Чиполлино", "Буратино", "Мэри Поппинс", "Питер Пэн",
    ],
    "movies": [
        "Дарт Вейдер", "Йода", "Индиана Джонс", "Джеймс Бонд",
        "Терминатор", "Нео (Матрица)", "Форрест Гамп", "Джокер",
        "Бэтмен", "Человек-паук", "Железный человек", "Тор",
        "Танос", "Джек Воробей", "Шрек", "Кунг-фу Панда",
        "Леон (киллер)", "Тайлер Дёрден", "Ганнибал Лектер", "Вито Корлеоне",
        "Марти Макфлай", "Эдвард Руки-ножницы", "Элли (Волшебник Изумрудного города)",
        "Балрог", "Голлум", "Рокки Бальбоа", "Джон Уик",
    ],
    "series": [
        "Уолтер Уайт", "Шелдон Купер", "Дейенерис Таргариен", "Джон Сноу",
        "Тирион Ланнистер", "Гомер Симпсон", "Рик Санчез", "Морти Смит",
        "Декстер Морган", "Доктор Хаус", "Шерлок (Камбербэтч)", "Одиннадцатый Доктор",
        "Лайт Ягами", "Наруто", "Луффи", "Геральт из Ривии",
        "Сол Гудман", "Джесси Пинкман", "Тони Сопрано", "Томас Шелби",
        "Рейчел Грин", "Барни Стинсон", "Майкл Скотт", "Дуайт Шрут",
    ],
}


def _normalize(text: str) -> str:
    """Нормализация строки персонажа."""
    text = text.strip()
    text = re.sub(r'\s+', ' ', text)
    # Убираем лишние кавычки
    text = text.strip('"\'«»')
    return text


def _validate_character(text: str) -> bool:
    """Проверка валидности персонажа."""
    if not text or len(text) < 2:
        return False
    if len(text) > 100:
        return False
    # Только пробелы/пунктуация
    if not re.search(r'[a-zA-Zа-яА-ЯёЁ]', text):
        return False
    return True


def get_characters(category: str = "mixed", count: int = 10,
                   custom: list[str] | None = None,
                   source: str = "default") -> list[str]:
    """
    Получить список персонажей для игры.

    Args:
        category: "books", "movies", "series", "mixed"
        count: сколько персонажей нужно
        custom: пользовательские персонажи
        source: "default", "custom", "mixed"

    Returns:
        Список уникальных персонажей
    """
    pool: list[str] = []

    # Встроенный банк
    if source in ("default", "mixed"):
        if category == "mixed":
            for chars in DEFAULT_CHARACTERS.values():
                pool.extend(chars)
        elif category in DEFAULT_CHARACTERS:
            pool.extend(DEFAULT_CHARACTERS[category])
        else:
            for chars in DEFAULT_CHARACTERS.values():
                pool.extend(chars)

    # Пользовательский банк
    if source in ("custom", "mixed") and custom:
        for c in custom:
            normalized = _normalize(c)
            if _validate_character(normalized):
                pool.append(normalized)

    # Дедупликация (case-insensitive)
    seen = set()
    unique_pool = []
    for c in pool:
        key = c.lower()
        if key not in seen:
            seen.add(key)
            unique_pool.append(c)

    if len(unique_pool) < count:
        logger.warning(f"В банке только {len(unique_pool)} персонажей, нужно {count}")
        # Добавляем из всех категорий
        for chars in DEFAULT_CHARACTERS.values():
            for c in chars:
                key = c.lower()
                if key not in seen:
                    seen.add(key)
                    unique_pool.append(c)

    random.shuffle(unique_pool)
    result = unique_pool[:count]
    logger.info(f"Выдано {len(result)} персонажей (source={source}, category={category})")
    return result


def load_custom_bank(filepath: str) -> list[str]:
    """Загрузить пользовательский банк из JSON."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            chars = data
        elif isinstance(data, dict) and "cards" in data:
            chars = data["cards"]
        else:
            return []
        return [_normalize(c) for c in chars if _validate_character(_normalize(str(c)))]
    except Exception as e:
        logger.error(f"Ошибка загрузки банка {filepath}: {e}")
        return []
