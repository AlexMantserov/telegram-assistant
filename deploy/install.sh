#!/usr/bin/env bash
# Установка и обновление Telegram-бота на сервере Ubuntu/Debian (запускать от root).
#   1) с компьютера загрузить этот файл в /root/ сервера, а файл .env — как /root/telegram-assistant.env;
#   2) ssh -t root@IP "bash /root/install.sh"
# Повторный запуск обновляет бота до последней версии из GitHub.
set -euo pipefail

APP="/opt/telegram-assistant"
SVC="telegram-assistant"
RUN_USER="tgbot"
REPO="git@github-tgbot:AlexMantserov/telegram-assistant.git"
KEY="/root/.ssh/tgbot_deploy"

[ "$(id -u)" -eq 0 ] || { echo "Запустите от root."; exit 1; }

echo "== 1. Пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv curl >/dev/null
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || { echo "Нужен Python 3.10 или новее, на сервере $(python3 -V)."; exit 1; }

echo "== 2. Ключ доступа к GitHub"
mkdir -p /root/.ssh && chmod 700 /root/.ssh
[ -f "$KEY" ] || ssh-keygen -q -t ed25519 -N '' -f "$KEY" -C "telegram-assistant-$(hostname)"
grep -q "Host github-tgbot" /root/.ssh/config 2>/dev/null \
  || printf 'Host github-tgbot\n  HostName github.com\n  User git\n  IdentityFile %s\n  IdentitiesOnly yes\n' "$KEY" >> /root/.ssh/config
grep -q "^github.com" /root/.ssh/known_hosts 2>/dev/null || ssh-keyscan -t ed25519 github.com 2>/dev/null >> /root/.ssh/known_hosts
if ! git ls-remote "$REPO" >/dev/null 2>&1; then
  echo
  echo "Сервер пока не может читать репозиторий. Добавьте этот ключ в GitHub:"
  echo "репозиторий telegram-assistant → Settings → Deploy keys → Add deploy key (галочку записи не ставить)"
  echo
  cat "$KEY.pub"
  echo
  echo "Затем запустите скрипт ещё раз."
  exit 1
fi

echo "== 3. Код бота"
id "$RUN_USER" >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin "$RUN_USER"
if [ -d "$APP/.git" ]; then
  # Папка принадлежит пользователю бота, а git запускается от root — разрешаем явно.
  git -c safe.directory="$APP" -C "$APP" fetch -q origin main
  git -c safe.directory="$APP" -C "$APP" reset -q --hard origin/main
else
  git clone -q "$REPO" "$APP"
fi

echo "== 4. Python-окружение"
[ -d "$APP/.venv" ] || python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install -q --upgrade pip
"$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt"

echo "== 5. Настройки (.env)"
if [ -f /root/telegram-assistant.env ]; then
  mv -f /root/telegram-assistant.env "$APP/.env"
fi
if [ ! -f "$APP/.env" ]; then
  cp "$APP/.env.example" "$APP/.env"
  echo "Создан $APP/.env из шаблона. Заполните его (nano $APP/.env) и запустите скрипт ещё раз."
  exit 1
fi
sed -i 's/\r$//' "$APP/.env"   # на случай, если файл правили в Windows
chown -R "$RUN_USER:$RUN_USER" "$APP"
chmod 600 "$APP/.env"

echo "== 6. Проверка доступа к Claude"
PROXY_URL=$(grep -E '^(ANTHROPIC_PROXY|PROXY_URL)=.+' "$APP/.env" | sort | head -1 | cut -d= -f2- || true)
[ "${PROXY_URL,,}" = "none" ] && PROXY_URL=""
CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 ${PROXY_URL:+--proxy "$PROXY_URL"} https://api.anthropic.com/v1/models || echo 000)
case "$CODE" in
  401) echo "api.anthropic.com доступен${PROXY_URL:+ через прокси}." ;;
  403) echo "ВНИМАНИЕ: Anthropic отклоняет запросы с этого адреса (403, регион не поддерживается). Нужен зарубежный прокси." ;;
  *)   echo "ВНИМАНИЕ: api.anthropic.com не ответил (код $CODE). Проверьте прокси." ;;
esac

echo "== 7. Служба systemd"
cp "$APP/deploy/telegram-bot.service" "/etc/systemd/system/$SVC.service"
systemctl daemon-reload
systemctl enable -q "$SVC"
systemctl restart "$SVC"
sleep 3
systemctl --no-pager --lines=15 status "$SVC" || true
echo
echo "Готово. Логи: journalctl -u $SVC -f"
