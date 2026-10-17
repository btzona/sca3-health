(() => {
  "use strict";

  const svgAttrs =
    'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"';

  const icons = {
    x: `<svg ${svgAttrs}><path d="M6 6l12 12M18 6 6 18"/></svg>`,
    reddit: `<svg ${svgAttrs}><circle cx="12" cy="13.2" r="6.1"/><path d="M15.5 8.5 16.8 5.8"/><circle cx="17.6" cy="5.1" r="1" fill="currentColor" stroke="none"/><circle cx="9.6" cy="12.5" r="0.7" fill="currentColor" stroke="none"/><circle cx="14.4" cy="12.5" r="0.7" fill="currentColor" stroke="none"/><path d="M9.8 15.3c.7.55 1.4.85 2.2.85s1.5-.3 2.2-.85"/></svg>`,
    facebook: `<svg ${svgAttrs}><path d="M14.2 7.4h-1.8c-.7 0-1.1.4-1.1 1.1V11H9.6v2.1h1.7V19h2.2v-5.9h1.7l.4-2.1h-2.1V8.7c0-.2.15-.3.4-.3h1.5"/></svg>`,
    email: `<svg ${svgAttrs}><rect x="3.5" y="5.5" width="17" height="13" rx="1.5"/><path d="m4 7.5 8 6 8-6"/></svg>`,
    copy: `<svg ${svgAttrs}><rect x="9" y="9" width="11" height="11" rx="1.5"/><path d="M5.5 15.5v-9A1.5 1.5 0 0 1 7 5h8"/></svg>`,
    check: `<svg ${svgAttrs}><path d="M5 12.5 9.2 16.5 19 7.5"/></svg>`,
  };

  function escapeAttr(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/"/g, "&quot;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function hrefs(url, title) {
    const u = encodeURIComponent(url);
    const t = encodeURIComponent(title);
    const body = encodeURIComponent(`${title}\n\n${url}`);
    return {
      x: `https://twitter.com/intent/tweet?url=${u}&text=${t}`,
      reddit: `https://www.reddit.com/submit?url=${u}&title=${t}`,
      facebook: `https://www.facebook.com/sharer/sharer.php?u=${u}`,
      email: `mailto:?subject=${t}&body=${body}`,
    };
  }

  function link(name, href, label) {
    const external = href.startsWith("mailto:")
      ? ""
      : ' target="_blank" rel="noopener noreferrer"';
    return `<a class="share-btn" href="${escapeAttr(href)}"${external} aria-label="${escapeAttr(label)}">${icons[name]}</a>`;
  }

  function html(url, title, options) {
    const opts = options || {};
    const label = opts.label || "Share";
    const groupLabel = opts.groupLabel || label;
    const safeTitle = title || "Ataxin-3";
    const links = hrefs(url, safeTitle);
    return `<div class="share" role="group" aria-label="${escapeAttr(groupLabel)}">
      <span class="share-label">${escapeAttr(label)}</span>
      ${link("x", links.x, "Share on X")}
      ${link("reddit", links.reddit, "Share on Reddit")}
      ${link("facebook", links.facebook, "Share on Facebook")}
      ${link("email", links.email, "Share by email")}
      <button type="button" class="share-btn" data-copy-url="${escapeAttr(url)}" aria-label="Copy link"><span class="icon-copy">${icons.copy}</span><span class="icon-check">${icons.check}</span></button>
    </div>`;
  }

  function mountAll() {
    document.querySelectorAll("[data-share-url]").forEach((el) => {
      if (el.dataset.shareReady === "true") return;
      el.dataset.shareReady = "true";
      el.innerHTML = html(el.dataset.shareUrl, el.dataset.shareTitle || document.title, {
        label: "Share",
        groupLabel: el.dataset.shareLabel || "Share this page",
      });
    });
  }

  function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      return navigator.clipboard.writeText(text);
    }
    return new Promise((resolve, reject) => {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.top = "0";
      area.style.left = "0";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.focus();
      area.select();
      try {
        const ok = document.execCommand("copy");
        area.remove();
        if (ok) resolve();
        else reject(new Error("copy failed"));
      } catch (err) {
        area.remove();
        reject(err);
      }
    });
  }

  function liveRegion() {
    let live = document.getElementById("share-live");
    if (!live) {
      live = document.createElement("div");
      live.id = "share-live";
      live.className = "visually-hidden";
      live.setAttribute("aria-live", "polite");
      document.body.appendChild(live);
    }
    return live;
  }

  const copyTimers = new WeakMap();

  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-copy-url]");
    if (!button) return;
    const url = button.getAttribute("data-copy-url");
    if (!url) return;
    copyText(url)
      .then(() => {
        button.dataset.copied = "true";
        button.setAttribute("aria-label", "Link copied");
        const live = liveRegion();
        live.textContent = "";
        live.textContent = "Link copied";
        const previous = copyTimers.get(button);
        if (previous) window.clearTimeout(previous);
        copyTimers.set(
          button,
          window.setTimeout(() => {
            button.dataset.copied = "false";
            button.setAttribute("aria-label", "Copy link");
          }, 2000)
        );
      })
      .catch(() => {
        button.dataset.copied = "false";
        button.setAttribute("aria-label", "Could not copy link");
        const live = liveRegion();
        live.textContent = "";
        live.textContent = "Could not copy link";
      });
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mountAll);
  } else {
    mountAll();
  }

  window.AtaxinShare = { html, hrefs };
})();
