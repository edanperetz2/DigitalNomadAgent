from app.agent.models import PlaceRequestProfile
from app.agent.orchestrator import _resolve_ambiguous_profile


def test_resolve_ambiguous_profile_defaults_unknown_purpose_and_discloses_assumption():
    profile = PlaceRequestProfile(
        purpose="unknown",
        clarification_required=True,
        clarification_question="What is the main purpose of this trip?",
    )

    resolved = _resolve_ambiguous_profile(profile)

    assert resolved.purpose == "mixed"
    assert len(resolved.assumptions) == 1
    assert "proceeding with a broad default" in resolved.assumptions[0].casefold()
    assert "What is the main purpose of this trip?" in resolved.assumptions[0]

    # The original profile object must be untouched -- callers still hold the checkpoint copy.
    assert profile.purpose == "unknown"
    assert profile.assumptions == []


def test_resolve_ambiguous_profile_leaves_a_known_purpose_unchanged():
    profile = PlaceRequestProfile(
        purpose="study",
        clarification_required=True,
        clarification_question="Which academic field?",
    )

    resolved = _resolve_ambiguous_profile(profile)

    assert resolved.purpose == "study"
    assert len(resolved.assumptions) == 1
