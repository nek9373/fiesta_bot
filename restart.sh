#!/bin/bash
# Честный перезапуск фиеста-бота
# Использует PID-файл + fallback на pgrep по cwd

set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
PIDFILE="$DIR/.bot.pid"

# Собираем PID из двух источников: PID-файл + pgrep
echo "[restart] Ищу процессы bot.py..."
PIDS=""

# 1) PID-файл
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        PIDS="$OLD_PID"
        echo "[restart] Из PID-файла: $OLD_PID"
    else
        echo "[restart] PID-файл есть ($OLD_PID), но процесс мёртв"
        rm -f "$PIDFILE"
    fi
fi

# 2) Fallback: ищем python процессы с bot.py запущенные из этой директории
FOUND=$(pgrep -f "python.*bot\.py" 2>/dev/null || true)
for pid in $FOUND; do
    # Проверяем что cwd процесса — наша директория
    PROC_CWD=$(readlink -f /proc/$pid/cwd 2>/dev/null || true)
    if [ "$PROC_CWD" = "$DIR" ]; then
        if ! echo "$PIDS" | grep -qw "$pid" 2>/dev/null; then
            PIDS="$PIDS $pid"
            echo "[restart] Найден по pgrep+cwd: $pid"
        fi
    fi
done

PIDS=$(echo "$PIDS" | xargs)  # trim

if [ -n "$PIDS" ]; then
    echo "[restart] Убиваю: $PIDS"
    kill $PIDS 2>/dev/null || true

    # Ждём до 5 секунд
    for i in $(seq 1 10); do
        sleep 0.5
        ALL_DEAD=true
        for pid in $PIDS; do
            if kill -0 "$pid" 2>/dev/null; then
                ALL_DEAD=false
                break
            fi
        done
        if $ALL_DEAD; then
            echo "[restart] Все процессы завершились"
            break
        fi
        if [ $i -eq 10 ]; then
            echo "[restart] Не умерли, SIGKILL..."
            for pid in $PIDS; do
                kill -9 "$pid" 2>/dev/null || true
            done
            sleep 1
        fi
    done
else
    echo "[restart] Процессов не найдено"
fi

rm -f "$PIDFILE"

# Финальная проверка
for pid in $PIDS; do
    if kill -0 "$pid" 2>/dev/null; then
        echo "[restart] ОШИБКА: процесс $pid всё ещё жив"
        exit 1
    fi
done

# Загружаем .env если есть
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
fi

echo "[restart] Запускаю bot.py..."
nohup .venv/bin/python bot.py >> fiesta.log 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[restart] PID: $NEW_PID (записан в $PIDFILE)"

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
    rm -f "$PIDFILE"
    tail -10 fiesta.log
    exit 1
fi
