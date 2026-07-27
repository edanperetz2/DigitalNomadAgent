(function () {
  "use strict";

  const LEGACY_HISTORY_KEY = "digitalnomadagent.recentConversations.v1";
  const THEME_KEY = "digitalnomadagent.theme";
  const SIDEBAR_COLLAPSED_KEY = "digitalnomadagent.sidebarCollapsed";
  const SIDEBAR_WIDTH_KEY = "digitalnomadagent.sidebarWidth";
  const SIDEBAR_DEFAULT_WIDTH = 350;
  const SIDEBAR_MIN_WIDTH = 260;
  const SIDEBAR_MAX_WIDTH = 480;

  const shell = document.querySelector(".app-shell");
  const homeView = document.getElementById("home-view");
  const form = document.getElementById("execute-form");
  const promptInput = document.getElementById("prompt-input");
  const submitBtn = document.getElementById("submit-btn");
  const loadingIndicator = document.getElementById("loading-indicator");
  const loadingStage = document.getElementById("loading-stage");
  const loadingNote = document.getElementById("loading-note");
  const loadingTimerValue = document.getElementById("loading-timer-value");
  const errorDisplay = document.getElementById("error-display");
  const resultsSection = document.getElementById("results");
  const resultsContent = document.getElementById("results-content");
  const userRequestDisplay = document.getElementById("user-request-display");
  const stepsContent = document.getElementById("steps-content");
  const historyList = document.getElementById("history-list");
  const clearHistoryBtn = document.getElementById("clear-history-btn");
  const historyToolbar = document.getElementById("history-toolbar");
  // Scoped by container, not just the shared .history-filter-btn class -- the
  // sidebar stays visible on every view, so an unscoped query would also catch
  // the "All Conversations" page's filter buttons and cross-wire their clicks.
  const historyFilterButtons = Array.from(document.querySelectorAll("#history-toolbar .history-filter-btn"));
  const clearSelectedBtn = document.getElementById("clear-selected-btn");
  const allConversationsFilterButtons = Array.from(
    document.querySelectorAll("#conversations-view .history-filter-btn")
  );
  const sidebarToggleBtn = document.getElementById("sidebar-toggle");
  const sidebarResizeHandle = document.getElementById("sidebar-resize-handle");
  const backToChatBtn = document.getElementById("back-to-chat");
  const conversationsBackBtn = document.getElementById("conversations-back");
  const conversationsView = document.getElementById("conversations-view");
  const allConversationsList = document.getElementById("all-conversations-list");
  const viewAllConversationsBtn = document.getElementById("view-all-conversations");
  const themeToggle = document.getElementById("theme-toggle");
  const exampleRemoteWorkBtn = document.getElementById("example-remote-work");
  const exampleStudyBtn = document.getElementById("example-study");
  const exampleVacationBtn = document.getElementById("example-vacation");

  // Kept in sync with app/api/agent_info_content.py's _EXAMPLES_SOURCE so the
  // home-page buttons and /api/agent_info's illustrative examples agree.
  const EXAMPLE_PROMPTS = {
    remoteWork:
      "I want to spend three months somewhere in Europe where I can work remotely, " +
      "live without a car, and stay within €1,800 per month.",
    study:
      "Recommend a city for a one-semester computer-science exchange. I care about " +
      "student life, public transportation, safety, and affordable housing.",
    vacation:
      "Find a quiet beach destination for two weeks in October, with warm but not " +
      "extremely hot weather and good hiking nearby.",
  };

  // Simulated stage labels shown while waiting for /api/execute. These mirror
  // the agent's real state-machine order but are not driven by live backend
  // events (the API is a single request/response call, not a stream).
  const LOADING_STAGES = [
    "Interpreting your request...",
    "Generating candidate destinations...",
    "Verifying candidates (geocoding)...",
    "Checking weather and climate fit...",
    "Matching time zones...",
    "Running budget-fit tool...",
    "Checking amenities and walkability...",
    "Gathering official sources...",
    "Scoring and ranking candidates...",
    "Validating recommendation quality...",
    "Writing your recommendation...",
  ];
  const LOADING_STAGE_INTERVAL_MS = 1300;
  const LOADING_NOTE_DELAY_MS = 15000;
  const EXECUTE_TIMEOUT_MS = 295000;
  let loadingStageTimer = null;
  let loadingNoteTimer = null;
  let loadingElapsedTimer = null;
  let savedSearchSessions = [];
  let historyFilter = "all";
  let selectedHistoryIds = new Set();
  let allConversationsFilter = "all";
  let currentPrompt = "";

  function getStoredTheme() {
    try {
      return localStorage.getItem(THEME_KEY);
    } catch (_err) {
      return null;
    }
  }

  function setStoredTheme(theme) {
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch (_err) {
      // Ignore storage errors; the visible theme still updates for this page.
    }
  }

  function setTheme(theme) {
    const nextTheme = theme === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", nextTheme);
    themeToggle.setAttribute("aria-pressed", String(nextTheme === "dark"));
    themeToggle.setAttribute(
      "aria-label",
      nextTheme === "dark" ? "Switch to light mode" : "Switch to dark mode"
    );
    themeToggle.innerHTML =
      nextTheme === "dark"
        ? '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 3v2M12 19v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M3 12h2M19 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4M8 12a4 4 0 1 0 8 0 4 4 0 0 0-8 0Z" /></svg>'
        : '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M20.5 14.4A7.7 7.7 0 0 1 9.6 3.5 8.5 8.5 0 1 0 20.5 14.4Z" /></svg>';
    setStoredTheme(nextTheme);
  }

  function initializeTheme() {
    const storedTheme = getStoredTheme();
    setTheme(storedTheme || "light");
  }

  function setSidebarCollapsed(collapsed) {
    shell.classList.toggle("sidebar-collapsed", collapsed);
    sidebarToggleBtn.setAttribute("aria-pressed", String(collapsed));
    sidebarToggleBtn.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
    try {
      localStorage.setItem(SIDEBAR_COLLAPSED_KEY, collapsed ? "1" : "0");
    } catch (_err) {
      // Ignore storage errors; the sidebar still toggles for this page load.
    }
  }

  function initializeSidebar() {
    let collapsed = false;
    try {
      collapsed = localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === "1";
    } catch (_err) {
      collapsed = false;
    }
    setSidebarCollapsed(collapsed);
  }

  function setSidebarWidth(width) {
    const clamped = Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, Math.round(width)));
    document.documentElement.style.setProperty("--sidebar-width", `${clamped}px`);
    try {
      localStorage.setItem(SIDEBAR_WIDTH_KEY, String(clamped));
    } catch (_err) {
      // Ignore storage errors; the sidebar still resizes for this page load.
    }
    return clamped;
  }

  function initializeSidebarWidth() {
    let width = SIDEBAR_DEFAULT_WIDTH;
    try {
      const stored = parseInt(localStorage.getItem(SIDEBAR_WIDTH_KEY), 10);
      if (Number.isFinite(stored)) width = stored;
    } catch (_err) {
      width = SIDEBAR_DEFAULT_WIDTH;
    }
    setSidebarWidth(width);
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text == null ? "" : String(text);
    return div.innerHTML;
  }

  function inlineMarkdownToHtml(text) {
    let line = escapeHtml(text);
    line = line.replace(
      /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
    );
    line = line.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    return line;
  }

  function splitTableRow(rawLine) {
    return rawLine
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((cell) => cell.trim());
  }

  function closeList(htmlParts, listState) {
    if (listState.open) {
      htmlParts.push("</ul>");
      listState.open = false;
    }
  }

  // Small, whitelist-only Markdown-subset renderer. Input is always
  // HTML-escaped first; only a narrow set of structural transforms is then
  // applied. Raw HTML from the LLM/response is never inserted directly.
  function safeMarkdownToHtml(markdown) {
    const lines = String(markdown || "").split(/\r?\n/);
    const htmlParts = [];
    const listState = { open: false };
    let inTable = false;
    let tableHeaderPending = false;

    function closeTable() {
      if (inTable) {
        htmlParts.push("</table></div>");
        inTable = false;
        tableHeaderPending = false;
      }
    }

    for (const rawLine of lines) {
      if (/^\s*\|.*\|\s*$/.test(rawLine)) {
        closeList(htmlParts, listState);
        if (!inTable) {
          htmlParts.push('<div class="table-wrap"><table class="md-table">');
          inTable = true;
          tableHeaderPending = true;
        }
        const cells = splitTableRow(rawLine);
        const isSeparator = cells.every((cell) => /^:?-+:?$/.test(cell));
        if (!isSeparator) {
          const tag = tableHeaderPending ? "th" : "td";
          htmlParts.push(
            "<tr>" + cells.map((cell) => `<${tag}>${inlineMarkdownToHtml(cell)}</${tag}>`).join("") + "</tr>"
          );
          tableHeaderPending = false;
        }
        continue;
      }

      closeTable();

      const trimmed = rawLine.trim();
      if (!trimmed) {
        closeList(htmlParts, listState);
        continue;
      }

      if (/^### /.test(rawLine)) {
        closeList(htmlParts, listState);
        htmlParts.push(`<h4>${inlineMarkdownToHtml(rawLine.replace(/^### /, ""))}</h4>`);
      } else if (/^## /.test(rawLine)) {
        closeList(htmlParts, listState);
        htmlParts.push(`<h3>${inlineMarkdownToHtml(rawLine.replace(/^## /, ""))}</h3>`);
      } else if (/^# /.test(rawLine)) {
        closeList(htmlParts, listState);
        htmlParts.push(`<h2>${inlineMarkdownToHtml(rawLine.replace(/^# /, ""))}</h2>`);
      } else if (/^\s*[-*]\s+/.test(rawLine)) {
        if (!listState.open) {
          htmlParts.push("<ul>");
          listState.open = true;
        }
        htmlParts.push(`<li>${inlineMarkdownToHtml(rawLine.replace(/^\s*[-*]\s+/, ""))}</li>`);
      } else {
        closeList(htmlParts, listState);
        htmlParts.push(`<p>${inlineMarkdownToHtml(rawLine)}</p>`);
      }
    }

    closeList(htmlParts, listState);
    closeTable();
    return htmlParts.join("\n");
  }

  function normalizeTitle(title) {
    return String(title || "")
      .toLowerCase()
      .replace(/&/g, " and ")
      .replace(/[^a-z0-9]+/g, " ")
      .trim();
  }

  function normalizeHeader(header) {
    return normalizeTitle(header).replace(/\s+/g, "_");
  }

  function splitSections(markdown) {
    const sections = [];
    let current = null;

    for (const line of String(markdown || "").split(/\r?\n/)) {
      const heading = /^(#{2,4})\s+(.+?)\s*$/.exec(line);
      if (heading) {
        if (current) sections.push(current);
        current = {
          level: heading[1].length,
          title: heading[2].trim(),
          lines: [],
        };
      } else if (current) {
        current.lines.push(line);
      }
    }

    if (current) sections.push(current);
    return sections;
  }

  function findSection(sections, acceptedTitles) {
    const accepted = new Set(acceptedTitles);
    return sections.find((section) => accepted.has(normalizeTitle(section.title))) || null;
  }

  function parseMarkdownTable(lines) {
    const tableLines = lines.filter((line) => /^\s*\|.*\|\s*$/.test(line));
    if (tableLines.length < 2) return [];

    const headers = splitTableRow(tableLines[0]).map(normalizeHeader);
    const rows = [];

    for (const line of tableLines.slice(1)) {
      const cells = splitTableRow(line);
      const isSeparator = cells.every((cell) => /^:?-+:?$/.test(cell));
      if (isSeparator) continue;

      const row = {};
      headers.forEach((header, index) => {
        row[header] = cells[index] || "";
      });
      rows.push(row);
    }

    return rows;
  }

  function parsePlaceTitle(title) {
    const match = /^(\d+)\.\s+(.+)$/.exec(title.trim());
    const rank = match ? match[1] : "";
    const label = match ? match[2].trim() : title.trim();
    const parts = label.split(",");
    const place = (parts.shift() || label).trim();
    const country = parts.join(",").trim();
    return { rank, place, country, label };
  }

  function parsePlaceSection(section) {
    const parsedTitle = parsePlaceTitle(section.title);
    const detail = {
      ...parsedTitle,
      why: [],
      drawbacks: [],
      limitations: [],
      confidence: "",
      other: [],
    };

    section.lines.forEach((line) => {
      const trimmed = line.trim();
      if (!trimmed) return;

      const bullet = /^[-*]\s+(.+)$/.exec(trimmed);
      const text = bullet ? bullet[1].trim() : trimmed;
      const pair = /^([^:]+):\s*(.*)$/.exec(text);
      if (!pair) {
        detail.other.push(text);
        return;
      }

      const key = normalizeTitle(pair[1]);
      const value = pair[2].trim();
      if (key === "why it fits") {
        detail.why.push(value);
      } else if (key === "main drawback") {
        detail.drawbacks.push(value);
      } else if (key === "evidence limitations" || key === "evidence limitation") {
        detail.limitations.push(value);
      } else if (key === "confidence") {
        detail.confidence = value;
      } else {
        detail.other.push(text);
      }
    });

    return detail;
  }

  function parseRecommendationMarkdown(markdown) {
    const sections = splitSections(markdown);
    if (!sections.length) return null;

    const interpretation = findSection(sections, ["interpretation"]);
    const bestMatchesSection = findSection(sections, ["best matches"]);
    const tradeoffs = findSection(sections, ["trade offs", "tradeoffs"]);
    const assumptions = findSection(sections, ["assumptions and limitations", "assumptions limitations"]);
    const sources = findSection(sections, ["sources"]);
    const bestMatches = bestMatchesSection ? parseMarkdownTable(bestMatchesSection.lines) : [];
    const details = sections
      .filter((section) => section.level >= 3 && /^\d+\.\s+/.test(section.title))
      .map(parsePlaceSection);

    if (!interpretation && !bestMatches.length && !details.length && !tradeoffs && !assumptions) {
      return null;
    }

    return {
      interpretation,
      bestMatches,
      details,
      tradeoffs,
      assumptions,
      sources,
    };
  }

  function confidenceClass(confidence) {
    const normalized = normalizeTitle(confidence);
    if (normalized.includes("high")) return "confidence-high";
    if (normalized.includes("low")) return "confidence-low";
    return "confidence-medium";
  }

  function getInitials(text) {
    const words = String(text || "PM")
      .replace(/[^\w\s]/g, " ")
      .split(/\s+/)
      .filter(Boolean);
    return words
      .slice(0, 2)
      .map((word) => word[0])
      .join("")
      .toUpperCase() || "PM";
  }

  function imageUrlFromCandidate(match, detail) {
    return (
      detail.image_url ||
      detail.imageUrl ||
      detail.thumbnail_url ||
      detail.thumbnailUrl ||
      match.image_url ||
      match.imageUrl ||
      match.thumbnail_url ||
      match.thumbnailUrl ||
      match.image ||
      ""
    );
  }

  function placeImageKey(item) {
    return [item.place, item.country].filter(Boolean).join(", ").trim();
  }

  function thumbMarkup(item) {
    const query = escapeHtml(placeImageKey(item));
    const label = escapeHtml(item.place || "Destination");
    const src = item.imageUrl ? ` src="${escapeHtml(item.imageUrl)}"` : "";
    return `
      <span class="place-thumb" data-place-query="${query}">
        <img${src} alt="${label}" loading="lazy" />
        <span class="place-thumb-fallback">${escapeHtml(getInitials(item.place))}</span>
      </span>
    `;
  }

  function applyThumbImage(thumb, src) {
    const image = thumb.querySelector("img");
    if (!image || !src) return;
    image.addEventListener(
      "error",
      () => {
        image.removeAttribute("src");
      },
      { once: true }
    );
    image.src = src;
  }

  async function findWikivoyageImage(query) {
    const candidates = Array.from(
      new Set(
        String(query || "")
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean)
          .concat(String(query || "").trim())
      )
    ).filter(Boolean);

    for (const candidate of candidates) {
      try {
        const response = await fetch(
          `https://en.wikivoyage.org/w/api.php?action=query&origin=*&format=json&prop=pageimages&piprop=thumbnail&pithumbsize=220&titles=${encodeURIComponent(candidate)}`
        );
        if (!response.ok) continue;
        const data = await response.json();
        const pages = data.query?.pages || {};
        const image = Object.values(pages).find((page) => page?.thumbnail?.source)?.thumbnail?.source;
        if (image) return image;
      } catch (_err) {
        break;
      }
    }
    return "";
  }

  async function findWikipediaImage(query) {
    const candidates = Array.from(
      new Set(
        String(query || "")
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean)
          .concat(String(query || "").trim())
      )
    ).filter(Boolean);

    for (const candidate of candidates) {
      try {
        const response = await fetch(
          `https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(candidate)}`
        );
        if (!response.ok) continue;
        const data = await response.json();
        const image = data.thumbnail?.source || data.originalimage?.source;
        if (image) return image;
      } catch (_err) {
        break;
      }
    }
    return "";
  }

  async function findDestinationImage(query) {
    return (await findWikipediaImage(query)) || (await findWikivoyageImage(query));
  }

  function hydratePlaceImages() {
    document.querySelectorAll(".place-thumb[data-place-query]").forEach((thumb) => {
      const query = thumb.getAttribute("data-place-query");
      const currentImage = thumb.querySelector("img");
      if (currentImage && currentImage.getAttribute("src")) return;
      if (!query || thumb.dataset.imageResolved === "true") return;
      thumb.dataset.imageResolved = "true";
      findDestinationImage(query).then((src) => {
        if (src) applyThumbImage(thumb, src);
      });
    });
  }

  function stripMarkdownSyntax(text) {
    return String(text || "")
      .replace(/\*\*/g, "")
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, "$1");
  }

  function shortTitle(prompt) {
    const cleaned = stripMarkdownSyntax(prompt).replace(/\s+/g, " ").trim();
    if (cleaned.length <= 48) return cleaned;
    return `${cleaned.slice(0, 45).trim()}...`;
  }

  function formatTimeLabel(isoDate) {
    const timestamp = Date.parse(isoDate);
    if (!Number.isFinite(timestamp)) return "recent";
    const elapsedSeconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
    if (elapsedSeconds < 60) return "now";
    const elapsedMinutes = Math.floor(elapsedSeconds / 60);
    if (elapsedMinutes < 60) return `${elapsedMinutes}m ago`;
    const elapsedHours = Math.floor(elapsedMinutes / 60);
    if (elapsedHours < 24) return `${elapsedHours}h ago`;
    const elapsedDays = Math.floor(elapsedHours / 24);
    return `${elapsedDays}d ago`;
  }

  function sectionHtml(section) {
    if (!section) return "";
    const body = section.lines.join("\n").trim();
    return body ? safeMarkdownToHtml(body) : "<p>Not specified.</p>";
  }

  function listHtml(items) {
    const values = items.filter(Boolean);
    if (!values.length) return "<p>Not specified.</p>";
    return `<ul>${values.map((item) => `<li>${inlineMarkdownToHtml(item)}</li>`).join("")}</ul>`;
  }

  function mergeRecommendationItems(parsed) {
    const detailsByRank = new Map(parsed.details.map((detail) => [String(detail.rank), detail]));
    const detailsByPlace = new Map(parsed.details.map((detail) => [normalizeTitle(detail.place), detail]));

    const items = parsed.bestMatches.map((match, index) => {
      const rank = String(match.rank || index + 1);
      const detail = detailsByRank.get(rank) || detailsByPlace.get(normalizeTitle(match.place)) || {};
      const parsedPlace = parsePlaceTitle(`${rank}. ${match.place || detail.place || "Unknown place"}`);
      return {
        rank,
        place: detail.place || parsedPlace.place,
        country: detail.country || parsedPlace.country,
        imageUrl: imageUrlFromCandidate(match, detail),
        why: detail.why && detail.why.length ? detail.why : [match.why_it_fits].filter(Boolean),
        drawbacks:
          detail.drawbacks && detail.drawbacks.length ? detail.drawbacks : [match.main_drawback].filter(Boolean),
        limitations: detail.limitations || [],
        confidence: detail.confidence || match.confidence || "Medium",
        other: detail.other || [],
      };
    });

    parsed.details.forEach((detail) => {
      const exists = items.some(
        (item) =>
          String(item.rank) === String(detail.rank) ||
          normalizeTitle(item.place) === normalizeTitle(detail.place)
      );
      if (!exists) {
        items.push({
          rank: detail.rank || String(items.length + 1),
          place: detail.place,
          country: detail.country,
          imageUrl: imageUrlFromCandidate({}, detail),
          why: detail.why,
          drawbacks: detail.drawbacks,
          limitations: detail.limitations,
          confidence: detail.confidence || "Medium",
          other: detail.other,
        });
      }
    });

    return items;
  }

  function renderBestMatchesTable(items) {
    if (!items.length) return "";

    const rows = items
      .map((item) => {
        const confidence = item.confidence || "Medium";
        return `
          <tr>
            <td class="rank-cell"><span class="rank-badge">${escapeHtml(item.rank)}</span></td>
            <td>
              <div class="place-cell">
                ${thumbMarkup(item)}
                <span>
                  <span class="place-name">${inlineMarkdownToHtml(item.place)}</span>
                  ${item.country ? `<span class="place-country">${inlineMarkdownToHtml(item.country)}</span>` : ""}
                </span>
              </div>
            </td>
            <td>${inlineMarkdownToHtml(item.why[0] || "Not specified.")}</td>
            <td>${inlineMarkdownToHtml(item.drawbacks[0] || "Not specified.")}</td>
            <td><span class="confidence-pill ${confidenceClass(confidence)}">${inlineMarkdownToHtml(confidence)}</span></td>
          </tr>
        `;
      })
      .join("");

    return `
      <section class="table-card" aria-labelledby="best-matches-heading">
        <h3 id="best-matches-heading">Best matches</h3>
        <div class="table-wrap">
          <table class="recommendations-table">
            <thead>
              <tr>
                <th>Rank</th>
                <th>Place</th>
                <th>Why it fits</th>
                <th>Main drawback</th>
                <th>Confidence</th>
              </tr>
            </thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </section>
    `;
  }

  function renderDetails(items) {
    if (!items.length) return "";

    const cards = items
      .map((item, index) => {
        const title = `${item.rank}. ${item.place}${item.country ? `, ${item.country}` : ""}`;
        const mainDrawback = item.drawbacks[0] || "Not specified.";
        const secondaryDetails = [
          ...item.drawbacks.slice(1).map((drawback) => `Main drawback: ${drawback}`),
          ...item.limitations.map((limitation) => `Evidence limitations: ${limitation}`),
        ];
        const confidence = item.confidence || "Medium";
        const extra = item.other && item.other.length
          ? `<div class="detail-extra markdown-body">${safeMarkdownToHtml(item.other.join("\n"))}</div>`
          : "";

        return `
          <details class="place-card" ${index === 0 ? "open" : ""}>
            <summary>
              ${thumbMarkup(item)}
              <span class="place-card-title">
                ${inlineMarkdownToHtml(title)}
              </span>
              <span class="confidence-pill ${confidenceClass(confidence)}">${inlineMarkdownToHtml(confidence)}</span>
              <span class="chevron" aria-hidden="true">
                <svg viewBox="0 0 24 24" focusable="false"><path d="m6 9 6 6 6-6" /></svg>
              </span>
            </summary>
            <div class="place-detail-grid">
              <div class="detail-group">
                <h4>Why it fits</h4>
                ${listHtml(item.why)}
              </div>
              <div class="detail-group">
                <h4>Main drawback</h4>
                ${listHtml([mainDrawback, ...secondaryDetails])}
              </div>
              <div class="confidence-panel">
                <span class="confidence-panel-icon" aria-hidden="true">
                  <svg viewBox="0 0 24 24" focusable="false">
                    <path d="m12 3 2.7 5.5 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1-4.4-4.3 6.1-.9L12 3Z" />
                  </svg>
                </span>
                <span>Confidence</span>
                <span class="confidence-pill ${confidenceClass(confidence)}">${inlineMarkdownToHtml(confidence)}</span>
              </div>
              ${extra}
            </div>
          </details>
        `;
      })
      .join("");

    return `
      <section class="details-section" aria-labelledby="recommendation-details-heading">
        <h3 id="recommendation-details-heading">Recommendation details</h3>
        ${cards}
      </section>
    `;
  }

  function renderInfoPanels(parsed) {
    const tradeoffsHtml = sectionHtml(parsed.tradeoffs);
    const assumptionsHtml = sectionHtml(parsed.assumptions);

    const sourcesHtml = parsed.sources
      ? `
        <section class="sources-panel markdown-body">
          <details>
            <summary>Sources</summary>
            <div>${sectionHtml(parsed.sources)}</div>
          </details>
        </section>
      `
      : "";

    return `
      <div class="info-grid">
        <article class="info-panel">
          <span class="info-icon info-trade" aria-hidden="true">
            <svg viewBox="0 0 24 24" focusable="false">
              <path d="M12 3v18M6 7h12M4 7l-3 6h6L4 7ZM20 7l-3 6h6l-3-6ZM8 21h8" />
            </svg>
          </span>
          <div class="markdown-body">
            <h3>Trade-offs</h3>
            ${tradeoffsHtml}
          </div>
        </article>
        <article class="info-panel">
          <span class="info-icon info-assumptions" aria-hidden="true">
            <svg viewBox="0 0 24 24" focusable="false">
              <path d="M12 3 5 6v5.8c0 4.3 2.8 7.8 7 9.2 4.2-1.4 7-4.9 7-9.2V6l-7-3Z" />
              <path d="M12 8v5M12 17h.01" />
            </svg>
          </span>
          <div class="markdown-body">
            <h3>Assumptions and limitations</h3>
            ${assumptionsHtml}
          </div>
        </article>
      </div>
      ${sourcesHtml}
    `;
  }

  function renderStructuredRecommendation(markdown, prompt) {
    const parsed = parseRecommendationMarkdown(markdown);
    if (!parsed) {
      return `<div class="recommendation-board markdown-body">${safeMarkdownToHtml(markdown)}</div>`;
    }

    const items = mergeRecommendationItems(parsed);
    if (!items.length) {
      return `<div class="recommendation-board markdown-body">${safeMarkdownToHtml(markdown)}</div>`;
    }

    return `
      <div class="recommendation-board">
        ${renderDetails(items)}
        ${renderInfoPanels(parsed)}
      </div>
    `;
  }

  // The orchestrator's interactive-mode clarification dead-end is the only path
  // that ever returns after exactly one Request Interpreter step -- a reliable
  // signal to show a reply box instead of trying to parse the question as a
  // recommendation table.
  function isClarificationSteps(steps) {
    return Array.isArray(steps) && steps.length === 1 && steps[0] && steps[0].module === "Request Interpreter";
  }

  function renderClarificationPrompt(question) {
    return `
      <div class="clarification-board">
        <div class="clarification-callout">
          <span class="clarification-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24" focusable="false">
              <path d="M12 3 5 6v5.8c0 4.3 2.8 7.8 7 9.2 4.2-1.4 7-4.9 7-9.2V6l-7-3Z" />
              <path d="M12 8v5M12 17h.01" />
            </svg>
          </span>
          <p>${escapeHtml(question)}</p>
        </div>
        <form id="clarification-reply-form" class="clarification-reply-form">
          <label for="clarification-reply-input">Add the missing details and continue</label>
          <textarea
            id="clarification-reply-input"
            rows="3"
            placeholder="e.g. remote work, budget €1,500/month, about 2 months"
            required
          ></textarea>
          <button type="submit">Continue</button>
        </form>
      </div>
    `;
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

  function formatElapsed(ms) {
    const totalSeconds = Math.max(0, Math.floor(ms / 1000));
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}:${String(seconds).padStart(2, "0")}`;
  }

  function setLoading(isLoading) {
    submitBtn.disabled = isLoading;
    loadingIndicator.hidden = !isLoading;

    if (loadingStageTimer) {
      clearInterval(loadingStageTimer);
      loadingStageTimer = null;
    }
    if (loadingNoteTimer) {
      clearTimeout(loadingNoteTimer);
      loadingNoteTimer = null;
    }
    if (loadingElapsedTimer) {
      clearInterval(loadingElapsedTimer);
      loadingElapsedTimer = null;
    }
    loadingNote.hidden = true;

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

    // Real requests commonly take 40-110+ seconds (serial geocoding + external
    // tool calls) -- without this, that silence reads as a hung/broken page.
    loadingNoteTimer = setTimeout(() => {
      loadingNote.hidden = false;
    }, LOADING_NOTE_DELAY_MS);

    const startedAt = Date.now();
    loadingTimerValue.textContent = "0:00";
    loadingElapsedTimer = setInterval(() => {
      loadingTimerValue.textContent = formatElapsed(Date.now() - startedAt);
    }, 1000);
  }

  function showError(message) {
    errorDisplay.textContent = message;
    errorDisplay.hidden = false;
  }

  function clearError() {
    errorDisplay.hidden = true;
    errorDisplay.textContent = "";
  }

  function setActiveConversation(activeButton) {
    document.querySelectorAll(".conversation-card").forEach((button) => {
      button.classList.toggle("active", button === activeButton);
    });
  }

  function showHome(clearPrompt) {
    shell.classList.remove("results-active");
    resultsSection.hidden = true;
    conversationsView.hidden = true;
    homeView.hidden = false;
    if (window.location.hash === "#conversations") {
      window.history.pushState({}, "", window.location.pathname);
    }
    clearError();
    if (clearPrompt) {
      promptInput.value = "";
      setActiveConversation(null);
    }
    promptInput.focus();
  }

  function showResults(prompt, markdown, steps) {
    currentPrompt = prompt || "";
    userRequestDisplay.textContent = prompt ? `Based on your request: "${prompt}"` : "";
    if (window.location.hash === "#conversations") {
      window.history.replaceState({}, "", window.location.pathname);
    }
    resultsContent.innerHTML = isClarificationSteps(steps)
      ? renderClarificationPrompt(markdown || "")
      : renderStructuredRecommendation(markdown || "", prompt);
    hydratePlaceImages();
    renderSteps(steps);
    homeView.hidden = true;
    conversationsView.hidden = true;
    resultsSection.hidden = false;
    shell.classList.add("results-active");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function showResultNotFound() {
    userRequestDisplay.textContent = "";
    resultsContent.innerHTML = `
      <div class="recommendation-board markdown-body">
        <h2 class="section-kicker">Result not found</h2>
        <p>This saved search is no longer available.</p>
      </div>
    `;
    renderSteps([]);
    homeView.hidden = true;
    conversationsView.hidden = true;
    resultsSection.hidden = false;
    shell.classList.add("results-active");
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function clearLegacyHistory() {
    try {
      localStorage.removeItem(LEGACY_HISTORY_KEY);
    } catch (_err) {
      // Ignore storage errors; legacy browser-only history is not used anymore.
    }
  }

  function normalizeSavedSession(session) {
    const result = session?.result || {};
    const prompt = session?.prompt || session?.original_request || session?.originalRequest || "";
    const response = result.response || session?.response || "";
    if (!session?.id || !prompt || !response) return null;
    return {
      id: String(session.id),
      title: session.title || shortTitle(prompt),
      prompt,
      response,
      steps: Array.isArray(result.steps) ? result.steps : Array.isArray(session.steps) ? session.steps : [],
      result: {
        status: result.status || "ok",
        error: result.error || null,
        response,
        steps: Array.isArray(result.steps) ? result.steps : Array.isArray(session.steps) ? session.steps : [],
      },
      createdAt: session.createdAt || session.created_at || new Date().toISOString(),
      updatedAt: session.updatedAt || session.updated_at || session.createdAt || session.created_at || new Date().toISOString(),
    };
  }

  function readBootstrappedSessions() {
    const script = document.getElementById("saved-search-sessions");
    if (!script) return [];
    try {
      const parsed = JSON.parse(script.textContent || "[]");
      if (!Array.isArray(parsed)) return [];
      return parsed.map(normalizeSavedSession).filter(Boolean);
    } catch (_err) {
      return [];
    }
  }

  function saveConversation(prompt, data, sessionId) {
    if (!sessionId) return;
    const now = new Date().toISOString();
    const session = normalizeSavedSession({
      id: sessionId,
      title: shortTitle(prompt),
      prompt,
      result: data,
      createdAt: now,
      updatedAt: now,
    });
    if (!session) return;
    savedSearchSessions = [
      session,
      ...savedSearchSessions.filter((item) => item.id !== session.id && item.prompt !== session.prompt),
    ];
    renderHistory();
  }

  // Derived from the disclosure line app/agent/recommendation_generator.py's
  // _mode_disclosure_line()/render_recommendation_fallback() always appends to a
  // successful response's markdown -- the only 3 possible strings it can contain.
  function sessionMode(item) {
    const text = item.response || "";
    if (text.includes("a real LLM provider")) return "llm";
    // Catches both "a deterministic fallback template" and "mock deterministic
    // mode" -- per explicit instruction, mock counts as fallback here, not LLM.
    if (text.includes("deterministic")) return "fallback";
    return "unknown";
  }

  function pruneSelectedHistoryIds() {
    const validIds = new Set(savedSearchSessions.map((item) => item.id));
    selectedHistoryIds.forEach((id) => {
      if (!validIds.has(id)) selectedHistoryIds.delete(id);
    });
  }

  function updateClearSelectedButton() {
    const count = selectedHistoryIds.size;
    clearSelectedBtn.disabled = count === 0;
    clearSelectedBtn.textContent = count ? `Clear selected (${count})` : "Clear selected";
  }

  function renderHistory() {
    pruneSelectedHistoryIds();
    clearHistoryBtn.hidden = !savedSearchSessions.length;
    historyToolbar.hidden = !savedSearchSessions.length;
    updateClearSelectedButton();

    if (!savedSearchSessions.length) {
      historyList.hidden = true;
      historyList.innerHTML = "";
      return;
    }

    const filtered = savedSearchSessions.filter(
      (item) => historyFilter === "all" || sessionMode(item) === historyFilter
    );

    if (!filtered.length) {
      historyList.hidden = true;
      historyList.innerHTML = "";
      historyToolbar.insertAdjacentHTML(
        "afterend",
        '<p class="history-empty-note" id="history-empty-note">No conversations match this filter.</p>'
      );
      return;
    }

    const existingNote = document.getElementById("history-empty-note");
    if (existingNote) existingNote.remove();

    historyList.hidden = false;
    historyList.innerHTML = filtered
      .map(
        (item) => `
          <div class="conversation-card history-card" data-history-id="${escapeHtml(item.id)}">
            <input
              type="checkbox"
              class="history-checkbox"
              data-history-id="${escapeHtml(item.id)}"
              aria-label="Select conversation: ${escapeHtml(shortTitle(item.title || item.prompt))}"
              ${selectedHistoryIds.has(item.id) ? "checked" : ""}
            />
            <button type="button" class="conversation-open" data-history-id="${escapeHtml(item.id)}">
              <span class="conversation-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24" focusable="false">
                  <path d="M21 11.5a7.5 7.5 0 0 1-8 7.5 8.7 8.7 0 0 1-3.5-.8L4 20l1.8-5.1A7.5 7.5 0 1 1 21 11.5Z" />
                </svg>
              </span>
              <span class="conversation-text">${escapeHtml(shortTitle(item.title || item.prompt))}</span>
              <span class="conversation-time">${escapeHtml(formatTimeLabel(item.updatedAt))}</span>
            </button>
          </div>
        `
      )
      .join("");
  }

  function allConversationItems() {
    return savedSearchSessions
      .filter((item) => allConversationsFilter === "all" || sessionMode(item) === allConversationsFilter)
      .map((item) => ({
        type: "history",
        id: item.id,
        title: item.title || shortTitle(item.prompt),
        prompt: item.prompt,
        updatedAt: item.updatedAt,
      }));
  }

  function renderAllConversations() {
    const items = allConversationItems();
    if (!items.length) {
      const message = savedSearchSessions.length
        ? "No conversations match this filter."
        : "No conversations are available yet.";
      allConversationsList.innerHTML = `<div class="empty-conversations"><p>${escapeHtml(message)}</p></div>`;
      return;
    }

    allConversationsList.innerHTML = items
      .map((item) => {
        return `
          <button type="button" class="all-conversation-card" data-conversation-type="${escapeHtml(item.type)}" data-conversation-id="${escapeHtml(item.id)}">
            <span class="conversation-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" focusable="false">
                <path d="M21 11.5a7.5 7.5 0 0 1-8 7.5 8.7 8.7 0 0 1-3.5-.8L4 20l1.8-5.1A7.5 7.5 0 1 1 21 11.5Z" />
              </svg>
            </span>
            <span>
              <strong>${escapeHtml(item.title)}</strong>
              <small>${escapeHtml(item.prompt)}</small>
            </span>
            <span class="conversation-time">${escapeHtml(formatTimeLabel(item.updatedAt))}</span>
          </button>
        `;
      })
      .join("");
  }

  function showConversations(pushState) {
    renderAllConversations();
    homeView.hidden = true;
    resultsSection.hidden = true;
    conversationsView.hidden = false;
    shell.classList.remove("results-active");
    if (pushState) {
      window.history.pushState({ view: "conversations" }, "", "#conversations");
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function openConversation(type, id) {
    if (type === "history") {
      const item = savedSearchSessions.find((historyItem) => historyItem.id === id);
      if (!item) return false;
      promptInput.value = item.prompt;
      showResults(item.prompt, item.response || "", item.steps || []);
      return true;
    }

    return false;
  }

  historyList.addEventListener("click", (event) => {
    if (event.target.closest(".history-checkbox")) return;
    const button = event.target.closest(".conversation-open");
    if (!button) return;
    const item = savedSearchSessions.find((historyItem) => historyItem.id === button.getAttribute("data-history-id"));
    if (item) {
      promptInput.value = item.prompt;
      setActiveConversation(button.closest(".conversation-card"));
      showResults(item.prompt, item.response || "", item.steps || []);
    } else {
      showResultNotFound();
    }
  });

  historyList.addEventListener("change", (event) => {
    const checkbox = event.target.closest(".history-checkbox");
    if (!checkbox) return;
    const id = checkbox.getAttribute("data-history-id");
    if (checkbox.checked) {
      selectedHistoryIds.add(id);
    } else {
      selectedHistoryIds.delete(id);
    }
    updateClearSelectedButton();
  });

  historyFilterButtons.forEach((filterBtn) => {
    filterBtn.addEventListener("click", () => {
      historyFilter = filterBtn.getAttribute("data-filter");
      historyFilterButtons.forEach((btn) => btn.classList.toggle("active", btn === filterBtn));
      renderHistory();
    });
  });

  allConversationsFilterButtons.forEach((filterBtn) => {
    filterBtn.addEventListener("click", () => {
      allConversationsFilter = filterBtn.getAttribute("data-filter");
      allConversationsFilterButtons.forEach((btn) => btn.classList.toggle("active", btn === filterBtn));
      renderAllConversations();
    });
  });

  clearHistoryBtn.addEventListener("click", async () => {
    if (!savedSearchSessions.length) return;
    const confirmed = window.confirm("Clear all conversation history? This cannot be undone.");
    if (!confirmed) return;
    clearHistoryBtn.disabled = true;
    try {
      const response = await fetch("/api/history", { method: "DELETE" });
      if (!response.ok) throw new Error(`Unexpected status ${response.status}`);
      savedSearchSessions = [];
      selectedHistoryIds.clear();
      renderHistory();
      renderAllConversations();
      setActiveConversation(null);
    } catch (_err) {
      showError("Could not clear history -- a network error occurred.");
    } finally {
      clearHistoryBtn.disabled = false;
    }
  });

  clearSelectedBtn.addEventListener("click", async () => {
    if (!selectedHistoryIds.size) return;
    const count = selectedHistoryIds.size;
    const confirmed = window.confirm(
      `Clear ${count} selected conversation${count === 1 ? "" : "s"}? This cannot be undone.`
    );
    if (!confirmed) return;
    clearSelectedBtn.disabled = true;
    try {
      const ids = Array.from(selectedHistoryIds);
      const response = await fetch("/api/history/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      });
      if (!response.ok) throw new Error(`Unexpected status ${response.status}`);
      savedSearchSessions = savedSearchSessions.filter((item) => !selectedHistoryIds.has(item.id));
      selectedHistoryIds.clear();
      renderHistory();
      renderAllConversations();
      setActiveConversation(null);
    } catch (_err) {
      showError("Could not clear the selected conversations -- a network error occurred.");
    } finally {
      updateClearSelectedButton();
    }
  });

  backToChatBtn.addEventListener("click", () => showHome(false));
  conversationsBackBtn.addEventListener("click", () => showHome(false));
  viewAllConversationsBtn.addEventListener("click", () => {
    showConversations(true);
  });
  allConversationsList.addEventListener("click", (event) => {
    const button = event.target.closest("[data-conversation-id]");
    if (!button) return;
    const opened = openConversation(
      button.getAttribute("data-conversation-type"),
      button.getAttribute("data-conversation-id")
    );
    if (!opened) {
      showResultNotFound();
    }
  });
  themeToggle.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme");
    setTheme(current === "dark" ? "light" : "dark");
  });

  sidebarToggleBtn.addEventListener("click", () => {
    setSidebarCollapsed(!shell.classList.contains("sidebar-collapsed"));
  });

  let isResizingSidebar = false;

  sidebarResizeHandle.addEventListener("mousedown", (event) => {
    if (shell.classList.contains("sidebar-collapsed")) return;
    event.preventDefault();
    isResizingSidebar = true;
    sidebarResizeHandle.classList.add("resizing");
    shell.classList.add("sidebar-resizing");
    document.body.style.userSelect = "none";
  });

  document.addEventListener("mousemove", (event) => {
    if (!isResizingSidebar) return;
    setSidebarWidth(event.clientX);
  });

  document.addEventListener("mouseup", () => {
    if (!isResizingSidebar) return;
    isResizingSidebar = false;
    sidebarResizeHandle.classList.remove("resizing");
    shell.classList.remove("sidebar-resizing");
    document.body.style.userSelect = "";
  });

  sidebarResizeHandle.addEventListener("keydown", (event) => {
    const current =
      parseInt(getComputedStyle(document.documentElement).getPropertyValue("--sidebar-width"), 10) ||
      SIDEBAR_DEFAULT_WIDTH;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setSidebarWidth(current - 12);
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      setSidebarWidth(current + 12);
    }
  });

  function fillExamplePrompt(text) {
    promptInput.value = text;
    promptInput.focus();
  }
  exampleRemoteWorkBtn.addEventListener("click", () => fillExamplePrompt(EXAMPLE_PROMPTS.remoteWork));
  exampleStudyBtn.addEventListener("click", () => fillExamplePrompt(EXAMPLE_PROMPTS.study));
  exampleVacationBtn.addEventListener("click", () => fillExamplePrompt(EXAMPLE_PROMPTS.vacation));

  window.addEventListener("popstate", () => {
    if (window.location.hash === "#conversations") {
      showConversations(false);
    } else {
      showHome(false);
    }
  });

  async function runExecute(promptText) {
    // Always return to the home view first -- the loading indicator lives
    // there. Without this, submitting from the results view (e.g. the
    // clarification reply form) would set loadingIndicator.hidden = false
    // while its container (homeView) stayed hidden, leaving the page blank.
    showHome(false);

    const prompt = String(promptText || "").trim();
    if (!prompt) {
      showError("Please enter a request before submitting.");
      return;
    }
    currentPrompt = prompt;
    promptInput.value = prompt;

    setLoading(true);
    const controller = new AbortController();
    const requestTimeout = setTimeout(() => controller.abort(), EXECUTE_TIMEOUT_MS);
    try {
      const response = await fetch("/api/execute", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Interactive-Mode": "true" },
        body: JSON.stringify({ prompt }),
        signal: controller.signal,
      });
      const sessionId = response.headers.get("X-DigitalNomadAgent-Session-Id");
      const data = await response.json();

      if (data.status === "ok") {
        saveConversation(prompt, data, sessionId);
        showResults(prompt, data.response || "", data.steps);
      } else {
        showError(data.error || "The agent could not complete this request.");
      }
    } catch (err) {
      if (err && err.name === "AbortError") {
        showError("The agent did not finish within the 300-second end-to-end limit.");
      } else {
        showError("A network error occurred while contacting the agent.");
      }
    } finally {
      clearTimeout(requestTimeout);
      setLoading(false);
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    runExecute(promptInput.value);
  });

  // Event delegation: #clarification-reply-form is injected dynamically into
  // resultsContent by renderClarificationPrompt, so it's never present at
  // setup time -- a single listener on the persistent container catches every
  // future instance via bubbling, rather than re-binding on each render.
  resultsContent.addEventListener("submit", (event) => {
    const replyForm = event.target.closest("#clarification-reply-form");
    if (!replyForm) return;
    event.preventDefault();
    const input = replyForm.querySelector("#clarification-reply-input");
    const detail = input ? input.value.trim() : "";
    if (!detail) return;
    runExecute(`${currentPrompt}\n\nAdditional details: ${detail}`);
  });

  initializeTheme();
  initializeSidebar();
  initializeSidebarWidth();
  clearLegacyHistory();
  savedSearchSessions = readBootstrappedSessions();
  renderHistory();
  if (window.location.hash === "#conversations") {
    showConversations(false);
  }
})();
