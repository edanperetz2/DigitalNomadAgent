"""Nothing on the backend tracks clarification rounds -- every /api/execute
call is stateless -- so the round cap has to live client-side. This pins the
one function that decides it: after 2 questions, the 3rd reply must be sent
non-interactively so the backend auto-resolves instead of asking again.
"""

from tests.integration.test_clickable_citations_ui import _run_app_js_assertions


def test_first_two_rounds_may_still_ask():
    _run_app_js_assertions(
        """
        assert(hooks.canStillAskClarification(0) === true, "round 0 (nothing asked yet) may ask");
        assert(hooks.canStillAskClarification(1) === true, "round 1 (one question asked) may ask again");
        """
    )


def test_third_round_must_not_ask_again():
    _run_app_js_assertions(
        """
        assert(hooks.canStillAskClarification(2) === false, "after 2 questions, the 3rd reply must not ask again");
        assert(hooks.canStillAskClarification(3) === false, "stays false, never re-opens once capped");
        """
    )
