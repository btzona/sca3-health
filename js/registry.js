(() => {
  "use strict";

  const marks = document.querySelectorAll("[data-checked], [data-status-for]");
  if (!marks.length) return;

  fetch("data/registry.json", { cache: "no-store" })
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then((payload) => {
      const records = payload.records || {};
      document.querySelectorAll("[data-status-for]").forEach((node) => {
        const record = records[node.dataset.statusFor];
        if (!record) return;
        node.textContent = `${record.registry}: ${record.status}`;
      });
      document.querySelectorAll("[data-checked]").forEach((node) => {
        const record = records[node.dataset.checked];
        node.textContent = record
          ? record.label
          : "The latest refresh did not include this registry record.";
      });
    })
    .catch(() => {
      document.querySelectorAll("[data-checked]").forEach((node) => {
        if (!node.textContent.trim()) node.textContent = "The registry check could not be loaded.";
      });
    });
})();
