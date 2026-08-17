import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _run_app_js_assertions(assertions: str) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for frontend renderer tests")

    script = f"""
    const fs = require("fs");

    function escapeHtml(value) {{
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
    }}

    class StubClassList {{
      add() {{}}
      remove() {{}}
      toggle() {{}}
      contains() {{ return false; }}
    }}

    class StubElement {{
      constructor(id) {{
        this.id = id;
        this.hidden = false;
        this.disabled = false;
        this.value = "";
        this.dataset = {{}};
        this.style = {{ setProperty() {{}} }};
        this.classList = new StubClassList();
        this.children = [];
        this.attributes = new Map();
        this._textContent = id === "saved-search-sessions" ? "[]" : "";
        this._innerHTML = "";
      }}
      addEventListener() {{}}
      appendChild(child) {{ this.children.push(child); return child; }}
      closest() {{ return null; }}
      focus() {{}}
      getAttribute(name) {{ return this.attributes.get(name) || null; }}
      querySelector() {{ return new StubElement("nested"); }}
      querySelectorAll() {{ return []; }}
      removeAttribute(name) {{ this.attributes.delete(name); }}
      setAttribute(name, value) {{ this.attributes.set(name, String(value)); }}
      set textContent(value) {{
        this._textContent = String(value ?? "");
        this._innerHTML = escapeHtml(this._textContent);
      }}
      get textContent() {{ return this._textContent; }}
      set innerHTML(value) {{ this._innerHTML = String(value ?? ""); }}
      get innerHTML() {{ return this._innerHTML; }}
    }}

    const elements = new Map();
    function element(id) {{
      if (!elements.has(id)) elements.set(id, new StubElement(id));
      return elements.get(id);
    }}

    global.localStorage = {{
      getItem() {{ return null; }},
      removeItem() {{}},
      setItem() {{}},
    }};
    global.requestAnimationFrame = (callback) => callback();
    global.fetch = async () => {{ throw new Error("unexpected fetch in renderer test"); }};
    global.window = {{
      __DIGITAL_NOMAD_AGENT_TEST_HOOKS__: {{}},
      addEventListener() {{}},
      confirm() {{ return false; }},
      history: {{ pushState() {{}}, replaceState() {{}} }},
      location: {{ hash: "", pathname: "/" }},
      scrollTo() {{}},
    }};
    global.document = {{
      body: {{ style: {{}} }},
      documentElement: {{
        setAttribute() {{}},
        style: {{ setProperty() {{}} }},
      }},
      addEventListener() {{}},
      createElement() {{ return new StubElement("created"); }},
      getElementById(id) {{ return element(id); }},
      querySelector(selector) {{ return element(selector); }},
      querySelectorAll() {{ return []; }},
    }};

    eval(fs.readFileSync({json.dumps(str(ROOT / "app" / "static" / "app.js"))}, "utf8"));
    const hooks = window.__DIGITAL_NOMAD_AGENT_TEST_HOOKS__;

    function assert(condition, message) {{
      if (!condition) throw new Error(message);
    }}

    {assertions}
    """
    result = subprocess.run(
        [node, "-e", script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout


def test_citation_with_url_renders_as_new_tab_link():
    _run_app_js_assertions(
        """
        const markdown = "Evidence [43].\\n\\n## Sources\\n\\n43. UK FCDO travel advice - https://example.com/source";
        const map = hooks.citationMapFromMarkdown(markdown);
        const html = hooks.safeMarkdownToHtml("Evidence [43].", map);
        assert(html.includes('class="citation-link"'), "citation should be a link");
        assert(html.includes('href="https://example.com/source"'), "citation should use source URL");
        assert(html.includes('target="_blank"'), "citation should open a new tab");
        assert(html.includes('rel="noopener noreferrer"'), "citation should use safe rel");
        assert(html.includes('title="UK FCDO travel advice"'), "citation should expose source name");
        """
    )


def test_citation_numbers_keep_their_exact_source_mapping():
    _run_app_js_assertions(
        """
        const markdown = [
          "Transit [43] and safety [44].",
          "",
          "## Sources",
          "",
          "43. Source A - https://example.com/a",
          "44. Source B - https://example.com/b",
        ].join("\\n");
        const html = hooks.safeMarkdownToHtml("Transit [43] and safety [44].", hooks.citationMapFromMarkdown(markdown));
        assert(/\\[43\\]<\\/a>/.test(html), "citation 43 should render");
        assert(html.indexOf('href="https://example.com/a"') < html.indexOf("[43]"), "43 should point at URL A");
        assert(html.indexOf('href="https://example.com/b"') < html.indexOf("[44]"), "44 should point at URL B");
        """
    )


def test_citation_without_url_stays_visible_but_not_clickable():
    _run_app_js_assertions(
        """
        const markdown = "Cost [45].\\n\\n## Sources\\n\\n45. WhereNext City Price Dataset";
        const html = hooks.safeMarkdownToHtml("Cost [45].", hooks.citationMapFromMarkdown(markdown));
        assert(html.includes('class="citation-chip citation-missing"'), "missing URL should render as a chip");
        assert(html.includes("[45]"), "citation number should stay visible");
        assert(!html.includes("href="), "missing URL should not create a link");
        """
    )


def test_adjacent_citations_render_independently():
    _run_app_js_assertions(
        """
        const markdown = [
          "Evidence [40][41][42].",
          "",
          "## Sources",
          "",
          "40. Source 40 - https://example.com/40",
          "41. Source 41 - https://example.com/41",
          "42. Source 42 - https://example.com/42",
        ].join("\\n");
        const html = hooks.safeMarkdownToHtml("Evidence [40][41][42].", hooks.citationMapFromMarkdown(markdown));
        const links = html.match(/class="citation-link"/g) || [];
        assert(links.length === 3, "all adjacent citations should be separate links");
        assert(html.includes("[40]") && html.includes("[41]") && html.includes("[42]"), "all labels should remain visible");
        """
    )


def test_existing_prompt_example_citations_can_render_as_links():
    _run_app_js_assertions(
        """
        const example = JSON.parse(fs.readFileSync("assets/prompt_examples.json", "utf8")).prompt_examples[0];
        const html = hooks.renderStructuredRecommendation(example.full_response, example.prompt);
        assert(html.includes('class="citation-link"'), "stored example should render clickable citations");
        assert(html.includes('target="_blank"'), "stored example links should open new tabs");
        assert(html.includes('rel="noopener noreferrer"'), "stored example links should use safe rel");
        """
    )

