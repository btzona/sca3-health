(() => {
  "use strict";

  const root = document.getElementById("trials");
  const statusEl = document.getElementById("trial-status");
  if (!root) return;

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
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

  function meansHtml(links, summaries) {
    for (const link of links) {
      const item = summaries.get(normalizeLink(link.url));
      if (item) {
        return `<p class="excerpt">${escapeHtml(item.summary)}</p>`;
      }
    }
    return "";
  }

  function card(trial, summaries) {
    const links = trial.links || [];
    const primary = links[0] || { url: "", label: "Registry" };
    const share =
      primary.url && window.AtaxinShare
        ? AtaxinShare.html(primary.url, trial.title || "Trial", { groupLabel: "Share this trial" })
        : "";
    const countries = (trial.countries || []).map((name) => `<span class="pill">${escapeHtml(name)}</span>`).join("");
    const sites = (trial.sites || [])
      .map((site) => {
        const place = [site.facility, site.city, site.country].filter(Boolean).join(", ");
        return `<li>${escapeHtml(place)}</li>`;
      })
      .join("");
    const linkHtml = links
      .map(
        (link) =>
          `<a class="outlink" href="${escapeHtml(link.url)}" rel="noopener noreferrer">${escapeHtml(link.label)}</a>`
      )
      .join("");
    const eligibility = trial.eligibility
      ? `<details class="criteria"><summary>Who the study says can take part</summary><p>${escapeHtml(trial.eligibility)}</p></details>`
      : "";
    return `<article class="card trial-card">
      <div class="trial-top">
        <span class="pill ${escapeHtml(trial.status_key || "")}">${escapeHtml(window.AtaxinVoice ? AtaxinVoice.pillLabel(trial.status_key, trial.status) : trial.status || "")}</span>
        <span class="source">${escapeHtml(trial.phase || "")}</span>
      </div>
      <h3><a href="${escapeHtml(primary.url)}" rel="noopener noreferrer">${escapeHtml(trial.title || "")}</a></h3>
      <p class="excerpt">${escapeHtml(trial.summary || "")}</p>
      ${meansHtml(links, summaries)}
      <dl class="fact-grid">
        <div><dt>Age</dt><dd>${escapeHtml(trial.ages || "See the study page")}</dd></div>
        <div><dt>Who it’s for</dt><dd>${escapeHtml(trial.population || "See the study page")}</dd></div>
        <div><dt>Who is running it</dt><dd>${escapeHtml(trial.sponsor || "See the study page")}</dd></div>
        <div><dt>Study number</dt><dd>${escapeHtml(trial.nct_id || trial.eu_ct || trial.id || "")}</dd></div>
      </dl>
      <p class="meta-line">${countries || '<span class="pill">Sites not listed</span>'} <span class="pill population">${escapeHtml(trial.study_type || "Interventional")}</span></p>
      ${sites ? `<ul class="sites">${sites}</ul>` : ""}
      ${eligibility}
      <div class="trial-links">${linkHtml}</div>
      ${trial.checked_label ? `<p class="checked">${escapeHtml(trial.checked_label)}</p>` : ""}
      ${share}
    </article>`;
  }

  Promise.all([
    fetch("data/trials.json", { cache: "no-store" }),
    fetch("data/summaries.json", { cache: "no-store" }).catch(() => null),
  ])
    .then(async ([trialRes, summaryRes]) => {
      if (!trialRes.ok) throw new Error(`HTTP ${trialRes.status}`);
      const data = await trialRes.json();
      const summaries = new Map();
      if (summaryRes && summaryRes.ok) {
        const packed = await summaryRes.json();
        (packed.items || []).forEach((item) => {
          if (item.link && item.summary) summaries.set(normalizeLink(item.link), item);
        });
      }
      const trials = data.trials || [];
      const when = data.updated ? new Date(data.updated) : null;
      const stamp = when && !Number.isNaN(when.getTime())
        ? when.toLocaleString("en-US", { timeZone: "America/Phoenix", dateStyle: "medium", timeStyle: "short" })
        : "the last successful refresh";
      if (statusEl) {
        statusEl.textContent = `${trials.length} ${trials.length === 1 ? "study is" : "studies are"} enrolling or about to start. Refreshed ${stamp}.`;
      }
      root.innerHTML = trials.length
        ? trials.map((trial) => card(trial, summaries)).join("")
        : '<div class="empty">No open studies were in the last refresh.</div>';
      if (window.AtaxinGlossary) AtaxinGlossary.link(root);
    })
    .catch(() => {
      if (statusEl) statusEl.textContent = "The trials file could not be loaded.";
      root.innerHTML = '<div class="empty" role="alert">data/trials.json is missing. The daily refresh writes it.</div>';
    });
})();
