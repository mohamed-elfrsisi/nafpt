"""Stage-1 deterministic filter: category matching, exclusion rules, basic profile matching.
No network, no AI. Decides whether a project is worth spending Gemini calls on."""
import re

import config
from profile_data import PRIORITY

_AR_DIAC = re.compile(r"[\u064B-\u065F\u0670\u0640]")
_AR_MAP = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})


def normalize(text):
    t = (text or "").lower()
    t = _AR_DIAC.sub("", t)
    return t.translate(_AR_MAP)


CATEGORY_TERMS = {
    "dotnet": ["c#", "csharp", "asp.net", "aspnet", ".net core", ".net", "dotnet", "entity framework", "ef core",
               "sql server", "mssql", "web api", "blazor", "linq", "سي شارب", "دوت نت", "دوت نيت"],
    "mean": ["angular", "node.js", "nodejs", "node", "express", "expressjs", "mongodb", "mongoose", "mean stack",
             "typescript", "انجولار", "نود جي اس", "مونجو"],
    "laravel": ["laravel", "php", "blade", "eloquent", "mysql", "لارافيل", "بي اتش بي"],
    "ai": ["llm", "gemini", "openai", "chatgpt", "gpt", "nlp", "embedding", "embeddings", "rag", "chatbot",
           "machine learning", "ذكاء اصطناعي", "شات بوت", "معالجه اللغه", "نماذج لغويه", "تعلم الي", "تعلم الاله"],
    "general_backend": ["backend", "back-end", "back end", "rest", "restful", "api", "crud", "authentication",
                        "authorization", "jwt", "server", "microservice", "باك اند", "سيرفر", "بوت تليجرام",
                        "telegram bot", "اتمته", "automation", "scraping", "سكرابينج"],
    "database": ["sql", "database", "postgres", "postgresql", "mysql", "mongodb", "stored procedure", "قاعده بيانات",
                 "قواعد بيانات", "داتا بيز"],
    "api_integration": ["integration", "integrate", "payment gateway", "webhook", "third-party", "third party",
                        "ربط", "تكامل", "بوابه دفع", "بوابات الدفع"],
}
# generic software signals (not skills): used only to avoid rejecting real software projects
FULLSTACK_TERMS = ["full stack", "fullstack", "full-stack", "web application", "web app", "website", "dashboard",
                   "admin panel", "erp", "crm", "hr system", "inventory", "لوحه تحكم", "تطبيق ويب", "موقع",
                   "منصه", "نظام ادارة", "نظام", "فرونت اند", "frontend", "front-end", "react", "vue"]
DEV_SIGNAL_TERMS = FULLSTACK_TERMS + ["application", "software", "programming", "developer", "development", "script",
                                      "bot", "app", "تطبيق", "برمجه", "برمج", "مبرمج", "مطور", "سكريبت", "بوت"]

EXCLUSIONS = {
    "تصميم/جرافيك": ["logo", "لوجو", "شعار", "graphic design", "تصميم جرافيك", "photoshop", "illustrator", "بوستر", "هويه بصريه"],
    "فيديو/مونتاج": ["video editing", "مونتاج", "motion graphics", "موشن جرافيك", "تعديل فيديو"],
    "ترجمة/تفريغ": ["translation", "translator", "ترجمه", "تفريغ", "transcription", "تدقيق لغوي", "proofreading"],
    "إدخال بيانات/إداري": ["data entry", "ادخال بيانات", "تسجيل بيانات", "مساعد افتراضي", "virtual assistant"],
    "تسويق/محتوى": ["كتابه محتوى", "كتابه مقال", "content writing", "copywriting", "social media management",
                    "ادارة صفحات", "تسويق الكتروني", "seo"],
    "هاردوير": ["arduino", "raspberry", "embedded", "iot device", "هاردوير", "دوائر الكترونيه", "plc"],
    "أبحاث ML متقدمة": ["تدريب نموذج", "train a model from scratch", "deep learning research", "computer vision research"],
}
OPTIONAL_EXCLUSIONS = {
    "wordpress": ("ووردبريس/WordPress", ["wordpress", "ووردبريس", "وورد بريس", "woocommerce", "elementor"]),
    "mobile": ("تطبيقات جوال أصلية", ["android app", "ios app", "flutter", "react native", "kotlin", "swift",
                                      "تطبيق اندرويد", "تطبيق ايفون", "تطبيق جوال", "تطبيقات الجوال", "تطبيق موبايل"]),
}


def _compile(term):
    t = normalize(term)
    if re.search(r"[a-z]", t):
        return re.compile(r"(?<![a-z0-9])" + re.escape(t) + r"s?(?![a-z0-9])")
    return re.compile(re.escape(t))


_CACHE = {}


def _match(term, text):
    pat = _CACHE.get(term)
    if pat is None:
        pat = _CACHE[term] = _compile(term)
    return pat.search(text) is not None


def classify(title, body):
    text = normalize(f"{title or ''}\n{body or ''}")
    cats = {}
    for cat, terms in CATEGORY_TERMS.items():
        hits = [t for t in terms if _match(t, text)]
        if hits:
            cats[cat] = hits
    fullstack_hits = [t for t in FULLSTACK_TERMS if _match(t, text)]
    if fullstack_hits and (len(cats) >= 2 or any(c in cats for c in ("mean", "laravel", "dotnet"))):
        cats["fullstack"] = fullstack_hits
    strong = {h for c, hs in cats.items() if c != "fullstack" for h in hs}
    dev_signal = bool(strong or fullstack_hits or any(_match(t, text) for t in DEV_SIGNAL_TERMS))

    excluded = []
    for reason, terms in EXCLUSIONS.items():
        if any(_match(t, text) for t in terms):
            excluded.append(reason)
    for key, (reason, terms) in OPTIONAL_EXCLUSIONS.items():
        enabled = config.ENABLE_WORDPRESS if key == "wordpress" else config.ENABLE_MOBILE
        if not enabled and any(_match(t, text) for t in terms):
            excluded.append(reason)

    ordered = sorted(cats, key=lambda c: (PRIORITY.get(c, 99), -len(cats[c])))
    if excluded and len(strong) < 2:
        decision, reason = "reject", "استبعاد: " + "، ".join(excluded)
    elif not strong and not dev_signal:
        decision, reason = "reject", "مفيش مؤشر لشغل برمجي في وصف المشروع"
    else:
        decision, reason = "pass", ""
    return {"categories": ordered, "matched_terms": sorted(strong), "excluded": excluded,
            "dev_signal": dev_signal, "decision": decision, "reason": reason}
