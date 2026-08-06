"""D50: the deployment answered {"detail":"Not Found"} to every route.

vercel.json rewrites everything to /main.py and the Python runtime hands the
ASGI app that rewritten path, so FastAPI matched nothing -- while importing and
running perfectly well. Reproducible without deploying, which is what these
tests do.
"""

import pytest
from fastapi.testclient import TestClient

from main import app as entrypoint_app


@pytest.fixture
def client():
    with TestClient(entrypoint_app) as test_client:
        yield test_client


def test_the_bare_app_still_serves_its_own_paths():
    from app.main import app as inner

    with TestClient(inner) as c:
        assert c.get("/api/team_info").status_code == 200


def test_the_unwrapped_app_reproduces_the_live_404():
    """The exact symptom the deployed URL showed, for every path it showed it on."""
    from app.main import app as inner

    with TestClient(inner) as c:
        response = c.get("/main.py")
        assert response.status_code == 404
        assert response.json() == {"detail": "Not Found"}


@pytest.mark.parametrize(
    "path",
    ["/", "/api/team_info", "/api/agent_info", "/api/model_architecture", "/openapi.json"],
)
def test_every_route_serves_under_the_rewritten_path(client, path):
    direct = client.get(path)
    rewritten = client.get(f"/main.py{path}" if path != "/" else "/main.py")

    assert direct.status_code == 200
    assert rewritten.status_code == direct.status_code
    assert rewritten.content == direct.content


def test_an_alternative_function_entrypoint_is_also_stripped(client):
    assert client.get("/api/index/api/team_info").status_code == 200


def test_a_genuinely_missing_route_still_404s(client):
    assert client.get("/main.py/api/does-not-exist").status_code == 404
    assert client.get("/api/does-not-exist").status_code == 404


def test_lifespan_runs_through_the_wrapper(client):
    """Startup opens the database; a wrapper that swallowed lifespan would leave
    request.app.state.db unset and every page 500."""
    assert client.get("/").status_code == 200
