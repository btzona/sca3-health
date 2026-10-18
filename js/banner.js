(() => {
  "use strict";

  const banner = document.getElementById("hope-banner");
  if (!banner) return;

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
    const day = raw.slice(0, 10);
    // A date with no time is a calendar day. Parsing it as UTC midnight
    // would show the previous evening in Arizona.
    if (/^\d{4}-\d{2}-\d{2}$/.test(day) && raw.length === 10) {
      const [year, month, date] = day.split("-").map(Number);
      return new Intl.DateTimeFormat("en-US", {
        timeZone: "UTC",
        month: "long",
        day: "numeric",
        year: "numeric",
      }).format(new Date(Date.UTC(year, month - 1, date)));
    }
    const date = new Date(raw);
    if (Number.isNaN(date.getTime())) return "";
    try {
      return new Intl.DateTimeFormat("en-US", {
        timeZone: "America/Phoenix",
        month: "long",
        day: "numeric",
        year: "numeric",
      }).format(date);
    } catch {
      return day;
    }
  }

  function approvalFields(program) {
    const approval = program && program.approval;
    if (!program || program.approved !== true || !approval) return null;
    const name = String(approval.name || program.name || "").trim();
    const country = String(approval.country || "").trim();
    const date = String(approval.date || "").trim();
    const source = String(approval.source || "").trim();
    if (!name || !country || !date || !/^https?:\/\//.test(source)) return null;
    return { name, country, date, source };
  }

  Promise.all([
    fetch("data/pipeline.json", { cache: "no-store" }),
    fetch("data/trials.json", { cache: "no-store" }).catch(() => null),
    fetch("data/articles.json", { cache: "no-store" }).catch(() => null),
  ])
    .then(async ([pipelineRes, trialsRes, articlesRes]) => {
      if (!pipelineRes.ok) return;
      const pipeline = await pipelineRes.json();
      const trials = trialsRes && trialsRes.ok ? await trialsRes.json() : {};
      const articles = articlesRes && articlesRes.ok ? await articlesRes.json() : {};
      const stamps = [trials.updated, articles.updated].filter(Boolean).sort();
      const refreshed = formatDay(stamps[stamps.length - 1]);
      const approvals = (pipeline.programs || []).map(approvalFields).filter(Boolean);
      if (!approvals.length) {
        const when = banner.querySelector("#hope-date");
        if (when && refreshed) {
          when.dateTime = stamps[stamps.length - 1].slice(0, 10);
          when.textContent = refreshed;
        }
        return;
      }
      const blocks = approvals
        .map(
          (item) => `<p class="hope-approval"><a href="${escapeHtml(item.source)}" rel="noopener noreferrer">${escapeHtml(item.name)}</a> was approved in ${escapeHtml(item.country)} on ${escapeHtml(formatDay(item.date) || item.date)}.</p>`
        )
        .join("");
      banner.innerHTML = `<div class="wrap">
        <h2>${approvals.length === 1 ? "An approval is now on the record." : "Approvals are now on the record."}</h2>
        ${blocks}
        <p>Read the source. An approval in one country is not automatically an approval everywhere.</p>
        <p class="hope-link"><a href="pipeline.html">What's being tested now</a></p>
      </div>`;
    })
    .catch(() => {});
})();
