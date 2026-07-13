PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def test_model_architecture_returns_png_bytes(client):
    response = client.get("/api/model_architecture")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content[:8] == PNG_SIGNATURE
    assert len(response.content) > 1000


def test_model_architecture_is_not_json(client):
    response = client.get("/api/model_architecture")
    assert not response.headers["content-type"].startswith("application/json")
    try:
        response.json()
        raised = False
    except Exception:
        raised = True
    assert raised
