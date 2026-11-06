import config
import classifier as cl


def test_dotnet_is_first_priority_and_matches_skills():
    r = cl.classify("API بـ ASP.NET Core", "نحتاج Web API مع SQL Server و Entity Framework وauthentication")
    assert r["decision"] == "pass" and r["categories"][0] == "dotnet"
    assert {"asp.net", "sql server", "entity framework"} <= set(r["matched_terms"])


def test_mean_laravel_ai_categories_and_priority_order():
    assert cl.classify("لوحة تحكم", "Angular و Node.js و MongoDB")["categories"][0] == "mean"
    assert cl.classify("موقع", "نظام بـ Laravel و MySQL")["categories"][0] == "laravel"
    r = cl.classify("شات بوت", "ربط OpenAI LLM بموقعنا مع embeddings")
    assert "ai" in r["categories"]
    mixed = cl.classify("نظام", "ASP.NET Core مع Angular وLLM")
    assert mixed["categories"].index("dotnet") < mixed["categories"].index("mean") < mixed["categories"].index("ai")


def test_exclusions_rejected_by_default():
    for title, body in (("تصميم لوجو", "عايز لوجو لشركتي"), ("ترجمة مقال", "ترجمة من الانجليزي"), ("Video editing", "montage"),
                        ("إدخال بيانات", "excel data entry"), ("تطبيق Flutter", "تطبيق اندرويد وايفون")):
        r = cl.classify(title, body)
        assert r["decision"] == "reject" and r["excluded"], title


def test_wordpress_and_mobile_toggle(monkeypatch):
    assert cl.classify("موقع WordPress", "قالب ووردبريس و PHP")["decision"] == "reject"
    monkeypatch.setattr(config, "ENABLE_WORDPRESS", True)
    assert cl.classify("موقع WordPress", "قالب ووردبريس و PHP")["decision"] == "pass"
    assert cl.classify("تطبيق جوال", "Flutter app")["decision"] == "reject"
    monkeypatch.setattr(config, "ENABLE_MOBILE", True)
    assert cl.classify("تطبيق جوال", "Flutter app")["decision"] == "pass"


def test_exclusion_does_not_kill_strong_technical_projects():
    r = cl.classify("API", "ASP.NET Core Web API مع SQL Server، وتصميم لوجو بسيط للصفحة")
    assert r["decision"] == "pass" and "تصميم/جرافيك" in r["excluded"]


def test_unconventional_phrasing_still_reaches_ai_stage():
    for text in ("عايز موقع يبيع منتجاتي وفيه لوحة تحكم", "محتاج حد يعملي نظام للمخزن", "i need an app that talks to my database"):
        assert cl.classify("طلب", text)["decision"] == "pass", text


def test_non_software_is_rejected_without_ai_cost():
    r = cl.classify("سباك", "مطلوب فني سباكة لتصليح تسريب")
    assert r["decision"] == "reject" and "مؤشر" in r["reason"]


def test_transferable_skills_are_not_rejected():
    assert cl.classify("Django REST backend", "Python backend REST API with PostgreSQL")["decision"] == "pass"


def test_term_boundaries_do_not_cause_false_hits():
    assert "ai" not in cl.classify("t", "i want to maintain a railway")["categories"]
    assert "dotnet" not in cl.classify("t", "see example.network page")["categories"]
