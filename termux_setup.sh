#!/usr/bin/env bash
# NafPT — تجهيز Termux (بدون Docker). شغّله مرة واحدة: bash termux_setup.sh
set -e
cd "$(dirname "$0")"
echo "== تثبيت الحزم =="
pkg update -y
pkg install -y python tmux
pkg install -y termux-api 2>/dev/null || echo "ℹ️ termux-api مش متثبّت (اختياري: wake lock). ثبّت تطبيق Termux:API كمان لو عايزه."
python -m pip install --upgrade pip
pip install -r requirements.txt
mkdir -p data/logs data/backups
[ -f .env ] || { cp .env.example .env; echo "اتعمل .env جديد: عدّله بـ nano .env"; }
chmod +x run_forever.sh nafpt.sh
echo ""
echo "✅ التجهيز خلص. الخطوات الجاية:"
echo "1) nano .env          (TELEGRAM_BOT_TOKEN و GEMINI_API_KEY)"
echo "2) python check_setup.py   (يتأكد من التوكن و@NafPTbot)"
echo "3) ./nafpt.sh start        (يشغّله في tmux)"
