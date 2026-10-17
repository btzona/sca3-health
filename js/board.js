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

  function formatDay(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    try {
      return new Intl.DateTimeFormat("en-US", {
        timeZone: "America/Phoenix",
        month: "short",
        day: "numeric",
        year: "numeric",
      }).format(date);
    } catch {
      return iso.slice(0, 10);
    }
  }

  Promise.all([
    fetch("data/pipeline.json", { cache: "no-store" }),
    fetch("data/trials.json", { cache: "no-store" }),
    fetch("data/changes.json", { cache: "no-store" }),
    fetch("data/registry.json", { cache: "no-store" }),
  ])
    .then(async (responses) => {
      const [pipelineRes, trialsRes, changesRes, registryRes] = responses;
      if (!pipelineRes.ok || !trialsRes.ok) throw new Error("missing board data");
      const pipeline = await pipelineRes.json();
      const trials = await trialsRes.json();
      const changes = changesRes.ok ? await changesRes.json() : null;
      const registry = registryRes.ok ? await registryRes.json() : { records: {} };
      const records = registry.records || {};
      const count = Number.isFinite(trials.trial_count) ? trials.trial_count : (trials.trials || []).length;
      const closest = (pipeline.closest || [])
        .map((program) => {
          const record = program.registry_id ? records[program.registry_id] : null;
          const status = record
            ? `<p class="board-status">${escapeHtml(record.registry)}: ${escapeHtml(record.status)}</p><p class="checked">${escapeHtml(record.label)}</p>`
            : `<p class="checked">No ClinicalTrials.gov or EU CTIS record is listed for this program.</p>`;
          return `<article class="board-program">
            <p class="board-kicker">${escapeHtml(program.stage || "")}</p>
            <h3><a href="${escapeHtml(program.href || "pipeline.html")}">${escapeHtml(program.name || "")}</a></h3>
            <p>${escapeHtml(program.summary || "")}</p>
            ${status}
          </article>`;
        })
        .join("");
      const changeBits = [];
      if (changes) {
        (changes.new_trials || []).forEach((item) => {
          changeBits.push(`<li><strong>New open trial.</strong> ${escapeHtml(item.title)} (${escapeHtml(item.status)})</li>`);
        });
        (changes.status_changes || []).forEach((item) => {
          changeBits.push(`<li><strong>Status change.</strong> ${escapeHtml(item.title)}: ${escapeHtml(item.from)} to ${escapeHtml(item.to)}</li>`);
        });
        (changes.removed_trials || []).forEach((item) => {
          changeBits.push(`<li><strong>Left the open list.</strong> ${escapeHtml(item.title)} was ${escapeHtml(item.from)}.</li>`);
        });
        (changes.papers || []).forEach((item) => {
          const flag = item.new_since_refresh ? "New since the last refresh. " : "";
          changeBits.push(
            `<li>${flag}<a href="${escapeHtml(item.link)}" rel="noopener noreferrer">${escapeHtml(item.title)}</a> <span class="board-meta">${escapeHtml(item.evidence || "Other")} · ${escapeHtml(item.source || "")}</span></li>`
          );
        });
      }
      const changeHtml = changeBits.length
        ? `<ul class="change-list">${changeBits.join("")}</ul>`
        : `<p>Nothing new was recorded for this month in the latest refresh.</p>`;
      const basis = changes && changes.basis ? `<p class="board-meta">${escapeHtml(changes.basis)}</p>` : "";
      root.innerHTML = `<div class="board-grid">
        <section class="board-panel">
          <h3>Closest to patients</h3>
          <p class="board-meta">From the pipeline page. No approved SCA3 drug is listed there.</p>
          <div class="board-programs">${closest}</div>
        </section>
        <section class="board-panel">
          <h3>Open SCA3 trials</h3>
          <p class="board-count">${escapeHtml(count)}</p>
          <p>Interventional studies a registry still lists as recruiting or not yet recruiting.</p>
          <p class="board-meta">Refreshed ${escapeHtml(formatDay(trials.updated) || "recently")}.</p>
          <p><a href="trials.html">See the open trials</a></p>
        </section>
        <section class="board-panel">
          <h3>What changed in ${escapeHtml((changes && changes.label) || "this month")}</h3>
          ${changeHtml}
          ${basis}
        </section>
      </div>
      <p class="board-note">Not medical advice. Trial status comes from ClinicalTrials.gov or the EU CTIS public record, not from a search snippet.</p>`;
    })
    .catch(() => {
      root.innerHTML = `<p class="board-note">The status board could not be loaded. The paper list below is unchanged.</p>`;
    });
})();
