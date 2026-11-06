#!/usr/bin/env bash
# NafPT control script (Termux + tmux). Usage: ./nafpt.sh {start|stop|restart|status|logs|attach|check|update <zip>}
cd "$(dirname "$0")"
S=nafpt
case "$1" in
  start)
    command -v tmux >/dev/null || { echo "ثبّت tmux: pkg install tmux"; exit 1; }
    tmux has-session -t $S 2>/dev/null && { echo "شغّال بالفعل (./nafpt.sh attach)"; exit 0; }
    tmux new-session -d -s $S "./run_forever.sh"; echo "✅ NafPT اشتغل في tmux (الجلسة: $S). السجل: ./nafpt.sh logs" ;;
  stop)
    touch data/STOP
    pkill -TERM -f "python main.py" 2>/dev/null
    sleep 3; tmux kill-session -t $S 2>/dev/null; rm -f data/STOP; echo "⏹ NafPT اتوقف" ;;
  restart) "$0" stop; "$0" start ;;
  status)
    tmux has-session -t $S 2>/dev/null && echo "tmux: شغّال" || echo "tmux: مش شغّال"
    pgrep -f "python main.py" >/dev/null && echo "process: شغّال" || echo "process: مش شغّال"
    if [ -f data/heartbeat ]; then echo "آخر نبضة: منذ $(( $(date +%s) - $(stat -c %Y data/heartbeat) )) ثانية"; fi ;;
  logs) tail -n 80 -f data/logs/nafpt.log ;;
  attach) tmux attach -t $S ;;
  check) python check_setup.py && python check_site.py ;;
  update)
    zip="$2"; [ -f "$zip" ] || { echo "استخدم: ./nafpt.sh update /path/NafPT-Production.zip"; exit 1; }
    "$0" stop
    ts=$(date +%Y%m%d-%H%M%S); mkdir -p data/backups
    cp .env "data/backups/env-$ts.bak" 2>/dev/null; cp profile.md "data/backups/profile-$ts.bak" 2>/dev/null
    cp policies.md "data/backups/policies-$ts.bak" 2>/dev/null; cp data/bot.db "data/backups/bot-$ts.db" 2>/dev/null
    tmp=$(mktemp -d); unzip -q "$zip" -d "$tmp"
    src=$(dirname "$(find "$tmp" -maxdepth 3 -name main.py | head -1)")
    for f in "$src"/* "$src"/.[!.]*; do
      n=$(basename "$f"); case "$n" in data|.env|profile.md|policies.md) continue;; esac
      cp -r "$f" .
    done
    rm -rf "$tmp"; pip install -r requirements.txt && echo "✅ اتحدّث (الـ .env والبيانات والبروفايل محفوظين). شغّل: ./nafpt.sh start" ;;
  *) echo "usage: $0 {start|stop|restart|status|logs|attach|check|update <zip>}" ;;
esac
