#!/usr/bin/env bash
# התקנה מלאה של בוט ההורדות על Ubuntu / Debian (כולל ARM / Oracle Ampere).
#   sudo bash install.sh
set -Eeuo pipefail

APP_DIR=/opt/dlbot
DATA_DIR=/var/lib/dlbot
TBA_DIR=/var/lib/telegram-bot-api
SRC_DIR=/usr/local/src/telegram-bot-api
REPO_URL="${REPO_URL:-https://github.com/davidggjg/zovex-dlbot.git}"

C_OK=$'\e[32m'; C_WARN=$'\e[33m'; C_ERR=$'\e[31m'; C_B=$'\e[1m'; C_0=$'\e[0m'
say()  { echo "${C_B}==>${C_0} $*"; }
ok()   { echo "${C_OK}  ✓${C_0} $*"; }
warn() { echo "${C_WARN}  !${C_0} $*"; }
die()  { echo "${C_ERR}  ✗ $*${C_0}" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "הרץ עם sudo."
command -v apt-get >/dev/null || die "הסקריפט נכתב ל-Ubuntu/Debian."

ARCH=$(uname -m)
say "מערכת: $(. /etc/os-release && echo "$PRETTY_NAME") · $ARCH · $(nproc) ליבות · \
$(free -m | awk '/Mem:/{print $2}')MB RAM"

# ---------------------------------------------------------------- swap
ensure_swap() {
  local mem swap
  mem=$(free -m | awk '/Mem:/{print $2}')
  swap=$(free -m | awk '/Swap:/{print $2}')
  if (( mem + swap < 4000 )) && [[ ! -f /swapfile ]]; then
    say "מוסיף 4GB swap (צריך לקומפילציה של השרת המקומי)"
    fallocate -l 4G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=4096 status=none
    chmod 600 /swapfile && mkswap -q /swapfile && swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    ok "swap פעיל"
  fi
}

# ---------------------------------------------------------------- packages
say "מתקין חבילות מערכת"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  git curl ca-certificates \
  python3 python3-venv python3-pip python3-dev \
  ffmpeg \
  build-essential cmake gperf zlib1g-dev libssl-dev >/dev/null
ok "חבילות הותקנו ($(ffmpeg -version | head -1 | cut -d' ' -f1-3))"

# ---------------------------------------------------------------- user & dirs
if ! id dlbot &>/dev/null; then
  useradd --system --home-dir "$DATA_DIR" --create-home --shell /usr/sbin/nologin dlbot
  ok "נוצר משתמש dlbot"
fi
mkdir -p "$DATA_DIR"/{downloads,cookies} "$TBA_DIR"/temp "$APP_DIR"
chown -R dlbot:dlbot "$DATA_DIR" "$TBA_DIR"
chmod 700 "$DATA_DIR/cookies"

# ---------------------------------------------------------------- code
say "מביא את הקוד"
if [[ -d $APP_DIR/.git ]]; then
  git -C "$APP_DIR" fetch --quiet origin && git -C "$APP_DIR" reset --hard --quiet origin/HEAD
  ok "הקוד עודכן"
elif [[ -f $(dirname "$(readlink -f "$0")")/app/main.py ]]; then
  cp -r "$(dirname "$(readlink -f "$0")")"/. "$APP_DIR"/
  ok "הקוד הועתק מהתיקייה המקומית"
else
  git clone --quiet --depth 1 "$REPO_URL" "$APP_DIR"
  ok "הקוד שוכפל מ-$REPO_URL"
fi

# ---------------------------------------------------------------- venv
say "בונה סביבת Python"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip wheel
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
ok "yt-dlp $("$APP_DIR/venv/bin/yt-dlp" --version)"

# ---------------------------------------------------------------- local Bot API
build_tba() {
  if [[ -x /usr/local/bin/telegram-bot-api ]]; then
    ok "telegram-bot-api כבר בנוי"
    return
  fi
  ensure_swap
  say "בונה את Telegram Bot API המקומי — זה החלק הארוך (20–60 דקות)"
  rm -rf "$SRC_DIR"
  git clone --quiet --recursive https://github.com/tdlib/telegram-bot-api.git "$SRC_DIR"
  mkdir -p "$SRC_DIR/build"
  cd "$SRC_DIR/build"
  cmake -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local .. >/dev/null
  # ליבה אחת פחות כדי שה-VPS ישאר שמיש בזמן הקומפילציה
  cmake --build . --target install -j "$(( $(nproc) > 1 ? $(nproc) - 1 : 1 ))"
  cd /
  [[ -x /usr/local/bin/telegram-bot-api ]] || die "הבנייה נכשלה."
  ok "נבנה: $(telegram-bot-api --version 2>&1 | head -1)"
}

echo
echo "${C_B}── Telegram Bot API מקומי ──${C_0}"
echo "בלעדיו: העלאה עד 50MB, הורדה עד 20MB."
echo "איתו:   העלאה עד 2000MB, הורדה ללא הגבלה."
echo "נדרשים api_id ו-api_hash מ-https://my.telegram.org (Apps → create application)."
read -rp "להתקין את השרת המקומי? [Y/n] " ANS
if [[ ${ANS:-Y} =~ ^[Yy]?$ ]]; then
  read -rp "  api_id: "   API_ID
  read -rp "  api_hash: " API_HASH
  [[ -n $API_ID && -n $API_HASH ]] || die "api_id/api_hash חסרים."
  build_tba
  cat > /etc/telegram-bot-api.env <<EOF
TELEGRAM_API_ID=$API_ID
TELEGRAM_API_HASH=$API_HASH
EOF
  chmod 600 /etc/telegram-bot-api.env
  install -m644 "$APP_DIR/systemd/telegram-bot-api.service" /etc/systemd/system/
  USE_LOCAL=true
else
  warn "מדלג — הבוט יעבוד מול הענן עם מגבלת 50MB."
  USE_LOCAL=false
fi

# ---------------------------------------------------------------- .env
if [[ ! -f $APP_DIR/.env ]]; then
  echo
  echo "${C_B}── הגדרות הבוט ──${C_0}"
  read -rp "  BOT_TOKEN (מ-@BotFather): " BOT_TOKEN
  read -rp "  ADMIN_IDS (ה-user id שלך, מ-@userinfobot): " ADMIN_IDS
  [[ -n $BOT_TOKEN ]] || die "BOT_TOKEN חסר."
  sed -e "s|^BOT_TOKEN=.*|BOT_TOKEN=$BOT_TOKEN|" \
      -e "s|^ADMIN_IDS=.*|ADMIN_IDS=$ADMIN_IDS|" \
      -e "s|^USE_LOCAL_API=.*|USE_LOCAL_API=$USE_LOCAL|" \
      -e "s|^GLOBAL_CONCURRENCY=.*|GLOBAL_CONCURRENCY=$(( $(nproc) > 2 ? 3 : 2 ))|" \
      "$APP_DIR/.env.example" > "$APP_DIR/.env"
  ok "נוצר .env"
else
  ok ".env קיים — לא נגעתי בו"
fi
chown root:dlbot "$APP_DIR/.env"
chmod 640 "$APP_DIR/.env"
chown -R root:dlbot "$APP_DIR"

# ---------------------------------------------------------------- services
say "מתקין services"
install -m644 "$APP_DIR"/systemd/dlbot.service \
              "$APP_DIR"/systemd/dlbot-cleanup.{service,timer} \
              "$APP_DIR"/systemd/dlbot-update.{service,timer} /etc/systemd/system/
systemctl daemon-reload

if [[ $USE_LOCAL == true ]]; then
  systemctl enable --now telegram-bot-api.service
  sleep 3
  if systemctl is-active --quiet telegram-bot-api; then
    ok "telegram-bot-api רץ על 127.0.0.1:8081"
  else
    journalctl -u telegram-bot-api -n 20 --no-pager
    die "telegram-bot-api לא עלה."
  fi
  warn "אם הבוט שימש קודם את api.telegram.org, הרץ פעם אחת:"
  echo "       curl -s 'https://api.telegram.org/bot<TOKEN>/logOut'"
  echo "       ואז: systemctl restart dlbot"
fi

systemctl enable --now dlbot-cleanup.timer dlbot-update.timer >/dev/null
systemctl enable --now dlbot.service
sleep 4

echo
if systemctl is-active --quiet dlbot; then
  ok "${C_B}הבוט פעיל! שלח לו /start בטלגרם.${C_0}"
else
  journalctl -u dlbot -n 30 --no-pager
  die "הבוט לא עלה — הלוג מעל."
fi

cat <<EOF

${C_B}פקודות שימושיות${C_0}
  systemctl status dlbot                 מצב
  journalctl -u dlbot -f                 לוג חי
  systemctl restart dlbot                הפעלה מחדש
  nano /opt/dlbot/.env                   שינוי מגבלות
  journalctl -u telegram-bot-api -f      לוג השרת המקומי

${C_B}עדכון הקוד${C_0}
  cd /opt/dlbot && git pull && systemctl restart dlbot
EOF
