import json
import re
from pathlib import Path

from app.api.schemas import TeamInfoResponse

REPO_ROOT = Path(__file__).resolve().parents[2]
TEAM_INFO_PATH = REPO_ROOT / "config" / "team_info.json"


def test_team_info_file_exists():
    assert TEAM_INFO_PATH.exists()


def test_team_info_file_is_valid_json():
    data = json.loads(TEAM_INFO_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)


def test_group_batch_order_number_uses_the_format_the_brief_specifies():
    """The brief specifies "{batch#}_{order#}" -- so "2_4", not "Batch 2 Order 4".

    This endpoint is the one most likely to be read by a script rather than a
    person, and the value shipped as prose for a while. Pinned because a
    formatting slip here is invisible in every test that only checks the keys.
    """
    data = json.loads(TEAM_INFO_PATH.read_text(encoding="utf-8"))
    assert re.fullmatch(r"\d+_\d+", data["group_batch_order_number"]), (
        f"expected {{batch}}_{{order}} digits, got {data['group_batch_order_number']!r}"
    )


def test_team_info_matches_strict_schema():
    data = json.loads(TEAM_INFO_PATH.read_text(encoding="utf-8"))
    resp = TeamInfoResponse.model_validate(data)
    assert set(data.keys()) == {"group_batch_order_number", "team_name", "students"}
    assert len(resp.students) == 3
    for student in resp.students:
        assert student.name
        assert student.email
