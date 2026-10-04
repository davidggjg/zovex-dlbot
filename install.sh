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

# ---------------------------------------------------------------- python
# Ubuntu 20.04 עדיין על Python 3.8, ו-yt-dlp/aiogram דורשים 3.9+.
# deadsnakes לא בונה ל-ARM, אז מביאים בניית CPython עצמאית — בלי קומפילציה.
PY_MIN=310
PY_BIN=$(command -v python3)

py_version() {
  "$1" -c 'import sys; print("%d%02d" % sys.version_info[:2])' 2>/dev/null || echo 0
}

# גרסה נעוצה: הורדה ישירה בלי תלות ב-API. ה-API משמש רק אם הנעיצה נעלמה.
PY_PIN_TAG=20250818
PY_PIN_VER=3.12.11

fetch_standalone_python() {
  local arch asset
  case "$ARCH" in
    aarch64|arm64) arch=aarch64-unknown-linux-gnu ;;
    x86_64|amd64)  arch=x86_64-unknown-linux-gnu  ;;
    *) die "אין בניית Python מוכנה ל-$ARCH. שדרג את המערכת ל-Ubuntu 22.04+." ;;
  esac

  if [[ -x /opt/python/bin/python3 ]] && (( $(py_version /opt/python/bin/python3) >= PY_MIN )); then
    PY_BIN=/opt/python/bin/python3
    ok "Python עצמאי כבר מותקן ($("$PY_BIN" -V 2>&1))"
    return
  fi

  say "ההפצה מספקת רק $(python3 -V 2>&1 | cut -d' ' -f2) — מביא Python מודרני ל-$arch"

  asset="https://github.com/astral-sh/python-build-standalone/releases/download/${PY_PIN_TAG}/cpython-${PY_PIN_VER}+${PY_PIN_TAG}-${arch}-install_only.tar.gz"
  if ! curl -fsIL --max-time 45 "$asset" >/dev/null 2>&1; then
    warn "הגרסה הנעוצה לא זמינה — מחפש את האחרונה דרך GitHub API"
    asset=$(curl -fsSL --max-time 60 \
      https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest \
      | python3 -c "
import json, sys
want = '$arch'
try:
    rel = json.load(sys.stdin)
except Exception:
    sys.exit(0)
hit = [a['browser_download_url'] for a in rel.get('assets', [])
       if want in a['browser_download_url']
       and a['browser_download_url'].endswith('install_only.tar.gz')
       and '/cpython-3.1' in a['browser_download_url']]
print(hit[0] if hit else '')
" 2>/dev/null) || true
  fi

  [[ -n $asset ]] || die "לא הצלחתי להשיג Python מודרני. בדוק גישה ל-github.com מהשרת."

  rm -rf /opt/python /tmp/py.tar.gz
  curl -fL --max-time 600 --retry 3 -o /tmp/py.tar.gz "$asset" \
    || die "הורדת Python נכשלה."
  mkdir -p /opt/python
  tar -xzf /tmp/py.tar.gz -C /opt/python --strip-components=1 || die "חילוץ Python נכשל."
  rm -f /tmp/py.tar.gz
  [[ -x /opt/python/bin/python3 ]] || die "חילוץ Python נכשל."
  PY_BIN=/opt/python/bin/python3
  "$PY_BIN" -c 'import ssl, sqlite3, ctypes' || die "בניית ה-Python חסרה מודולים."
  ok "הותקן $("$PY_BIN" -V 2>&1) ב-/opt/python"
}

if (( $(py_version "$PY_BIN") < PY_MIN )); then
  fetch_standalone_python
else
  ok "Python של המערכת מתאים ($("$PY_BIN" -V 2>&1))"
fi

# ---------------------------------------------------------------- venv
say "בונה סביבת Python"
rm -rf "$APP_DIR/venv"
"$PY_BIN" -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip wheel
"$APP_DIR/venv/bin/pip" install --quiet -r "$APP_DIR/requirements.txt"
ok "yt-dlp $("$APP_DIR/venv/bin/yt-dlp" --version) · aiogram $("$APP_DIR/venv/bin/python" -c 'import aiogram;print(aiogram.__version__)')"

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
if [[ -n ${API_ID:-} && -n ${API_HASH:-} ]]; then
  ANS=y
  ok "api_id/api_hash התקבלו ממשתני סביבה"
elif [[ ${SKIP_LOCAL_API:-} == 1 ]]; then
  ANS=n
else
  read -rp "להתקין את השרת המקומי? [Y/n] " ANS
fi
if [[ ${ANS:-Y} =~ ^[Yy]?$ ]]; then
  [[ -n ${API_ID:-} ]]   || read -rp "  api_id: "   API_ID
  [[ -n ${API_HASH:-} ]] || read -rp "  api_hash: " API_HASH
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
  [[ -n ${BOT_TOKEN:-} ]] || read -rp "  BOT_TOKEN (מ-@BotFather): " BOT_TOKEN
  [[ -n ${ADMIN_IDS:-} ]] || read -rp "  ADMIN_IDS (ה-user id שלך, מ-@userinfobot): " ADMIN_IDS
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
  if [[ -n ${BOT_TOKEN:-} ]]; then
    say "מנתק את הבוט מהענן כדי שיוכל לעבוד מול השרת המקומי (logOut)"
    RESP=$(curl -s --max-time 20 "https://api.telegram.org/bot$BOT_TOKEN/logOut" || true)
    case "$RESP" in
      *'"ok":true'*)        ok "הבוט נותק מהענן" ;;
      *LOGGED_OUT*|*"not found"*) ok "הבוט כבר לא מחובר לענן" ;;
      *) warn "logOut החזיר: ${RESP:0:120}" ;;
    esac
    sleep 2
  else
    warn "אם הבוט שימש קודם את api.telegram.org, הרץ פעם אחת:"
    echo "       curl -s 'https://api.telegram.org/bot<TOKEN>/logOut'"
    echo "       ואז: systemctl restart dlbot"
  fi
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
