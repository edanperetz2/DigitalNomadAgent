"""What the reader must not miss goes above the answer, not under the sources.

Every deterministic disclosure was appended, so all of them landed at 98-99% of
the way down the answer, after the bibliography. Their own wording assumed the
opposite: "the order below does not reflect them at all" had nothing below it,
and neither did "Every place below is equally unverified". On P10 the refusal of
the three things actually asked for -- live flight prices, hotel rates, a visa
fee -- sat beneath 35 source entries, on the one prompt where the refusal is the
answer.

They stay on this side of the LLM call either way: routed through the model as
something to paraphrase, they get dropped (D32, D56).
"""

from app.agent.models import CandidateEvaluation, PlaceRequestProfile, ValidationResult
from app.agent.recommendation_generator import render_recommendation_fallback

PROFILE = PlaceRequestProfile(purpose="vacation")
EVALUATIONS = [
    CandidateEvaluation(place="Rhodes", country="Greece", criterion_scores={"cost": 0.7}),
    CandidateEvaluation(place="Split", country="Croatia", criterion_scores={"cost": 0.6}),
    CandidateEvaluation(place="Bari", country="Italy", criterion_scores={"cost": 0.5}),
]


def _answer(**kwargs) -> str:
    return render_recommendation_fallback(
        PROFILE, EVALUATIONS, ValidationResult(approved=True), [], **kwargs
    )


def _position(answer: str, marker: str) -> float:
    assert marker in answer, marker
    return answer.index(marker) / len(answer)


def test_an_impossible_request_is_stated_before_anything_is_ranked():
    answer = _answer(conflicts=["Snow and outdoor swimming cannot both be had."])

    assert _position(answer, "cannot both be satisfied") < 0.2


def test_what_could_not_be_answered_leads_rather_than_trails():
    """P10 asked for three things this agent cannot supply and was told so
    below the bibliography."""
    answer = _answer(out_of_scope=["live or confirmed flight prices"])

    assert _position(answer, "outside what this agent can answer") < 0.2


def test_a_degraded_run_says_so_at_the_top():
    answer = _answer(service_notices=["The request-interpreter model was unavailable."])

    assert _position(answer, "Reduced-capability run") < 0.2


def test_unverified_requirements_are_stated_up_front():
    answer = _answer(unverifiable_requirements=["reliable internet for daily video calls"])

    assert _position(answer, "nothing here could check it") < 0.2


def test_priorities_the_ranking_could_not_use_are_stated_up_front():
    answer = _answer(unmeasured_priorities=["food scene"])

    assert _position(answer, "Not used in this ranking") < 0.2


def test_the_wording_now_describes_where_the_places_actually_are():
    """"the order below" and "Every place below" were written for a preamble and
    delivered as a footer."""
    answer = _answer(
        unmeasured_priorities=["food scene"],
        unverifiable_requirements=["reliable internet"],
    )

    assert answer.index("Every place below") < answer.index("Rhodes")


def test_unmeasured_priorities_claim_no_direction():
    """The UI renders this one *under* the ranking, so it cannot say "below".

    The other blocks are fixed above the answer and may point downwards at it.
    This block moved, and a sentence that points the wrong way is the same
    defect as the one that put it under the bibliography -- just reversed.
    """
    answer = _answer(unmeasured_priorities=["food scene"])

    claim = answer[answer.index("Not used in this ranking") :].split("\n")[0]
    assert "below" not in claim, claim
    assert "the ranking does not reflect them" in claim


def test_how_the_answer_was_generated_stays_at_the_foot():
    """Provenance is a footer; it is not something the reader must see first."""
    answer = _answer()

    assert _position(answer, "**Generated using:**") > 0.8


def test_a_clean_run_gains_no_preamble():
    """The blocks fire on the exception, not on every answer."""
    assert _answer().lstrip().startswith("#")
