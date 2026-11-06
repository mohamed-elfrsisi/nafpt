import asyncio
import json
from types import SimpleNamespace

import config
import db
import validator

BASE_AR = (
    "السلام عليكم، قرأت وصف مشروعك بعناية وفهمت أنك تحتاج إلى واجهة برمجية منظمة مع قاعدة بيانات واضحة وقابلة للتوسع. "
    "أقترح بناء الواجهة باستخدام ASP.NET Core مع SQL Server وتقسيم العمل إلى مراحل صغيرة قابلة للمراجعة بعد كل مرحلة. "
    "عملت ضمن فريق على منصة تعليمية مشابهة باستخدام نفس التقنيات وتعلمت كثيرا من تلك التجربة العملية. "
    "سأسلم النتائج على دفعات وأوضح لك التقدم باستمرار حتى تكون على اطلاع كامل بكل خطوة. ")
BASE_EN = (
    "Hello, I carefully read your project description and understood that you need a clean backend API with a well "
    "structured database that can grow later. I would build it with ASP.NET Core and SQL Server, splitting the work into "
    "small milestones that you can review after each step. I worked inside a team on a similar study platform using the "
    "same technologies and learned a lot from that experience. I will deliver in stages and keep you updated all the way. ")


def proposal_ar(sign="تحياتي، محمد ياسر الفرسيسي"):
    return BASE_AR + "هل لديك مخطط حالي لقاعدة البيانات أم نبدأ من الصفر؟ " + BASE_AR[:200] + " " + sign


def proposal_en(sign="Best regards, Mohamed Yasser Elfarsisi"):
    return BASE_EN + "Do you already have a database schema or should we start from scratch? " + BASE_EN[:200] + " " + sign


assert validator.validate(proposal_ar())[0] == [], validator.validate(proposal_ar())
assert validator.validate(proposal_en())[0] == [], validator.validate(proposal_en())


def run(coro):
    return asyncio.run(coro)


def listing_html(ids, next_page=None):
    links = "".join(f'<a href="/project/{i}-slug">p{i}</a>' for i in ids)
    nxt = f'<a rel="next" href="/projects?page={next_page}">التالي</a>' if next_page else ""
    return f"<html><body><nav><a href='/about'>about</a></nav>{links}{nxt}</body></html>"


def detail_html(title, desc, budget="$250 - $500", duration="20 يوم", status="مفتوح", offers="3",
                skills=("C#", "ASP.NET Core"), published="منذ يومين"):
    skills_html = "".join(f"<span>{s}</span>" for s in skills)
    parts = [f"<html><head><title>{title} - نافذلي</title></head><body><nav>menu</nav><main><h1>{title}</h1>"]
    if status is not None:
        parts.append(f"<div>حالة المشروع</div><div>{status}</div>")
    parts.append(f"<div>تاريخ النشر</div><div>{published}</div>")
    if budget is not None:
        parts.append(f"<div>الميزانية</div><div>{budget}</div>")
    if duration is not None:
        parts.append(f"<div>مدة التنفيذ</div><div>{duration}</div>")
    if offers is not None:
        parts.append(f"<div>عدد العروض</div><div>{offers}</div>")
    if skills:
        parts.append(f"<h2>المهارات</h2>{skills_html}")
    parts.append(f"<h2>تفاصيل المشروع</h2><p>{desc}</p></main><footer>foot</footer></body></html>")
    return "".join(parts)


