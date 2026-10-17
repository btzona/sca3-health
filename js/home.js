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
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    try {
      return new Intl.DateTimeFormat("en-US", {
        timeZone: "America/Phoenix",
        month: "long",
        day: "numeric",
        year: "numeric",
      }).format(date);
    } catch {
      return String(iso).slice(0, 10);
    }
  }

  function plainSentence(article) {
    if (article.family_line) return article.family_line;
    const tags = article.tags || [];
    const evidence = article.evidence || "";
    if (evidence === "News") return "A news story about SCA3 was added.";
    if (tags.includes("Clinical trial") || tags.includes("Therapy/ASO") || tags.includes("Gene therapy")) {
      return "A paper about a treatment study was added. A study is not an approval, and it does not mean the treatment works.";
    }
    if (tags.includes("Biomarkers") || tags.includes("Natural history")) {
      return "A new paper looks at how the disease shows up, or how to measure it. It is not a treatment.";
    }
    if (tags.includes("Care/Rehab")) {
      return "A new paper looks at movement or day-to-day life. It is not a treatment.";
    }
    if (tags.includes("Genetics/Reproduction")) {
      return "A new paper looks at the gene or at family planning. It is not a treatment you can ask for.";
    }
    return "A new research paper was added. It does not, by itself, say a treatment works.";
  }

  fetch("data/articles.json", { cache: "no-store" })
    .then((response) => {
      if (!response.ok) throw new Error("missing articles");
      return response.json();
    })
    .then((payload) => {
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
          return `<li>
            <p class="new-line">${escapeHtml(plainSentence(article))}</p>
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
