#!/usr/bin/env bash
# يشغّل NafPT ويعيد تشغيله عند الأعطال (مع تأخير متزايد). للإيقاف: ./nafpt.sh stop
cd "$(dirname "$0")"
mkdir -p data/logs
LOG=data/logs/run_forever.log
if command -v termux-wake-lock >/dev/null 2>&1; then termux-wake-lock; else echo "termux-api مش موجود: wake lock مش مفعّل" >> "$LOG"; fi
CHILD=""
trap '[ -n "$CHILD" ] && kill -TERM "$CHILD" 2>/dev/null; command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock; exit 0' INT TERM
delay=5
rm -f data/STOP
while [ ! -f data/STOP ]; do
  start=$(date +%s)
  python main.py >> "$LOG" 2>&1 &
  CHILD=$!
  wait "$CHILD"; code=$?
  CHILD=""
  ran=$(( $(date +%s) - start ))
  [ -f data/STOP ] && break
  if [ "$code" -eq 2 ]; then echo "$(date) إعدادات ناقصة (exit 2): مش هعيد التشغيل" >> "$LOG"; break; fi
  if [ "$code" -eq 3 ]; then echo "$(date) نسخة تانية شغّالة (exit 3): مش هعيد التشغيل" >> "$LOG"; break; fi
  if [ "$ran" -gt 120 ]; then delay=5; else delay=$(( delay * 2 > 120 ? 120 : delay * 2 )); fi
  echo "$(date) exit=$code بعد ${ran}s؛ إعادة تشغيل بعد ${delay}s" >> "$LOG"
  sleep "$delay"
done
rm -f data/STOP
command -v termux-wake-unlock >/dev/null 2>&1 && termux-wake-unlock
