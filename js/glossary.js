(() => {
  "use strict";

  function skip(node) {
    const parent = node.parentElement;
    if (!parent) return true;
    return Boolean(parent.closest("a, button, script, style, code, textarea, .term, .nav, .share, h1"));
  }

  function linkTerms(root, terms) {
    const used = new Set();
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(node) {
        if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        if (skip(node)) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      },
    });
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);

    nodes.forEach((node) => {
      let guard = 0;
      while (node.parentNode && guard < 12) {
        guard += 1;
        const text = node.nodeValue || "";
        let best = null;
        terms.forEach((term) => {
          if (used.has(term.id)) return;
          term.re.lastIndex = 0;
          const match = term.re.exec(text);
          if (!match) return;
          if (!best || match.index < best.index) {
            best = { term, index: match.index, length: match[0].length, word: match[0] };
          }
        });
        if (!best) break;
        used.add(best.term.id);
        const after = text.slice(best.index + best.length);
        const link = document.createElement("a");
        link.className = "term";
        link.href = `glossary.html#${best.term.id}`;
        link.append(document.createTextNode(best.word));
        const tip = document.createElement("span");
        tip.className = "tip";
        tip.setAttribute("aria-hidden", "true");
        tip.textContent = best.term.tip;
        link.append(tip);
        const parent = node.parentNode;
        parent.insertBefore(document.createTextNode(text.slice(0, best.index)), node);
        parent.insertBefore(link, node);
        node.nodeValue = after;
        if (!after) break;
      }
    });
  }

  let terms = null;
  const queued = [];

  function link(root) {
    if (!root) return;
    if (!terms) {
      queued.push(root);
      return;
    }
    linkTerms(root, terms);
  }

  window.AtaxinGlossary = { link };

  fetch("data/glossary.json", { cache: "no-store" })
    .then((res) => (res.ok ? res.json() : null))
    .then((data) => {
      if (!data) return;
      terms = (data.terms || [])
        .map((term) => ({
          id: term.id,
          tip: term.tip,
          pattern: term.pattern || "",
          re: new RegExp(term.pattern, "i"),
        }))
        .sort((a, b) => b.pattern.length - a.pattern.length);
      document.querySelectorAll("[data-glossary]").forEach((root) => linkTerms(root, terms));
      queued.splice(0).forEach((root) => {
        if (root.closest("[data-glossary]")) return;
        linkTerms(root, terms);
      });
    })
    .catch(() => {});
})();
