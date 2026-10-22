(() => {
  "use strict";

  const root = document.getElementById("board-root");
  if (!root) return;

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function statusSentence(status) {
    if (window.AtaxinVoice) return AtaxinVoice.statusSentence(status);
    return status || "";
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
    for (const item of (payload && payload.items) || []) {
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

  function summaryFor(item, index) {
    const doi = doiFrom(item.link) || doiFrom(item.id);
    if (doi && index.byId.has(doi)) return index.byId.get(doi);
    const id = String(item.id || "").trim().toLowerCase();
    if (id && index.byId.has(id)) return index.byId.get(id);
    return index.byLink.get(normalizeLink(item.link)) || "";
  }

  Promise.all([
    fetch("data/pipeline.json", { cache: "no-store" }),
    fetch("data/trials.json", { cache: "no-store" }),
    fetch("data/changes.json", { cache: "no-store" }),
    fetch("data/registry.json", { cache: "no-store" }),
    fetch("data/summaries.json", { cache: "no-store" }).catch(() => null),
  ])
    .then(async (responses) => {
      const [pipelineRes, trialsRes, changesRes, registryRes, summaryRes] = responses;
      if (!pipelineRes.ok || !trialsRes.ok) throw new Error("missing board data");
      const pipeline = await pipelineRes.json();
      const trials = await trialsRes.json();
      const changes = changesRes.ok ? await changesRes.json() : null;
      const registry = registryRes.ok ? await registryRes.json() : { records: {} };
      const summaryIndex = summaryRes && summaryRes.ok ? indexSummaries(await summaryRes.json()) : { byId: new Map(), byLink: new Map() };
      const records = registry.records || {};
      const count = Number.isFinite(trials.trial_count) ? trials.trial_count : (trials.trials || []).length;
      const closest = (pipeline.programs || [])
        .filter((program) => program.closest)
        .map((program) => {
          const record = program.registry_id ? records[program.registry_id] : null;
          const spoken = record ? `<p>${escapeHtml(statusSentence(record.status))}</p><p class="checked">${escapeHtml(record.label)}</p>` : "";
          return `<article class="board-program">
            <h3><a href="${escapeHtml(program.href || "pipeline.html")}">${escapeHtml(program.name || "")}</a></h3>
            <p>${escapeHtml(program.plain || "")}</p>
            ${spoken}
          </article>`;
        })
        .join("");
      const changeBits = [];
      if (changes) {
        (changes.new_trials || []).forEach((item) => {
          changeBits.push(`<li><p>A study was added. ${escapeHtml(statusSentence(item.status))}</p><p class="board-title">${escapeHtml(item.title)}</p></li>`);
        });
        (changes.status_changes || []).forEach((item) => {
          const spoken = statusSentence(item.to) || item.to || "";
          changeBits.push(`<li><p>A study's listing changed. ${escapeHtml(spoken)}</p><p class="board-title">${escapeHtml(item.title)}</p></li>`);
        });
        (changes.removed_trials || []).forEach((item) => {
          changeBits.push(`<li><p>A study is no longer listed as open.</p><p class="board-title">${escapeHtml(item.title)}</p></li>`);
        });
        (changes.papers || []).forEach((item) => {
          const meaning = summaryFor(item, summaryIndex);
          const line = meaning ? `<p>${escapeHtml(meaning)}</p>` : "";
          changeBits.push(
            `<li>${line}<p class="board-title"><a href="${escapeHtml(item.link)}" rel="noopener noreferrer">${escapeHtml(item.title)}</a></p></li>`
          );
        });
      }
      const changeHtml = changeBits.length
        ? `<ul class="change-list">${changeBits.join("")}</ul>`
        : `<p>Nothing new turned up for this month.</p>`;
      const month = (changes && changes.label) || "this month";
      root.innerHTML = `<div class="board-grid">
        <section class="board-panel">
          <h3>Furthest along</h3>
          <p>None is approved yet.</p>
          <div class="board-programs">${closest}</div>
        </section>
        <section class="board-panel">
          <h3>Trials you could ask about</h3>
          <p class="board-lead">${escapeHtml(count)} ${count === 1 ? "study is" : "studies are"} enrolling or about to start.</p>
          <p><a href="trials.html">See them, and who can join</a></p>
        </section>
        <section class="board-panel">
          <h3>New this month</h3>
          <p class="board-kicker">${escapeHtml(month)}</p>
          ${changeHtml}
        </section>
      </div>`;
    })
    .catch(() => {
      root.innerHTML = `<p>The summary could not be loaded.</p>`;
    });
})();
