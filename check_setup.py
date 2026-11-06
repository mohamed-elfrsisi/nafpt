"""Verifies Telegram (token, bot identity, webhook, chat id) and Gemini WITHOUT printing any secret.
Usage: python check_setup.py [--send-test]"""
import sys

import httpx

import config
import logging_setup

logging_setup.register_secret(config.TG_TOKEN)
logging_setup.register_secret(config.GEMINI_KEY)


def tg(method, **params):
    r = httpx.post(f"https://api.telegram.org/bot{config.TG_TOKEN}/{method}", json=params, timeout=20)
    return r.status_code, r.json() if r.headers.get("content-type", "").startswith("application/json") else {}


def main():
    send = "--send-test" in sys.argv
    errors, warnings = config.validate()
    print(f"== {config.APP_NAME} setup check ==")
    for e in errors:
        print("❌ config:", e)
    for w in warnings:
        print("⚠️ config:", w)
    if not config.TG_TOKEN:
        sys.exit(2)
    try:
        code, data = tg("getMe")
    except httpx.HTTPError as e:
        print("❌ Telegram: تعذّر الاتصال:", logging_setup.safe_exc(e))
        sys.exit(1)
    if code != 200 or not data.get("ok"):
        print(f"❌ Telegram getMe فشل (HTTP {code}): التوكن غلط أو اتلغى")
        sys.exit(1)
    me = data["result"]
    print(f"✅ getMe: @{me['username']} (bot id {me['id']})")
    if me["username"].lower() != config.EXPECTED_BOT_USERNAME.lower():
        print(f"❌ ده مش @{config.EXPECTED_BOT_USERNAME}! التوكن بتاع بوت تاني.")
    code, wh = tg("getWebhookInfo")
    url = (wh.get("result") or {}).get("url")
    print("⚠️ فيه webhook مضبوط، ده بيمنع getUpdates (long polling) وبيخلي البوت ميردش." if url else "✅ مفيش webhook (long polling سليم)")
    if config.TG_CHAT_ID:
        if config.TG_CHAT_ID == me["id"]:
            print("❌ TELEGRAM_CHAT_ID هو رقم البوت نفسه مش رقمك.")
        elif send:
            code, d = tg("sendMessage", chat_id=config.TG_CHAT_ID, text="✅ NafPT setup check: الرسالة دي وصلت.")
            print("✅ رسالة الاختبار اتبعتت" if d.get("ok") else f"❌ الإرسال فشل: {d.get('description')}")
        else:
            print("ℹ️ Chat ID متظبط (استخدم --send-test عشان تتأكد بإرسال رسالة فعلية)")
    else:
        code, up = tg("getUpdates", limit=5, timeout=0)
        found = {m["message"]["chat"]["id"] for m in up.get("result", []) if m.get("message")}
        if code == 409:
            print("⚠️ getUpdates رجّع 409 Conflict: فيه نسخة تانية شغّالة بنفس التوكن.")
        print("ℹ️ ابعت /start للبوت. Chat IDs ظاهرة في آخر الرسائل:", sorted(found) or "لسه مفيش")
    if config.GEMINI_KEY:
        try:
            r = httpx.get(f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}",
                          headers={"x-goog-api-key": config.GEMINI_KEY}, timeout=20)
            print(f"{'✅' if r.status_code == 200 else '❌'} Gemini {config.GEMINI_MODEL}: HTTP {r.status_code}")
        except httpx.HTTPError as e:
            print("❌ Gemini:", logging_setup.safe_exc(e))


if __name__ == "__main__":
    main()
