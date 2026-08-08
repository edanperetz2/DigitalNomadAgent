"""A citation the reader follows has to land on the source that carries that number.

Numbering the sources in the payload and asking the model to reproduce them
worked on three prompts and failed on four, which then ran two numbering systems
at once: they renumbered the printed list 1..N and appended the given number in
brackets, while the prose cited the given number. P06 cited [75] against a list
that stopped at 57; P02 cited [97] against a list of 60. That is 63 citations
across four answers resolving to nothing, where the drift this replaced was one
or two entries.

The model now cites and the list is built here, the same rule every disclosure
already follows: what the reader must be able to check does not travel through
the model.
"""

from app.agent.recommendation_generator import _bibliography, _strip_model_sources

SOURCES = [
    {"source_name": "OpenStreetMap Nominatim — London", "source_url": "https://osm.org/1"},
    {"source_name": "WhereNext City Price Dataset — London", "source_url": None},
    {"source_name": "Wikivoyage Get around — London", "source_url": "https://wikivoyage.org/3"},
    {"source_name": "UK FCDO travel advice — London", "source_url": "https://gov.uk/4"},
]


def test_the_numbers_are_the_ones_the_model_was_given():
    body = "London has strong transit [3] and a comparatively good safety picture [4]."

    lines = _bibliography(body, SOURCES).splitlines()

    assert "3. Wikivoyage Get around — London — https://wikivoyage.org/3" in lines
    assert "4. UK FCDO travel advice — London — https://gov.uk/4" in lines


def test_every_citation_in_the_prose_resolves():
    """The property that was broken: a cited number always has an entry."""
    body = "Transit [3], safety [4], cost [2]."

    listed = {line.split(".", 1)[0] for line in _bibliography(body, SOURCES).splitlines() if line[:1].isdigit()}

    assert {"2", "3", "4"} <= listed


def test_uncited_sources_are_left_out_and_the_numbering_keeps_its_gaps():
    """Listing only what was used means gaps, and the gaps are correct: the
    number has to keep pointing at the same source."""
    body = "Only transit was used here [3]."

    assert _bibliography(body, SOURCES) == (
        "## Sources\n\n3. Wikivoyage Get around — London — https://wikivoyage.org/3"
    )


def test_a_number_the_model_invented_is_not_listed():
    """It cannot be resolved, so printing an entry for it would invent a source.
    With nothing else cited, the answer falls back to the full list rather than
    to no provenance at all."""
    body = "A claim with no backing [99]."

    listed = _bibliography(body, SOURCES)

    assert "99." not in listed
    assert "1. OpenStreetMap Nominatim — London" in listed


def test_an_answer_that_cites_nothing_still_gets_its_sources():
    """P05 and P07 write "Relevant evidence" as prose and cite no numbers.
    Filtering to what was cited would leave them with no provenance."""
    listed = _bibliography("A well-written answer with no bracketed numbers.", SOURCES)

    assert len(_bibliography("x", SOURCES).splitlines()) == len(listed.splitlines())
    assert "1. OpenStreetMap Nominatim — London" in listed


def test_no_sources_means_no_section():
    assert _bibliography("Anything [1].", []) == ""


def test_a_source_without_a_url_still_lists():
    body = "Cost evidence [2]."

    assert _bibliography(body, SOURCES) == "## Sources\n\n2. WhereNext City Price Dataset — London"


def test_a_list_the_model_wrote_anyway_is_removed():
    """The prompt says not to write one; nothing may depend on it obeying."""
    body = "The answer.\n\n## Sources\n\n1. Something it made up\n2. Something else"

    assert _strip_model_sources(body) == "The answer."


def test_stripping_only_touches_the_sources_section():
    body = "## Best matches\n\nA table.\n\n## Assumptions and limitations\n\nCaveats."

    assert _strip_model_sources(body) == body


def test_stripping_matches_the_headings_models_actually_use():
    for heading in ("## Sources", "### Sources used", "## References", "#### sources"):
        body = f"Answer text.\n\n{heading}\n\n1. An entry"
        assert _strip_model_sources(body) == "Answer text.", heading
