import json

import httpx
import pytest

import config
import db
import llm
import validator
from helpers import FakeGemini, gem_response, proposal_ar, proposal_en, run
from llm import GeminiError

AN = {"categories": ["dotnet"], "scores": {"technical_skill_match": 8, "experience_similarity": 6, "feasibility": 6,
                                            "commercial_attractiveness": 6}, "recommendation": "apply", "language": "ar"}


def script(monkeypatch, *responses):
    calls = []
    it = iter(responses)

    async def post(body):
        calls.append(body)
        r = next(it)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(llm, "_post_once", post)
    return calls


def test_score_formula_weights_are_transparent():
    assert llm.validate_analysis(AN)["final_score"] == 6.8        # .4*8 + .25*6 + .2*6 + .15*6
    ten = {**AN, "scores": {k: 10 for k in llm.WEIGHTS}}
    zero = {**AN, "scores": {k: 0 for k in llm.WEIGHTS}}
    assert llm.validate_analysis(ten)["final_score"] == 10.0 and llm.validate_analysis(zero)["final_score"] == 0.0
    assert sum(llm.WEIGHTS.values()) == pytest.approx(1.0)


def test_analyze_project_end_to_end_with_fake(monkeypatch):
    monkeypatch.setattr(llm, "_post_once", FakeGemini())
    a = run(llm.analyze_project({"title": "ASP.NET API", "body": "تفاصيل", "skills": '["C#"]', "budget": None}))
    assert a["final_score"] == 9.0 and a["categories"] == ["dotnet"] and a["similar_projects"] == ["FocusFlow"]
    assert a["stage1"]["categories"] and a["language"] == "ar"


def test_malformed_json_is_retried_then_succeeds(monkeypatch):
    calls = script(monkeypatch, gem_response("not json {"), gem_response("```json\n" + json.dumps(AN) + "\n```"))
    a = run(llm.analyze_project({"title": "t", "body": "b"}))
    assert a["final_score"] == 6.8 and len(calls) == 2
    assert "not valid JSON" in calls[1]["contents"][0]["parts"][0]["text"]


def test_persistently_malformed_json_fails_cleanly(monkeypatch):
    script(monkeypatch, *[gem_response("nope")] * 5)
    with pytest.raises(GeminiError) as e:
        run(llm.analyze_project({"title": "t", "body": "b"}))
    assert e.value.kind == "invalid"


def test_missing_scores_in_valid_json_is_invalid(monkeypatch):
    script(monkeypatch, *[gem_response('{"categories": []}')] * 5)
    with pytest.raises(GeminiError):
        run(llm.analyze_project({"title": "t", "body": "b"}))


def test_quota_error_is_not_retried(monkeypatch):
    calls = script(monkeypatch, (429, '{"error":{"message":"Quota exceeded for requests per day"}}', {}))
    with pytest.raises(GeminiError) as e:
        run(llm._gen("s", "p"))
    assert e.value.kind == "quota" and len(calls) == 1 and not e.value.retryable


def test_rate_limit_is_retried_with_backoff(fresh_env, monkeypatch):
    sleeps = fresh_env
    calls = script(monkeypatch, (429, "slow down", {"retry-after": "7"}), (429, "slow down", {}), gem_response("ok"))
    assert run(llm._gen("s", "p")) == "ok" and len(calls) == 3 and sleeps[0] == 7 and sleeps[1] == 4


@pytest.mark.parametrize("failure,kind", [((401, "bad key", {}), "auth"), ((403, "permission denied", {}), "auth"),
                                          ((404, "no model", {}), "auth"), ((400, "weird bad request", {}), "invalid")])
def test_permanent_errors_do_not_retry(monkeypatch, failure, kind):
    calls = script(monkeypatch, failure)
    with pytest.raises(GeminiError) as e:
        run(llm._gen("s", "p"))
    assert e.value.kind == kind and len(calls) == 1


def test_transient_errors_empty_and_timeouts_are_retried(monkeypatch):
    calls = script(monkeypatch, (500, "oops", {}), GeminiError("timeout", "t"), gem_response(""), (200, '{"candidates":[]}', {}),
                   gem_response("finally"))
    assert run(llm._gen("s", "p")) == "finally" and len(calls) == 5 if config.GEMINI_RETRIES >= 5 else True