# ---------------------------------------------------------------- fake Gemini
class FakeGemini:
    """Replaces llm._post_once. Behaves like a model; `vulnerable` makes it obey injected phone numbers."""

    def __init__(self):
        self.calls = []
        self.vulnerable = True

    async def __call__(self, body):
        system = body["systemInstruction"]["parts"][0]["text"]
        prompt = body["contents"][0]["parts"][0]["text"]
        if "You evaluate freelance projects" in system:
            kind = "analysis"
            title = prompt.split("TITLE:", 1)[-1][:120]
            hi = "ASP.NET" in title or "API" in title
            lo = "Excel" in title or "منخفض" in title
            s = 9 if hi else 2 if lo else 6
            out = json.dumps({
                "categories": ["dotnet"] if hi else ["general_backend"],
                "scores": {"technical_skill_match": s, "experience_similarity": s, "feasibility": s,
                           "commercial_attractiveness": s},
                "score_explanation": "مناسب لخبرتك في الـ APIs", "matched_skills": ["C#", "ASP.NET Core"],
                "similar_projects": ["FocusFlow", "Google Project"], "required_skills": ["C#"],
                "technical_assessment": "تنفيذ API بسيط على ASP.NET Core.", "risks": ["النطاق غير واضح"],
                "missing_information": ["لا توجد ميزانية"], "recommendation": "apply",
                "language": "en" if "English project" in title else "ar"}, ensure_ascii=False)
        elif "You write freelance proposals" in system:
            kind = "proposal"
            en = "Write in clear, natural professional English" in system
            out = proposal_en() if en else proposal_ar()
            if self.vulnerable and "01000000000" in prompt and "Draft to fix" not in prompt:
                out += " كلمني على 01000000000"
        elif "You manage a freelancer" in system:
            kind = "interpret"
            out = json.dumps({"intent": "chat", "proposal": None, "reply": "تمام"}, ensure_ascii=False)
        else:
            kind, out = "other", "{}"
        self.calls.append((kind, system, prompt))
        return 200, json.dumps({"candidates": [{"content": {"parts": [{"text": out}]}}]}), {}

    def count(self, kind):
        return sum(1 for c in self.calls if c[0] == kind)


def gem_response(text, status=200):
    return status, json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}), {}


# ---------------------------------------------------------------- fake Telegram
class FakeBot:
    def __init__(self, username="NafPTbot", bot_id=123456789):
        self.sent, self._id, self.fail = [], 0, None
        self.username, self.id = username, bot_id

    async def send_message(self, chat_id, text, **kw):
        if self.fail:
            raise self.fail
        self._id += 1
        self.sent.append(SimpleNamespace(chat_id=chat_id, text=text, kw=kw, message_id=self._id))
        return SimpleNamespace(message_id=self._id)

    async def get_me(self):
        return SimpleNamespace(username=self.username, id=self.id)

    def texts(self):
        return [m.text for m in self.sent]


def make_app():
    return SimpleNamespace(bot=FakeBot(), bot_data={})


class FakeMessage:
    def __init__(self, text=None, reply_to=None):
        self.text, self.replies, self.reply_to_message = text, [], reply_to

    async def reply_text(self, text, **kw):
        self.replies.append((text, kw))
        return SimpleNamespace(message_id=1)


class FakeQuery:
    def __init__(self, data):
        self.data, self.message, self.answers, self.markup_cleared = data, FakeMessage(), [], 0

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))

    async def edit_message_reply_markup(self, reply_markup=None):
        self.markup_cleared += 1


def make_update(text=None, chat_id=None, user_id=None, chat_type="private", reply_to=None):
    cid = config.TG_CHAT_ID if chat_id is None else chat_id
    return SimpleNamespace(message=FakeMessage(text, reply_to), callback_query=None,
                           effective_chat=SimpleNamespace(id=cid, type=chat_type),
                           effective_user=SimpleNamespace(id=cid if user_id is None else user_id))


def make_callback(data, chat_id=None, user_id=None):
    cid = config.TG_CHAT_ID if chat_id is None else chat_id
    q = FakeQuery(data)
    return SimpleNamespace(message=None, callback_query=q, effective_chat=SimpleNamespace(id=cid, type="private"),
                           effective_user=SimpleNamespace(id=cid if user_id is None else user_id))


def ctx_for(app):
    return SimpleNamespace(application=app)


# ---------------------------------------------------------------- db fixtures
def make_project(status="discovered", url=None, **fields):
    url = url or f"https://nafezly.com/project/{abs(hash(str(fields) + status)) % 10**7}-x"
    pid, _ = db.upsert_listing(url)
    base = dict(title="مشروع API", body="محتاج API بـ ASP.NET Core", content_hash="h1", site_status="open")
    base.update(fields)
    db.update(pid, status=status, **base)
    return pid


def make_pending(score=8.0, **fields):
    pid = make_project("pending_approval", score=score, notify_status="queued", **fields)
    db.add_analysis(pid, {"final_score": score, "categories": ["dotnet"], "scores": {
        "technical_skill_match": 8, "experience_similarity": 8, "feasibility": 8, "commercial_attractiveness": 8},
        "score_explanation": "مناسب", "technical_assessment": "تقييم", "risks": ["خطر"], "missing_information": [],
        "language": "ar", "matched_skills": ["C#"], "similar_projects": ["FocusFlow"]}, content_hash="h1")
    db.add_proposal(pid, proposal_ar(), source="ai")
    return pid
