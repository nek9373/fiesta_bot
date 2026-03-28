#!/bin/bash
# Честный перезапуск фиеста-бота
# Убивает ВСЕ процессы, ждёт смерти, запускает один

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "[restart] Ищу процессы bot.py..."
PIDS=$(pgrep -f "python.*fiesta_bot.*bot.py" 2>/dev/null || true)

if [ -n "$PIDS" ]; then
    echo "[restart] Найдены PID: $PIDS"
    echo "[restart] Отправляю SIGTERM..."
    kill $PIDS 2>/dev/null || true

    # Ждём до 5 секунд
    for i in $(seq 1 10); do
        sleep 0.5
        ALIVE=$(pgrep -f "python.*fiesta_bot.*bot.py" 2>/dev/null || true)
        if [ -z "$ALIVE" ]; then
            echo "[restart] Все процессы завершились"
            break
        fi
        if [ $i -eq 10 ]; then
            echo "[restart] Не умерли, SIGKILL..."
            kill -9 $ALIVE 2>/dev/null || true
            sleep 1
        fi
    done
else
    echo "[restart] Процессов не найдено"
fi

# Финальная проверка
STILL=$(pgrep -f "python.*fiesta_bot.*bot.py" 2>/dev/null || true)
if [ -n "$STILL" ]; then
    echo "[restart] ОШИБКА: процессы всё ещё живы: $STILL"
    exit 1
fi

# Загружаем .env если есть
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

echo "[restart] Запускаю bot.py..."
nohup .venv/bin/python bot.py >> fiesta.log 2>&1 &
NEW_PID=$!
echo "[restart] PID: $NEW_PID"

# Ждём 3 секунды и проверяем что жив
sleep 3
if kill -0 $NEW_PID 2>/dev/null; then
    # Проверяем лог на ошибки
    ERRORS=$(tail -5 fiesta.log | grep -i "error\|traceback" || true)
    if [ -n "$ERRORS" ]; then
        echo "[restart] ВНИМАНИЕ — ошибки в логе:"
        echo "$ERRORS"
    else
        echo "[restart] Бот запущен и работает"
        tail -3 fiesta.log
    fi
else
    echo "[restart] ОШИБКА: процесс умер сразу после запуска"
    tail -10 fiesta.log
    exit 1
fi
