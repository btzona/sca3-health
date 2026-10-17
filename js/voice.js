(() => {
  "use strict";

  function statusSentence(status) {
    const text = String(status || "").trim();
    const low = text.toLowerCase();
    if (!low) return "";
    if (low === "recruiting") return "The study record says it is enrolling now.";
    if (low.includes("not yet")) return "The study record says it has not started enrolling yet.";
    if (low.includes("active") && low.includes("not")) return "The study record says it is still going, and is not taking new people.";
    if (low.startsWith("terminated")) return "The study record says it was stopped.";
    const ended = text.match(/end of trial ([0-9]{1,2} [A-Za-z]{3} [0-9]{4})/i);
    if (low.startsWith("ended") && ended) return `The European record says this study ended on ${ended[1]}.`;
    if (low.startsWith("ended")) return "The European record says this study has ended.";
    return `The study record says: ${text}.`;
  }

  function pillLabel(statusKey, status) {
    if (statusKey === "recruiting" || String(status || "").toLowerCase() === "recruiting") return "Enrolling now";
    if (statusKey === "not_yet" || String(status || "").toLowerCase().includes("not yet")) return "Not started yet";
    return statusSentence(status).replace(/\.$/, "") || status || "";
  }

  window.AtaxinVoice = { statusSentence, pillLabel };
})();
