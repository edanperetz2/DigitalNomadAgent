from scripts.golden_set.cases import GoldenCase
from scripts.golden_set.scorer import score_case

_NORMAL_RESPONSE = (
    "## Best matches\n\n| Rank | Place |\n|---|---|\n| 1 | Valencia |\n"
    "\n**Generated using:** mock deterministic mode (MOCK_LLM=true)."
)


def test_normal_case_passes_with_expected_shape():
    case = GoldenCase(name="normal", prompt="p", expected_modules=frozenset({"Request Interpreter"}))
    data = {
        "status": "ok",
        "response": _NORMAL_RESPONSE,
        "steps": [{"module": "Request Interpreter"}],
    }
    result = score_case(case, data, max_finalists=8)
    assert result.passed, result.failures


def test_stale_placeholder_fails_the_case():
    case = GoldenCase(name="stale", prompt="p")
    data = {
        "status": "ok",
        "response": _NORMAL_RESPONSE + " scoring awaits the LLM reasoning contract.",
        "steps": [],
    }
    result = score_case(case, data, max_finalists=8)
    assert not result.passed
    assert any(c.check == "no_stale_scoring_placeholder" for c in result.failures)


def test_banned_claim_fails_the_case_but_legitimate_disclaimer_does_not():
    case = GoldenCase(name="claim", prompt="p")
    bad_data = {
        "status": "ok",
        "response": _NORMAL_RESPONSE + " This destination offers guaranteed safety for visitors.",
        "steps": [],
    }
    assert not score_case(case, bad_data, max_finalists=8).passed

    # The system's own real disclaimer legitimately says "guaranteed admission/visa
    # eligibility are claimed here" -- must not be flagged as a banned claim.
    disclaimer_data = {
        "status": "ok",
        "response": _NORMAL_RESPONSE
        + " No live prices, flight availability, or guaranteed admission/visa eligibility are claimed here.",
        "steps": [],
    }
    assert score_case(case, disclaimer_data, max_finalists=8).passed


def test_forbidden_phrase_fails_when_present():
    case = GoldenCase(name="excluded", prompt="p", forbidden_phrases=("France",))
    data = {"status": "ok", "response": _NORMAL_RESPONSE + " ### 2. Nice, France", "steps": []}
    result = score_case(case, data, max_finalists=8)
    assert not result.passed
    assert any(c.check == "forbidden_absent:France" for c in result.failures)


def test_clarification_case_only_checks_for_question_mark():
    case = GoldenCase(name="clarify", prompt="p", expect_clarification=True)
    data = {"status": "ok", "response": "Could you clarify your budget?", "steps": []}
    assert score_case(case, data, max_finalists=8).passed


def test_error_case_checks_required_phrase_in_error_text():
    case = GoldenCase(name="err", prompt="p", expect_status="error", required_phrases=("eliminated",))
    passing = {"status": "error", "error": "All candidates were eliminated by hard constraints."}
    failing = {"status": "error", "error": "Something else went wrong."}
    assert score_case(case, passing, max_finalists=8).passed
    assert not score_case(case, failing, max_finalists=8).passed


def test_finalist_count_out_of_bounds_fails():
    case = GoldenCase(name="too_many", prompt="p")
    rows = "\n".join(f"| {i} | Place{i} |" for i in range(1, 10))
    data = {"status": "ok", "response": f"## Best matches\n\n{rows}\n\n**Generated using:** mock.", "steps": []}
    result = score_case(case, data, max_finalists=8)
    assert not result.passed
    assert any(c.check == "finalist_count_in_bounds" for c in result.failures)
