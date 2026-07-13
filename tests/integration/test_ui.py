def test_index_returns_html(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_index_contains_prompt_input_and_submit(client):
    html = client.get("/").text
    assert 'id="prompt-input"' in html
    assert 'id="submit-btn"' in html
    assert "<form" in html
    assert 'for="prompt-input"' in html


def test_index_contains_example_prompt_buttons(client):
    html = client.get("/").text
    assert 'data-example="remote_work"' in html
    assert 'data-example="study"' in html
    assert 'data-example="vacation"' in html


def test_index_links_to_architecture_and_agent_info(client):
    html = client.get("/").text
    assert "/api/model_architecture" in html
    assert "/api/agent_info" in html


def test_app_js_references_execute_endpoint(client):
    js = client.get("/static/app.js").text
    assert "/api/execute" in js


def test_app_js_escapes_html_before_rendering():
    from pathlib import Path

    js_path = Path(__file__).resolve().parents[2] / "app" / "static" / "app.js"
    js = js_path.read_text(encoding="utf-8")
    assert "escapeHtml" in js
    assert "textContent" in js
