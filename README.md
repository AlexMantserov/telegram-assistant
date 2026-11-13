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

## Установка на сервер (из PowerShell на Windows)

Сервер: Ubuntu/Debian, вход по SSH под root. Скрипт `deploy/install.sh` всё делает сам:
ставит пакеты, скачивает код из GitHub, создаёт Python-окружение и службу `telegram-assistant`.

1. Скачайте `deploy/install.sh` и заполненный `.env` в одну папку на компьютере, откройте там PowerShell.
2. Загрузите файлы на сервер (вместо `IP` подставьте адрес сервера):
   ```powershell
   scp .\install.sh root@IP:/root/install.sh
   scp .\.env root@IP:/root/telegram-assistant.env
   ```
3. Запустите установку:
   ```powershell
   ssh -t root@IP "sed -i 's/\r$//' /root/install.sh && bash /root/install.sh"
   ```
4. При первом запуске скрипт покажет ключ `ssh-ed25519 ...`. Добавьте его в GitHub:
   репозиторий → Settings → Deploy keys → Add deploy key (галочку «Allow write access» не ставить).
   Затем повторите команду из пункта 3.

В конце скрипт проверит, пускает ли Anthropic запросы с сервера, и покажет статус службы.
При старте бот пишет вам «Бот запущен ✅».

### Прокси
Если сервер в России, укажите в `.env` прокси `PROXY_URL=socks5://логин:пароль@адрес:порт`.
Он используется и для Telegram, и для Claude. Для Claude прокси должен быть зарубежным,
иначе Anthropic ответит ошибкой 403. Отдельные прокси: `TELEGRAM_PROXY` и `ANTHROPIC_PROXY`.

### Полезные команды на сервере
| Что сделать | Команда |
|---|---|
| Логи в реальном времени | `journalctl -u telegram-assistant -f` |
| Перезапуск после правки `.env` | `systemctl restart telegram-assistant` |
| Обновить бота до новой версии | `bash /root/install.sh` |
| Править настройки | `nano /opt/telegram-assistant/.env` |

### Запуск через Docker (необязательно)
```bash
cp .env.example .env && nano .env
docker compose up -d --build
```

## Безопасность
Файл `.env` хранит секреты и не попадает в git (он в `.gitignore`).
Сообщения от чужих пользователей бот молча игнорирует.
