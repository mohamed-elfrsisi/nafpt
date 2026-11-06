# NafPT — Nafezly Proposal Telegram  (v1.0.0)

مساعدك الشخصي لاكتشاف فرص العمل الحر على **نافذلي**: بيراقب المشاريع، بيحلّلها على مهاراتك الحقيقية، بيكتبلك
بروبوزال مخصص، وبيبعتلها على تليجرام (**@NafPTbot**) للمراجعة. **إنت اللي بتوافق، وNafPT مش بيقدّم أي عرض بنفسه أبدًا.**

## 1. الفكرة والـ Workflow
```
Nafezly (صفحات متعددة) ─► scraper ─► db (SQLite) ─► classifier (مرحلة 1، بدون AI)
                                          │               │
                                          ▼               ▼
                                   monitor (دورات،     llm: Gemini (مرحلة 2)
                                   إعادة محاولة)       تحليل + درجة + بروبوزال
                                          │               │ + validator (حارس السياسات)
                                          ▼               ▼
                                     Telegram (@NafPTbot) ◄──► إنت: موافقة/رفض/تعديل/إعادة توليد
```
**حالات المشروع:** `discovered → analyzed → proposal_generated → pending_approval → approved → submitted`
بدائل: `rejected` (تلقائي أو منك)، `closed` (المشروع اتقفل)، `failed` (بعد محاولات متكررة، وتقدر `/retry`).
تعديل البروبوزال أو إعادة توليده بيحفظ **نسخة جديدة** وبيفضل المشروع `pending_approval`.
زر «قدّمت يدويًا» هو التأكيد الصريح الوحيد، وهو بيسجّل بس إنك قدّمت بنفسك على الموقع.

## 2. المميزات
- اكتشاف **كل** المشاريع المتاحة (بما فيها اللي كانت موجودة قبل التشغيل) مع دعم ترقيم الصفحات (`MAX_PAGES`).
- استخراج: العنوان، الوصف، الرابط، تاريخ النشر، الميزانية، المدة، المهارات، الحالة (مفتوح/مقفول/غير معروف)، عدد العروض لو ظاهر. الحاجة اللي مش موجودة بتتكتب «غير متاحة» ومش بتتخترع.
- كشف التكرار، والتعديلات (hash للمحتوى)، والمشاريع المقفولة أو المحذوفة.
- تصنيف حسب تخصصاتك (أولوية: .NET ← MEAN ← Laravel ← AI) واستبعاد (تصميم/ترجمة/إدخال بيانات/ووردبريس/موبايل/هاردوير...).
- درجة شفافة 0–10: 40% مهارات + 25% تشابه مع مشاريعك + 20% قابلية التنفيذ + 15% جاذبية تجارية.
- **كل مشروع مؤهل (≥ `MIN_SCORE`) بياخد تحليل + بروبوزال مخصص بلغة العميل (عربي/إنجليزي)**.
- حارس سياسات آلي (تليفون/إيميل/روابط/تواصل خارجي/ادعاء senior أو سنين خبرة/placeholders/الطول) مع إعادة كتابة تلقائية.
- مقاوم لـ prompt injection: نص المشروع بيتعامل كبيانات فقط داخل وسم معزول، ومخرجات الموديل بتتفحص.
- يتعافى بعد الأعطال وإعادة التشغيل (كل الحالة في SQLite)، مع إعادة محاولة بتأخير متزايد وتنبيهات.
- لوج آمن: التوكن والمفاتيح بتتخفي دائمًا (بما فيها روابط httpx/تليجرام).

## 3. التقنيات
Python 3.12+ (مصمم لـ 3.13) · python-telegram-bot (long polling) · httpx · BeautifulSoup4 · SQLite (WAL) · Gemini REST API.
بدون متصفح وبدون Docker.

## 4. التثبيت على Termux (انسخ والصق)
> مهم: ثبّت Termux من **F-Droid** مش Play Store.
```bash
pkg update -y && pkg upgrade -y
pkg install -y python unzip nano tmux
termux-setup-storage            # اضغط "سماح"
cd ~ && unzip ~/storage/downloads/NafPT-Production.zip
cd NafPT && bash termux_setup.sh
```
### الإعداد
```bash
nano .env          # TELEGRAM_BOT_TOKEN و GEMINI_API_KEY (CTRL+O ثم Enter للحفظ، CTRL+X للخروج)
nano profile.md    # مراجعة بياناتك (جاهز ومتعبّي من السيرة الذاتية)
python check_setup.py        # يتأكد من التوكن وهوية @NafPTbot والـ webhook والـ Gemini
python check_site.py         # يتأكد من استخراج المشاريع من نافذلي على شبكتك
```
**الـ Chat ID:** ابعت `/start` لـ @NafPTbot، هيرد عليك برقمك الخاص. حطه في `.env` عند `TELEGRAM_CHAT_ID` (رقمك إنت، مش رقم البوت).
بعدها `python check_setup.py --send-test` يبعتلك رسالة اختبار حقيقية.

