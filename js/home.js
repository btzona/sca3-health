(() => {
  "use strict";

  const root = document.getElementById("new-root");
  if (!root) return;

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatDay(iso) {
    if (!iso) return "";
    const raw = String(iso);
    const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(raw);
    const date = new Date(dateOnly ? `${raw}T12:00:00Z` : raw);
    if (Number.isNaN(date.getTime())) return "";
    try {
      return new Intl.DateTimeFormat("en-US", {
        timeZone: dateOnly ? "UTC" : "America/Phoenix",
        month: "long",
        day: "numeric",
        year: "numeric",
      }).format(date);
    } catch {
      return raw.slice(0, 10);
    }
  }

  function doiFrom(text) {
    const match = String(text || "").match(/10\.\d{4,9}\/[^\s"'<>]+/i);
    if (!match) return "";
    return match[0].replace(/[.,);]+$/, "").toLowerCase();
  }

  function normalizeLink(url) {
    try {
      const parsed = new URL(url);
      parsed.hash = "";
      return `${parsed.hostname.replace(/^www\./, "")}${parsed.pathname.replace(/\/$/, "")}`.toLowerCase();
    } catch {
      return String(url || "").trim().toLowerCase();
    }
  }

  function indexSummaries(payload) {
    const byId = new Map();
    const byLink = new Map();
    for (const item of payload.items || []) {
      const text = String(item.summary || "").trim();
      if (!text) continue;
      const id = String(item.id || "").trim().toLowerCase();
      const doi = doiFrom(item.link) || doiFrom(id);
      if (id) byId.set(id, text);
      if (doi) byId.set(doi, text);
      if (item.link) byLink.set(normalizeLink(item.link), text);
    }
    return { byId, byLink };
  }

  function summaryFor(article, index) {
    const doi = doiFrom(article.link) || doiFrom(article.id);
    if (doi && index.byId.has(doi)) return index.byId.get(doi);
    const id = String(article.id || "").trim().toLowerCase();
    if (id && index.byId.has(id)) return index.byId.get(id);
    return index.byLink.get(normalizeLink(article.link)) || "";
  }

  Promise.all([
    fetch("data/articles.json", { cache: "no-store" }),
    fetch("data/summaries.json", { cache: "no-store" }).catch(() => null),
  ])
    .then(async ([articlesRes, summaryRes]) => {
      if (!articlesRes.ok) throw new Error("missing articles");
      const payload = await articlesRes.json();
      const index =
        summaryRes && summaryRes.ok ? indexSummaries(await summaryRes.json()) : { byId: new Map(), byLink: new Map() };
      const latest = (payload.articles || [])
        .filter((article) => article && article.title && article.link)
        .slice()
        .sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")))
        .slice(0, 4);
      if (!latest.length) {
        root.innerHTML = `<p>Nothing new is listed yet. <a href="research.html">The research page</a> is still there.</p>`;
        return;
      }
      root.innerHTML = `<ol class="new-list">${latest
        .map((article) => {
          const when = formatDay(article.date);
          const source = [article.source, when].filter(Boolean).join(" · ");
          const summary = summaryFor(article, index);
          const line = summary ? `<p class="new-line">${escapeHtml(summary)}</p>` : "";
          return `<li>
            ${line}
            <p class="new-source"><a href="${escapeHtml(article.link)}" rel="noopener noreferrer">${escapeHtml(article.title)}</a>${source ? ` <span>${escapeHtml(source)}</span>` : ""}</p>
          </li>`;
        })
        .join("")}</ol>`;
      if (window.AtaxinGlossary) AtaxinGlossary.link(root);
    })
    .catch(() => {
      root.innerHTML = `<p>The latest items could not be loaded. <a href="research.html">The research page</a> is still there.</p>`;
    });
})();