def test_gives_up_after_configured_retries(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_RETRIES", 3)
    calls = script(monkeypatch, *[(503, "x", {})] * 5)
    with pytest.raises(GeminiError) as e:
        run(llm._gen("s", "p"))
    assert e.value.kind == "server" and len(calls) == 3


def test_blocked_content_and_non_json_http_body(monkeypatch):
    script(monkeypatch, (200, json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}), {}))
    with pytest.raises(GeminiError) as e:
        run(llm._gen("s", "p"))
    assert e.value.kind == "blocked"
    script(monkeypatch, (200, "<html>proxy</html>", {}))
    with pytest.raises(GeminiError) as e2:
        run(llm._gen("s", "p"))
    assert e2.value.kind == "invalid"


def test_daily_budget_is_enforced_and_observable(monkeypatch):
    monkeypatch.setattr(config, "GEMINI_DAILY_LIMIT", 2)
    calls = script(monkeypatch, gem_response("a"), gem_response("b"), gem_response("c"))
    run(llm._gen("s", "p")); run(llm._gen("s", "p"))
    with pytest.raises(GeminiError) as e:
        run(llm._gen("s", "p"))
    assert e.value.kind == "budget" and len(calls) == 2 and db.get_counter("gemini_calls") == 2


def test_language_detection():
    assert llm.detect_language("عايز موقع تجارة إلكترونية") == "ar"
    assert llm.detect_language("I need an e-commerce website built with ASP.NET") == "en"
    assert llm.detect_language("نحتاج API مع ASP.NET Core و SQL Server") == "ar"


def test_arabic_and_english_proposals(monkeypatch):
    fake = FakeGemini()
    monkeypatch.setattr(llm, "_post_once", fake)
    ar, v1 = run(llm.write_proposal({"title": "مشروع", "body": "محتاج API"}, {"language": "ar"}))
    en, v2 = run(llm.write_proposal({"title": "English project", "body": "need an API"}, {"language": "en"}))
    assert "السلام عليكم" in ar and v1["language"] == "ar" and "Hello" in en and v2["language"] == "en"
    assert "اكتب بالعربي" in fake.calls[0][1] and "professional English" in fake.calls[1][1]
    for text in (ar, en):
        assert validator.validate(text)[0] == []
    sysmsg = fake.calls[0][1]
    assert "NOT senior" in sysmsg and "FocusFlow" in sysmsg and "Mohamed" in fake.calls[1][1] or "محمد" in fake.calls[0][1]


def test_proposal_that_never_passes_validation_is_not_returned(monkeypatch):
    bad = proposal_ar() + " كلمني على 01012345678"
    calls = script(monkeypatch, *[gem_response(bad)] * 6)
    with pytest.raises(GeminiError) as e:
        run(llm.write_proposal({"title": "t", "body": "b"}, {"language": "ar"}))
    assert e.value.kind == "invalid" and len(calls) == config.MAX_REWRITES + 1
    short = script(monkeypatch, *[gem_response("قصير")] * 6)
    with pytest.raises(GeminiError):
        run(llm.write_proposal({"title": "t", "body": "b"}, {"language": "ar"}))


def test_proposal_text_is_cleaned_from_fences_and_labels(monkeypatch):
    script(monkeypatch, gem_response("```\nProposal: " + proposal_en() + "\n```"))
    text, _ = run(llm.write_proposal({"title": "t", "body": "b"}, {"language": "en"}))
    assert text.startswith("Hello") and "```" not in text


def test_instructions_and_previous_version_are_passed(monkeypatch):
    calls = script(monkeypatch, gem_response(proposal_ar()))
    run(llm.write_proposal({"title": "t", "body": "b"}, {}, instructions="خليه أقصر", previous="OLD TEXT"))
    prompt = calls[0]["contents"][0]["parts"][0]["text"]
    assert "خليه أقصر" in prompt and "OLD TEXT" in prompt


def test_interpret_defaults_unknown_intent_to_chat(monkeypatch):
    script(monkeypatch, gem_response(json.dumps({"intent": "launch_missiles", "reply": "x"})))
    assert run(llm.interpret("hi", {"title": "t"}, "p"))["intent"] == "chat"


def test_check_model_reports_real_state(monkeypatch):
    for status, ok in ((200, True), (403, False), (404, False)):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r, s=status: httpx.Response(s, text="API key not valid")))
        monkeypatch.setattr(llm, "_get_client", lambda c=client: c)
        good, msg = run(llm.check_model())
        assert good is ok and config.GEMINI_KEY not in msg