### التشغيل
```bash
./nafpt.sh start     # يشغّله في tmux مع إعادة تشغيل تلقائية
./nafpt.sh status    # حالة العملية وآخر نبضة
./nafpt.sh logs      # متابعة السجل (CTRL+C للخروج)
./nafpt.sh attach    # الدخول لجلسة tmux (CTRL+B ثم D للخروج من غير إيقاف)
./nafpt.sh stop      # إيقاف سليم
./nafpt.sh restart
```
السجلات: `data/logs/nafpt.log` (دوّارة) و`data/logs/run_forever.log`.

### التحديث لاحقًا
```bash
cd ~/NafPT && ./nafpt.sh update ~/storage/downloads/NafPT-Production.zip
```
بيعمل نسخة احتياطية من `.env` والبيانات، ويحدّث الكود فقط، **من غير ما يلمس** `.env` و`data/` و`profile.md` و`policies.md`.

### الترقية من النسخة القديمة (nafezly-assistant 0.x)
```bash
cd ~ && ./nafezly-assistant/nafpt.sh stop 2>/dev/null; pkill -f "python main.py"   # وقّف القديم (أو CTRL+C)
mv nafezly-assistant nafezly-assistant-old
unzip ~/storage/downloads/NafPT-Production.zip && cd NafPT
cp ../nafezly-assistant-old/.env .env
mkdir -p data && cp ../nafezly-assistant-old/data/bot.db data/bot.db 2>/dev/null
pip install -r requirements.txt && chmod +x *.sh
python check_setup.py && ./nafpt.sh start
```
قاعدة البيانات القديمة بتتحوّل تلقائيًا (بعد نسخة احتياطية في `data/backups/`). المشاريع اللي كانت اتسجّلت «seen» من غير تحليل
بترجع للطابور وبتتحلل، فاستنى دفعات من الإشعارات في أول ساعات (3 تحليلات و3 إشعارات في كل دورة، بحد يومي لـ Gemini).

### بعد إعادة تشغيل الموبايل
1. افتح Termux ثم `cd ~/NafPT && ./nafpt.sh start`.
2. (اختياري) ثبّت **Termux:Boot** وضع في `~/.termux/boot/nafpt.sh`: `#!/data/data/com.termux/files/usr/bin/sh` ثم `cd ~/NafPT && ./nafpt.sh start`، وفعّل التطبيق مرة واحدة.
3. الحالة محفوظة في SQLite فبيكمّل من حيث وقف.

### حدود أندرويد (مهم)
أندرويد **ما بيضمنش** تشغيل دائم في الخلفية. حتى مع tmux و`termux-wake-lock` (محتاج Termux:API) واستثناء Termux من توفير البطارية،
ممكن النظام يقفل العملية (خصوصًا على شاشة مقفولة لساعات أو أجهزة بتحافظ جامد على البطارية). NafPT مصمم يتعافى (إعادة تشغيل + حالة محفوظة)
وبيبعتلك تنبيه لو المراقبة وقفت، لكن للاعتمادية الكاملة استخدم VPS/Raspberry Pi. `Docker` متاح للسيرفر (`docker compose up -d`).

