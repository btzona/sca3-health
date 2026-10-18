(function () {
  var boxes = document.querySelectorAll("[data-quote]");
  if (!boxes.length) return;

  function monthName(month) {
    return ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"][month - 1];
  }

  function formatDay(iso) {
    var parts = String(iso || "").split("-");
    if (parts.length !== 3) return iso || "";
    return Number(parts[2]) + " " + monthName(Number(parts[1])) + " " + parts[0];
  }

  function formatPrice(value, currency) {
    var number = Number(value);
    if (!isFinite(number)) return "";
    if (currency === "JPY") {
      return number.toLocaleString("en-US", { maximumFractionDigits: 1 }) + " JPY";
    }
    return number.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " " + (currency || "USD");
  }

  function render(box, quote) {
    var slot = box.querySelector("[data-quote-body]");
    if (!slot) return;
    if (!quote || quote.close == null || quote.as_of == null) {
      slot.textContent = "The latest snapshot does not include a last close for this ticker.";
      return;
    }
    var change = Number(quote.change_percent);
    var direction = change > 0 ? "up" : change < 0 ? "down" : "flat";
    var signed = isFinite(change)
      ? (change > 0 ? "+" : change < 0 ? "\u2212" : "") + Math.abs(change).toFixed(2) + "%"
      : "";
    slot.innerHTML = "";
    var price = document.createElement("p");
    price.className = "stock-figure";
    price.textContent = formatPrice(quote.close, quote.currency);
    var delta = document.createElement("p");
    delta.className = "stock-change " + direction;
    delta.textContent = signed ? "Daily change " + signed : "Daily change not available";
    var asOf = document.createElement("p");
    asOf.className = "stock-note";
    asOf.textContent = "Last close as of " + formatDay(quote.as_of) + ". Delayed.";
    slot.appendChild(price);
    slot.appendChild(delta);
    slot.appendChild(asOf);
  }

  function fail() {
    boxes.forEach(function (box) {
      var slot = box.querySelector("[data-quote-body]");
      if (slot) slot.textContent = "The delayed last close could not be loaded.";
    });
  }

  fetch("data/stocks.json", { headers: { Accept: "application/json" } })
    .then(function (response) {
      if (!response.ok) throw new Error("stocks");
      return response.json();
    })
    .then(function (data) {
      var quotes = data.quotes || {};
      boxes.forEach(function (box) {
        render(box, quotes[box.getAttribute("data-quote")]);
      });
    })
    .catch(fail);
})();
