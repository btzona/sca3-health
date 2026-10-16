(() => {
  "use strict";

  const grid = document.getElementById("articles");
  const statusEl = document.getElementById("status");
  const updatedEl = document.getElementById("updated");
  const countEl = document.getElementById("count");
  const searchInput = document.getElementById("search");
  const filterBtns = Array.from(document.querySelectorAll("[data-filter]"));

  const pageSize = 12;

  let data = null;
  let filter = "all";
  let query = "";
  let visibleCount = pageSize;

  const monthNames = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
  ];

  function escapeHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function italicizeGenes(text) {
    return escapeHtml(text || "").replace(/\b(ATXN3)\b/g, "<em>$1</em>");
  }

  function formatDate(iso) {
    if (!iso) return "Date unknown";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "Date unknown";
    try {
      return new Intl.DateTimeFormat("en-US", {
        timeZone: "America/Phoenix",
        year: "numeric",
        month: "short",
        day: "numeric",
      }).format(d);
    } catch {
      return `${monthNames[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`;
    }
  }

  function formatUpdated(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    try {
      return new Intl.DateTimeFormat("en-US", {
        timeZone: "America/Phoenix",
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
        timeZoneName: "short",
      }).format(d);
    } catch {
      return d.toISOString();
    }
  }

  function phoenixYearMonth(iso) {
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "unknown";
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "America/Phoenix",
      year: "numeric",
      month: "2-digit",
    }).formatToParts(d);
    const year = parts.find((part) => part.type === "year");
    const month = parts.find((part) => part.type === "month");
    if (!year || !month) return "unknown";
    return `${year.value}-${month.value}`;
  }

  function monthLabel(key) {
    if (key === "unknown") return "Date unknown";
    const [year, month] = key.split("-");
    const index = Number(month) - 1;
    if (!year || index < 0 || index > 11) return "Date unknown";
    return `${monthNames[index]} ${year}`;
  }

  function setStatus(msg, state) {
    statusEl.textContent = msg;
    if (state) statusEl.dataset.state = state;
    else delete statusEl.dataset.state;
  }

  function matches(article) {
    const tags = article.tags || [];
    if (filter !== "all" && !tags.includes(filter)) return false;
    if (!query) return true;
    const blob = [article.title, article.summary, article.source, tags.join(" ")]
      .join(" ")
      .toLowerCase();
    return blob.includes(query);
  }

  function cardHtml(article) {
    const tags = article.tags || [];
    const primary = tags[0] || "Research";
    const rest = tags.slice(1);
    const extra = rest.length
      ? `<p class="tag-row">${rest
          .map((tag) => `<span class="badge muted">${escapeHtml(tag)}</span>`)
          .join("")}</p>`
      : "";
    return `
<article class="card">
  <div class="card-top">
    <span class="badge">${escapeHtml(primary)}</span>
    <span class="source">${escapeHtml(article.source || "")}</span>
  </div>
  <h3><a href="${escapeHtml(article.link)}" rel="noopener noreferrer">${italicizeGenes(article.title)}</a></h3>
  <p class="excerpt">${italicizeGenes(article.summary)}</p>
  ${extra}
  <div class="card-foot">
    <time class="date" datetime="${escapeHtml(article.date || "")}">${formatDate(article.date)}</time>
    <a class="outlink" href="${escapeHtml(article.link)}" rel="noopener noreferrer">Original</a>
  </div>
</article>`;
  }

  function render() {
    if (!data) return;
    const items = (data.articles || []).filter(matches);
    countEl.textContent = String(items.length);

    if (!items.length) {
      grid.innerHTML =
        '<div class="empty" role="status">Nothing matches this filter. Try All, or clear the search.</div>';
      return;
    }

    const shown = items.slice(0, visibleCount);
    const groups = [];
    for (const article of shown) {
      const key = article.date ? phoenixYearMonth(article.date) : "unknown";
      const last = groups[groups.length - 1];
      if (!last || last.key !== key) groups.push({ key, items: [article] });
      else last.items.push(article);
    }

    const more =
      items.length > shown.length
        ? `<div class="more-wrap"><button type="button" class="more" id="load-more">Show earlier entries</button><p class="more-note">${shown.length} of ${items.length}</p></div>`
        : "";

    grid.innerHTML =
      groups
        .map(
          (group) => `<section class="month">
  <h2 class="month-label">${monthLabel(group.key)}</h2>
  <div class="grid">${group.items.map(cardHtml).join("")}</div>
</section>`
        )
        .join("") + more;

    const moreBtn = document.getElementById("load-more");
    if (moreBtn) {
      moreBtn.addEventListener("click", () => {
        visibleCount += pageSize;
        render();
        const next = document.getElementById("load-more");
        if (next) next.focus();
      });
    }
  }

  async function load() {
    setStatus("Loading ledger…");
    try {
      const res = await fetch("data/articles.json", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
      updatedEl.textContent = formatUpdated(data.updated);
      const dated = (data.articles || [])
        .map((article) => article.date)
        .filter(Boolean)
        .sort();
      const span = dated.length
        ? `${formatDate(dated[0])} – ${formatDate(dated[dated.length - 1])}`
        : "dates unrecorded";
      setStatus(`${data.article_count} items · ${span}`);
      render();
    } catch (err) {
      console.error(err);
      setStatus("Could not load data/articles.json. Run python3 fetch.py, then reload.", "error");
      grid.innerHTML =
        '<div class="empty" role="alert">The ledger file is missing. Refresh it with <code>python3 fetch.py</code>.</div>';
    }
  }

  filterBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      filter = btn.dataset.filter;
      visibleCount = pageSize;
      filterBtns.forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
      render();
    });
  });

  let timer = null;
  searchInput.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => {
      query = searchInput.value.trim().toLowerCase();
      visibleCount = pageSize;
      render();
    }, 120);
  });

  load();
})();
