from pathlib import Path

from PIL import Image

from app.core.module_names import ALL_MODULES

REPO_ROOT = Path(__file__).resolve().parents[2]
PNG_PATH = REPO_ROOT / "assets" / "model_architecture.png"


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
