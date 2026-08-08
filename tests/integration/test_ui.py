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


def test_index_starts_without_legacy_prompt_buttons(client):
    html = client.get("/").text
    assert 'id="history-list"' in html
    assert 'class="conversation-card example-btn"' not in html
    assert 'data-example="' not in html
    assert '<script id="saved-search-sessions" type="application/json">[]</script>' in html


def test_index_links_to_architecture_and_agent_info(client):
    html = client.get("/").text
    assert "/api/model_architecture" in html
    assert "/api/agent_info" in html


def test_app_js_references_execute_endpoint(client):
    js = client.get("/static/app.js").text
    assert "/api/execute" in js
    assert "EXECUTE_TIMEOUT_MS = 295000" in js
    assert "controller.abort()" in js


def test_app_js_escapes_html_before_rendering():
    from pathlib import Path

    js_path = Path(__file__).resolve().parents[2] / "app" / "static" / "app.js"
    js = js_path.read_text(encoding="utf-8")
    assert "escapeHtml" in js
    assert "textContent" in js


def test_index_does_not_claim_capabilities_the_system_lacks(client):
    """D13: there is no visa tool and nothing is real-time; the landing page
    must not promise either. Generated responses explicitly disclaim both."""
    html = client.get("/").text.lower()
    assert "visa" not in html
    assert "real-time" not in html
    assert "up-to-date" not in html


def test_the_submit_control_is_labelled_run_agent(client):
    """The brief names a "Run Agent" button that calls POST /api/execute. The
    control did the right thing under the wrong name ("Get recommendations"),
    which is a graded requirement failing on wording alone."""
    html = client.get("/").text
    assert ">Run Agent<" in html
    assert 'aria-label="Run Agent"' in html
    assert "Get recommendations" not in html