## 5. إعداد بوت تليجرام
البوت الرسمي: **@NafPTbot** (https://t.me/NafPTbot). التوكن من @BotFather (`/mybots` ← NafPTbot ← API Token). NafPT بيتأكد عند البدء إن التوكن بتاع `@NafPTbot`
(`EXPECTED_BOT_USERNAME`) ويرفض يشتغل بتوكن بوت تاني. متنشئش بوت جديد ومتغيّرش اسم البوت.

## 6. إعداد Gemini
مفتاح من Google AI Studio في `GEMINI_API_KEY`. الموديل في `GEMINI_MODEL` (الافتراضي `gemini-2.5-flash`). حدود الباقة المجانية بتتغير، راجع صفحة Google الرسمية؛
`GEMINI_DAILY_LIMIT` (250) حد أمان على طلبات البوت. NafPT بيتحقق من المفتاح والموديل عند البدء بدون استهلاك حصة توليد.

## 7. الإعدادات (`.env`)
| المتغير | الافتراضي | الوظيفة |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` / `GEMINI_API_KEY` | — | إلزامي |
| `TELEGRAM_CHAT_ID` | — | رقمك الخاص (من `/start`) |
| `EXPECTED_BOT_USERNAME` | NafPTbot | هوية البوت المسموحة |
| `MIN_SCORE` / `STRONG_SCORE` / `EXCELLENT_SCORE` | 4 / 7 / 9 | حدود التأهيل والتصنيف |
| `GEMINI_MODEL` / `GEMINI_DAILY_LIMIT` | gemini-2.5-flash / 250 | الموديل وحد الطلبات اليومي |
| `NAFEZLY_LIST_URL` / `NAFEZLY_LINK_REGEX` / `NAFEZLY_DETAIL_SELECTOR` | | مصدر المشاريع وطريقة الاستخراج |
| `MAX_PAGES` / `REQUEST_DELAY` | 3 / 1.5 | عدد الصفحات وفاصل الطلبات (ثواني) |
| `POLL_ACTIVE` / `POLL_QUIET` | 90 / 300 | فاصل الفحص وقت النشاط/الهدوء (ثواني). زوّدهم لتوفير البطارية |
| `ACTIVE_FROM` / `ACTIVE_TO` | 10 / 24 | ساعات النشاط |
| `MAX_PROCESS_PER_TICK` / `MAX_NOTIFY_PER_TICK` | 3 / 3 | تحليلات وإشعارات في الدورة (تحكم في الـ backlog) |
| `ENABLE_WORDPRESS` / `ENABLE_MOBILE` | 0 / 0 | السماح بمشاريع ووردبريس / تطبيقات جوال |
| `MAX_ATTEMPTS` / `ALERT_AFTER_FAILURES` | 5 / 5 | إعادة المحاولات والتنبيه |

## 8. قاعدة البيانات (SQLite، schema v2)
`projects` (الحالة، الحقول المستخرجة، hash، إعادة المحاولة، حالة الإشعار) · `project_analysis` (التحليل، الدرجات، التصنيف) ·
`proposals` (كل النسخ + المصدر `ai/regenerated/edited_ai/user_edit`) · `approval_actions` (سجل قراراتك) ·
`monitoring_runs` (كل فحص) · `error_logs` · `settings` (إصدار المخطط، الإيقاف المؤقت، العدّادات).
الترحيل من النسخة القديمة آمن (نسخة احتياطية تلقائية في `data/backups/` قبل أي تغيير).

## 9. أوامر تليجرام
`/start` تعريف + Chat ID · `/status` حالة فعلية (تليجرام، Gemini، السكرابر، آخر/القادم فحص، الأعداد، الأخطاء) ·
`/pause` `/resume` · `/stats` · `/retry` (يرجّع الفاشل للطابور) · `/help`.
البطاقة: ✅ موافقة · ❌ رفض · 🔄 إعادة توليد · ✏️ تعديل · 🔗 فتح المشروع. وتقدر تكتب بالكلام (مصري/عربي/إنجليزي): «خليه أقصر»، «ابعت»، «لغي».

## 10. استكشاف الأخطاء
| العَرَض | السبب والحل |
|---|---|
| البوت مبيردش (و`getUpdates 200` في اللوج) | الأغلب `TELEGRAM_CHAT_ID` غلط/فاضي. ابعت `/start` وخد رقمك. شغّل `python check_setup.py --send-test` |
| `Conflict: terminated by other getUpdates` | نسخة تانية بنفس التوكن (جهاز/جلسة tmux). `./nafpt.sh stop` وتأكد إن مفيش غيرها. وكمان أي webhook مضبوط بيمنع polling (check_setup بيكشفه) |
| الـ Chat ID = رقم البوت | الرقم الصح هو رقمك الخاص من `/start`، مش رقم التوكن |
| `check_site.py` مفيش روابط | عدّل `NAFEZLY_LINK_REGEX`، ولو الصفحة JavaScript بالكامل البوت مش هيقدر (ابعتلي `data/diagnostics/*.html`) |
| حقول فاضية في البطاقة | عناوين الصفحة مختلفة عن المتوقع. شغّل `check_site.py --save-html` وابعت الملفات للتعديل |
| `Gemini: ❌` | `python check_setup.py`: مفتاح/موديل غلط أو الحصة خلصت. المعالجة بتتوقف وتكمل تلقائيًا |
| `NafPT شغّال بالفعل` | `./nafpt.sh stop` ثم `start` |
| `فشل` متكرر لمشروع | `/status` ثم `/retry`، واللوج: `./nafpt.sh logs` |

## 11. الأمان
أسرار في `.env` فقط · تنقيح تلقائي للتوكن/المفاتيح في كل اللوج والرسائل · صلاحية: Chat ID خاص + مستخدم واحد، والـ callbacks بتتفحص (والمجموعات مرفوضة) ·
استعلامات SQL معاملية · HTML بيتعمله escape قبل ما يتبعت لتليجرام · نص نافذلي = بيانات غير موثوقة · نسخة واحدة فقط (lock) · مفيش تقديم تلقائي ولا تخطي لـ CAPTCHA أو حدود المنصة
(لو الموقع رفض الطلبات NafPT بيهدّى وبينبّهك).

## 12. قيود معروفة
- استخراج الحقول مبني على عناوين عربية/إنجليزية شائعة (الميزانية، المدة، حالة المشروع...) ولسه محتاج تأكيد على صفحات نافذلي الحقيقية بـ `check_site.py`. الحقل المجهول بيبان «غير متاحة»، وحالة المشروع «غير معروفة» لو الموقع مش بيعلنها.
- الاختبارات اتشغّلت بمحاكاة (Gemini/تليجرام/موقع محلي) على Python 3.12. التسليم الفعلي لتليجرام لازم تتأكد منه بـ `check_setup.py --send-test`.
- مفيش تقديم تلقائي، ولا اقتراح أسعار (الـ profile مفيهوش أسعارك فمش هنخترع أرقام).
- ملاحظة: بند «500+ مستخدم متزامن» في مشروع NTI مكتوب «حسب السيرة الذاتية»، راجعه بنفسك قبل ما تعتمد عليه.

## 13. الاختبارات
```bash
pip install -r requirements-dev.txt && python -m pytest -q
```
