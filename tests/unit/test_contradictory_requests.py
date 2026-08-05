"""D38: P08 is the "plausibly-worded but internally impossible" case, and it is
impossible on two axes -- the $400 budget, and wanting proper snow while also
swimming outdoors and sitting outside at cafes in the evening. Only the budget
was ever detected. The words "snow" and "swim" appear nowhere in the answer.
"""

from app.climate_scoring import contradictory_climate_requests

P08 = (
    "I'm looking for somewhere in Scandinavia for a month this winter. I want proper snow "
    "and a real winter atmosphere, but I also want to be able to swim outdoors and sit "
    "outside at cafes in the evening. I don't want to spend more than $400 a month "
    "including accommodation, and I won't have a car."
)


def test_snow_and_outdoor_swimming_are_reported_as_irreconcilable():
    conflicts = contradictory_climate_requests(P08)

    assert len(conflicts) == 1
    assert "snow" in conflicts[0]
    assert "swimming outdoors" in conflicts[0]


def test_the_conflict_asks_which_side_to_optimise_for():
    """A contradiction the agent notices but cannot resolve is the user's to
    settle -- stating it without asking leaves them no way forward."""
    assert "which one matters more" in contradictory_climate_requests(P08)[0]


def test_an_ordinary_request_reports_no_conflict():
    assert contradictory_climate_requests(
        "Ten days in October, somewhere safe and walkable with good street food."
    ) == []
    assert contradictory_climate_requests(
        "Six months escaping the winter, November through April, mild not tropical."
    ) == []


def test_a_ruled_out_preference_cannot_contradict_anything():
    """"No snow" is not a request for snow."""
    assert contradictory_climate_requests(
        "Somewhere warm with no snow, where I can swim outdoors every day."
    ) == []


def test_conflicts_are_detected_across_separate_stated_fields():
    assert contradictory_climate_requests(
        ["proper snow", "a real winter atmosphere"], ["swim outdoors", "sit outside at cafes"]
    )
