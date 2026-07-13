(function () {
  "use strict";

  const EXAMPLE_PROMPTS = {
    remote_work:
      "I want to spend three months somewhere in Europe where I can work remotely, live without a car, and stay within €1,800 per month.",
    study:
      "Recommend a city for a one-semester computer-science exchange. I care about student life, public transportation, safety, and affordable housing.",
    vacation:
      "Find a quiet beach destination for two weeks in October, with warm but not extremely hot weather and good hiking nearby.",
  };

  const form = document.getElementById("execute-form");
  const promptInput = document.getElementById("prompt-input");
  const submitBtn = document.getElementById("submit-btn");
  const loadingIndicator = document.getElementById("loading-indicator");
  const loadingStage = document.getElementById("loading-stage");
  const errorDisplay = document.getElementById("error-display");
  const resultsSection = document.getElementById("results");
  const resultsContent = document.getElementById("results-content");
  const stepsContent = document.getElementById("steps-content");

  // Simulated stage labels shown while waiting for /api/execute. These mirror
  // the agent's real state-machine order but are not driven by live backend
  // events (the API is a single request/response call, not a stream).
  const LOADING_STAGES = [
    "Interpreting your request…",
    "Generating candidate destinations…",
    "Verifying candidates (geocoding)…",
    "Checking weather & climate fit…",
    "Matching time zones…",
    "Running budget-fit tool…",
    "Checking amenities & walkability…",
    "Gathering official sources…",
    "Scoring & ranking candidates…",
    "Validating recommendation quality…",
    "Writing your recommendation…",
  ];
  const LOADING_STAGE_INTERVAL_MS = 1300;
  let loadingStageTimer = null;

  document.querySelectorAll(".example-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const key = btn.getAttribute("data-example");
      if (EXAMPLE_PROMPTS[key]) {
        promptInput.value = EXAMPLE_PROMPTS[key];
        promptInput.focus();
      }
    });
  });

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
  }

  // Small, whitelist-only Markdown-subset renderer. Input is always
  // HTML-escaped first; only a narrow set of structural transforms is then
  // applied. Raw HTML from the LLM/response is never inserted directly.
  function safeMarkdownToHtml(markdown) {
    const escaped = escapeHtml(markdown);
    const lines = escaped.split("\n");
    const htmlParts = [];
    let inTable = false;
    let listOpen = false;

    function closeList() {
      if (listOpen) {
        htmlParts.push("</ul>");
        listOpen = false;
      }
    }

    for (let rawLine of lines) {
      let line = rawLine;

      // Links: [text](https://...)
      line = line.replace(
        /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
      );
      // Bold: **text**
      line = line.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");

      if (/^\s*\|.*\|\s*$/.test(rawLine)) {
        if (!inTable) {
          htmlParts.push('<table class="md-table">');
          inTable = true;
        }
        const cells = line
          .trim()
          .replace(/^\|/, "")
          .replace(/\|$/, "")
          .split("|");
        const isSeparator = cells.every((c) => /^:?-+:?$/.test(c.trim()));
        if (!isSeparator) {
          const tag = htmlParts.length && htmlParts[htmlParts.length - 1] === '<table class="md-table">' ? "th" : "td";
          htmlParts.push(
            "<tr>" + cells.map((c) => `<${tag}>${c.trim()}</${tag}>`).join("") + "</tr>"
          );
        }
        continue;
      } else if (inTable) {
        htmlParts.push("</table>");
        inTable = false;
      }

      if (/^### /.test(rawLine)) {
        closeList();
        htmlParts.push(`<h4>${line.replace(/^### /, "")}</h4>`);
      } else if (/^## /.test(rawLine)) {
        closeList();
        htmlParts.push(`<h3>${line.replace(/^## /, "")}</h3>`);
      } else if (/^# /.test(rawLine)) {
        closeList();
        htmlParts.push(`<h2>${line.replace(/^# /, "")}</h2>`);
      } else if (/^\s*[-*]\s+/.test(rawLine)) {
        if (!listOpen) {
          htmlParts.push("<ul>");
          listOpen = true;
        }
        htmlParts.push(`<li>${line.replace(/^\s*[-*]\s+/, "")}</li>`);
      } else if (/^\d+\.\s+/.test(rawLine)) {
        closeList();
        htmlParts.push(`<p>${line}</p>`);
      } else if (rawLine.trim() === "") {
        closeList();
      } else {
        closeList();
        htmlParts.push(`<p>${line}</p>`);
      }
    }
    closeList();
    if (inTable) htmlParts.push("</table>");
    return htmlParts.join("\n");
  }

  function renderSteps(steps) {
    if (!steps || steps.length === 0) {
      stepsContent.innerHTML = "<p>No LLM calls were made for this request.</p>";
      return;
    }
    const parts = steps.map((step, i) => {
      return `
        <article class="step">
          <h4>Step ${i + 1}: ${escapeHtml(step.module)}</h4>
          <details>
            <summary>Prompt</summary>
            <pre>${escapeHtml(JSON.stringify(step.prompt, null, 2))}</pre>
          </details>
          <details>
            <summary>Response</summary>
            <pre>${escapeHtml(JSON.stringify(step.response, null, 2))}</pre>
          </details>
        </article>
      `;
    });
    stepsContent.innerHTML = parts.join("\n");
  }

  function setLoading(isLoading) {
    submitBtn.disabled = isLoading;
    loadingIndicator.hidden = !isLoading;

    if (loadingStageTimer) {
      clearInterval(loadingStageTimer);
      loadingStageTimer = null;
    }

    if (!isLoading) return;

    let stageIndex = 0;
    loadingStage.textContent = LOADING_STAGES[stageIndex];
    loadingStage.classList.remove("fade");
    loadingStageTimer = setInterval(() => {
      stageIndex = (stageIndex + 1) % LOADING_STAGES.length;
      loadingStage.classList.add("fade");
      loadingStage.textContent = LOADING_STAGES[stageIndex];
      requestAnimationFrame(() => loadingStage.classList.remove("fade"));
    }, LOADING_STAGE_INTERVAL_MS);
  }

  function showError(message) {
    errorDisplay.textContent = message;
    errorDisplay.hidden = false;
  }

  function clearError() {
    errorDisplay.hidden = true;
    errorDisplay.textContent = "";
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    clearError();
    resultsSection.hidden = true;

    const prompt = promptInput.value.trim();
    if (!prompt) {
      showError("Please enter a request before submitting.");
      return;
    }

    setLoading(true);
    try {
      const response = await fetch("/api/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt }),
      });
      const data = await response.json();

      if (data.status === "ok") {
        resultsContent.innerHTML = safeMarkdownToHtml(data.response || "");
        renderSteps(data.steps);
        resultsSection.hidden = false;
      } else {
        showError(data.error || "The agent could not complete this request.");
      }
    } catch (err) {
      showError("A network error occurred while contacting the agent.");
    } finally {
      setLoading(false);
    }
  });
})();
