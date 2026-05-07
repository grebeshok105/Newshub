#!/bin/bash

# Скрипт автоматического обновления NewsHub Bot на сервере.
# Убедитесь, что архив NewsHub_Server.zip лежит в домашней директории (~/).

echo "🚀 Начинаю обновление NewsHub..."

# 1. Распаковка
if [ -f ~/NewsHub_Server.zip ]; then
    echo "📦 Распаковка архива..."
    unzip -o ~/NewsHub_Server.zip -d ~/NewsHub
else
    echo "❌ Ошибка: Файл ~/NewsHub_Server.zip не найден!"
    exit 1
fi

cd ~/NewsHub

# 2. Перезапуск Docker
echo "🔄 Перезапуск контейнеров..."
# Используем docker-compose down && up чтобы избежать ошибок старых версий
sudo docker-compose down
sudo docker-compose up -d --build

# 3. Очистка (удаляем старые образы без тегов)
echo "🧹 Очистка старых данных..."
sudo docker image prune -f

echo "✅ Бот успешно обновлен и запущен!"
