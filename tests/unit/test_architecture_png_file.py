"""The architecture diagram must say what the code says.

The brief requires sub-module and sub-agent names to be consistent across the
architecture diagram, the `steps` trace and the written descriptions. A PNG
cannot be diffed, so `scripts/render_architecture.py` writes a sidecar manifest
of every label it drew, and these tests assert that manifest against
`app.core.module_names`. Before this existed, the diagram was titled
"PlaceMatch", named three tools that had been deleted, and omitted six that
existed -- for weeks, invisibly.
"""

import json
from pathlib import Path

from PIL import Image

from app.core.module_names import ALL_MODULES, TOOL_NAMES, tool_display_label

REPO_ROOT = Path(__file__).resolve().parents[2]
PNG_PATH = REPO_ROOT / "assets" / "model_architecture.png"
LABELS_PATH = REPO_ROOT / "assets" / "model_architecture.labels.json"


def _labels() -> dict:
    return json.loads(LABELS_PATH.read_text(encoding="utf-8"))


def test_architecture_png_exists_and_opens():
    assert PNG_PATH.exists()
    with Image.open(PNG_PATH) as img:
        img.verify()


def test_architecture_png_has_reasonable_dimensions():
    with Image.open(PNG_PATH) as img:
        width, height = img.size
    assert width >= 800
    assert height >= 600


def test_canonical_module_names_available_for_diagram():
    assert len(ALL_MODULES) == 7
    assert all(isinstance(name, str) and name for name in ALL_MODULES)


def test_label_manifest_accompanies_the_png():
    assert LABELS_PATH.exists(), "Run: python scripts/render_architecture.py"


def test_diagram_names_every_pipeline_module():
    """All seven canonical modules appear, spelled exactly as the code spells them."""
    assert _labels()["modules"] == list(ALL_MODULES)


def test_diagram_names_every_registered_tool_and_no_others():
    """The registry and the diagram list the same tools -- this is the D-era bug."""
    expected = [tool_display_label(name) for name in TOOL_NAMES]
    assert _labels()["tools"] == expected


def test_diagram_does_not_name_a_tool_the_code_does_not_have():
    drawn = set(_labels()["all_labels"])
    known = {tool_display_label(name) for name in TOOL_NAMES} | set(ALL_MODULES)
    for retired in ("Education Options", "Accessibility", "Official Sources", "Terrain"):
        assert retired not in drawn, f"{retired!r} is on the diagram but not in the registry"
    assert known <= drawn


def test_diagram_uses_the_current_product_name():
    manifest = _labels()
    assert "DigitalNomadAgent" in manifest["title"]
    assert not any("PlaceMatch" in label for label in manifest["all_labels"])


def test_manifest_matches_a_fresh_render():
    """The committed PNG and manifest are in sync with the renderer."""
    import sys

    sys.path.insert(0, str(REPO_ROOT))
    from scripts.render_architecture import manifest, render

    assert manifest(render()) == _labels()
