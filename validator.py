"""Proposal policy guard. Returns (errors, warnings). Errors = must not be sent as-is."""
import re

import config

_AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_AR_FOLD = str.maketrans({"ة": "ه", "أ": "ا", "إ": "ا", "آ": "ا", "ى": "ي"})

_PHONE = re.compile(r"(?:\+?\d[\s\-().]?){9,}")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_LINK = re.compile(r"(https?://|www\.|\b[\w-]+\.(?:com|app|dev|me|org|io/)\b)", re.I)
_HANDLE = re.compile(r"(?<![\w@])@\w{3,}")
_OFFSITE = re.compile(
    r"(كلمني|كلمنى|تواصل معي|تواصل معى|تواصل معايا|راسلني|خارج المنصه|خارج الموقع|على الخاص|"
    r"contact me (?:on|via|at|directly)|reach me (?:on|at|via)|message me on|dm me|my (?:phone|number|whatsapp|email)|"
    r"outside (?:the )?(?:platform|nafezly)|off[- ]platform)", re.I)
_PLACEHOLDER = re.compile(r"(\[[^\]\n]{2,40}\]|\{\{.*?\}\}|<[^>\n]{2,30}>|lorem ipsum|your name|اسمك هنا)", re.I)
_SENIOR = re.compile(r"(\bsenior\b|\blead architect\b|سينيور|خبير\s+(?:محترف|عالمي))", re.I)
_YEARS = re.compile(
    r"(\d+\s*\+?\s*(?:years?|yrs)\s+(?:of\s+)?(?:professional\s+|commercial\s+)?experience|"
    r"(?:خبره|خبرت\w*)\s*(?:ل|لمده|تزيد عن|اكتر من|أكثر من)?\s*\d+\s*(?:سنين|سنوات|سنه|عام)|"
    r"\d+\s*(?:سنين|سنوات|سنه|اعوام|أعوام)\s*(?:من\s*)?(?:ال)?خبر)", re.I)
_PLATFORMS = re.compile(r"(واتس\s?اب|whats\s?app|تليجرام|تيليجرام|telegram|سكايب|skype|فيسبوك|facebook|انستجرام|instagram)", re.I)
_GUARANTEE = re.compile(r"(مضمون|ضمان النتيجه|guaranteed?|100%\s*(?:success|satisfaction))", re.I)
_FAKE_PROOF = re.compile(r"(عملاء(?:ي)? (?:السابقين )?(?:قالوا|اشادوا)|testimonial|clients? (?:say|said)|رضا العملاء)", re.I)


def validate(text, language=None):
    """Return (errors, warnings)."""
    errors, warnings = [], []
    raw = text or ""
    t = raw.translate(_AR_DIGITS)
    folded = t.translate(_AR_FOLD)
    if not raw.strip():
        return ["نص فاضي"], []
    if _PHONE.search(t):
        errors.append("فيه رقم تليفون")
    if _EMAIL.search(t):
        errors.append("فيه إيميل")
    if _LINK.search(t):
        errors.append("فيه رابط خارجي")
    if _HANDLE.search(t):
        errors.append("فيه حساب تواصل (@)")
    if _OFFSITE.search(folded):
        errors.append("دعوة للتواصل خارج المنصة")
    if _PLACEHOLDER.search(t):
        errors.append("فيه placeholder لم يُستبدل")
    if _SENIOR.search(folded):
        errors.append("ادّعاء مستوى senior (غير صحيح)")
    if _YEARS.search(folded):
        errors.append("ادّعاء سنوات خبرة محددة (غير موثّق)")
    n = len(raw.split())
    if n < config.MIN_WORDS:
        errors.append(f"قصير ({n} كلمة)")
    elif n > config.MAX_WORDS:
        errors.append(f"طويل ({n} كلمة)")
    if _PLATFORMS.search(t):
        warnings.append("ذكر منصة تواصل (راجع السياق)")
    if _GUARANTEE.search(folded):
        warnings.append("كلمة ضمان/مضمون (تجنّب الوعود)")
    if _FAKE_PROOF.search(folded):
        warnings.append("إشارة لآراء/عملاء (تأكد إنها حقيقية)")
    return errors, warnings
