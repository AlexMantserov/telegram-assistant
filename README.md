# Telegram-бот помощник

Личный бот: отвечает только владельцу и передаёт сообщения в Claude.
Сейчас это шаг 1 (каркас). Дальше добавятся почта reg.ru и Google Таблицы.

## Команды
- `/start` или `/help` — подсказка
- `/ping` — проверить, что бот жив
- `/reset` — начать диалог с Claude заново
- любой текст — вопрос или задача для Claude

## Что нужно заранее
1. Токен бота: в Telegram напишите @BotFather → `/newbot`.
2. Ваш Telegram ID: напишите боту @userinfobot, он пришлёт число.
3. Ключ API Anthropic: https://console.anthropic.com/settings/keys (на счёте должны быть средства).

## Установка на сервер

### Вариант А: Docker (проще всего)
```bash
git clone <ссылка на репозиторий> /opt/telegram-bot
cd /opt/telegram-bot
cp .env.example .env && nano .env      # вписать токен, ID и ключ
docker compose up -d --build
docker compose logs -f                 # посмотреть логи
```

### Вариант Б: без Docker (systemd)
Нужен Python 3.11 или новее.
```bash
git clone <ссылка на репозиторий> /opt/telegram-bot
cd /opt/telegram-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env
.venv/bin/python -m bot.main           # пробный запуск, остановить Ctrl+C

# запуск как службы (в файле поменяйте User= на своего пользователя)
sudo cp deploy/telegram-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now telegram-bot
journalctl -u telegram-bot -f          # логи
```

При старте бот пишет вам «Бот запущен ✅». Если сообщения нет, смотрите логи.

## Обновление
```bash
cd /opt/telegram-bot && git pull
docker compose up -d --build           # или: sudo systemctl restart telegram-bot
```

## Безопасность
Файл `.env` хранит секреты и не попадает в git (он в `.gitignore`).
Сообщения от чужих пользователей бот молча игнорирует.
