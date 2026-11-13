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
# Новые настройки из шаблона дописываем в конец .env, существующие не трогаем
while IFS= read -r line; do
  key=${line%%=*}
  if [[ "$line" =~ ^[A-Z_]+= ]] && ! grep -q "^$key=" "$APP/.env"; then
    echo "$line" >> "$APP/.env"; echo "  добавлена настройка $key"
  fi
done < "$APP/.env.example"
# Ключ Google: берём тот же сервисный аккаунт, что у moysklad
if [ ! -f "$APP/google-key.json" ] && [ -f /opt/moysklad/google-key.json ]; then
  cp /opt/moysklad/google-key.json "$APP/google-key.json"; echo "  скопирован google-key.json из /opt/moysklad"
fi
mkdir -p "$APP/data"
chown -R "$RUN_USER:$RUN_USER" "$APP"
chmod 600 "$APP/.env"
[ -f "$APP/google-key.json" ] && chmod 600 "$APP/google-key.json"

echo "== 6. Проверка связи"
env_get() { grep -E "^$1=.+" "$APP/.env" | head -1 | cut -d= -f2- || true; }
pick_proxy() { local v; v=$(env_get "$1"); [ -n "$v" ] || v=$(env_get PROXY_URL); [ "${v,,}" = "none" ] && v=""; echo "$v"; }
check() {  # $1 — название, $2 — адрес, $3 — прокси
  local code err
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 ${3:+--proxy "$3"} "$2" 2>/tmp/tgbot_curl_err) || true
  err=$(tr -d '\n' </tmp/tgbot_curl_err)
  local via="напрямую"; [ -n "$3" ] && via="через прокси"
  echo "$1 ($via): код ${code:-нет}${err:+, ошибка: $err}"
}
TG_PROXY=$(pick_proxy TELEGRAM_PROXY); TG_PROXY=${TG_PROXY/#socks5:/socks5h:}
AN_PROXY=$(pick_proxy ANTHROPIC_PROXY); AN_PROXY=${AN_PROXY/#socks5:/socks5h:}
check "Telegram " https://api.telegram.org/bot/getMe "$TG_PROXY"
check "Anthropic" https://api.anthropic.com/v1/models "$AN_PROXY"
echo "  Telegram: код 401 или 404 — связь есть."
echo "  Anthropic: 401 — связь есть; 403 — страна не поддерживается, нужен зарубежный прокси; 000 — прокси не пропускает."
if [ -n "$(env_get MAIL_ADDRESS)" ]; then
  (cd "$APP" && runuser -u "$RUN_USER" -- .venv/bin/python -c '
from bot.config import load_settings
from bot.mail_client import MailClient, utf7_decode
s = load_settings()
c = MailClient(s.mail_address, s.mail_password, s.mail_imap_host, s.mail_imap_port, s.mail_smtp_host, s.mail_smtp_port, s.mail_spam_folder)
c.check_login()
f = c.spam_folder(); print("Почта: вход выполнен, папка спама:", utf7_decode(f) if f else "НЕ НАЙДЕНА (укажите MAIL_SPAM_FOLDER)")
' 2>&1 | tail -1) || true
fi
if [ -f "$APP/google-key.json" ]; then
  echo "Google: сервисный аккаунт $(grep -o '"client_email": *"[^"]*"' "$APP/google-key.json" | cut -d'"' -f4)"
  echo "  Откройте таблице с правилами доступ «Редактор» для этого адреса."
fi

echo "== 7. Служба systemd"
cp "$APP/deploy/telegram-bot.service" "/etc/systemd/system/$SVC.service"
systemctl daemon-reload
systemctl enable -q "$SVC"
systemctl restart "$SVC"
sleep 3
systemctl --no-pager --lines=15 status "$SVC" || true
echo
echo "Готово. Логи: journalctl -u $SVC -f"
