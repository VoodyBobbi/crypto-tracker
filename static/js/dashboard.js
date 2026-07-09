(function () {
  "use strict";

  var REFRESH_MS = 30000;
  var body = document.getElementById("coins-body");
  var statusDot = document.getElementById("status-dot");
  var statusText = document.getElementById("status-text");
  var refreshBtn = document.getElementById("refresh-btn");
  var timer = null;

  function fmtPrice(v) {
    if (v === null || v === undefined) return "—";
    var opts = v >= 1
      ? { minimumFractionDigits: 2, maximumFractionDigits: 2 }
      : { minimumFractionDigits: 2, maximumFractionDigits: 6 };
    return "$" + Number(v).toLocaleString("en-US", opts);
  }

  function fmtCap(v) {
    if (v === null || v === undefined) return "—";
    return "$" + Number(v).toLocaleString("en-US", { maximumFractionDigits: 0 });
  }

  function fmtChange(v) {
    if (v === null || v === undefined) return { text: "—", cls: "" };
    var cls = v >= 0 ? "up" : "down";
    var sign = v >= 0 ? "+" : "";
    return { text: sign + v.toFixed(2) + "%", cls: cls };
  }

  function setStatus(state, text) {
    statusDot.className = "dot" + (state ? " " + state : "");
    statusText.textContent = text;
  }

  function render(coins) {
    var rows = coins.map(function (c) {
      var ch = fmtChange(c.change_24h);
      return (
        '<tr>' +
        '<td class="col-rank">' + (c.rank || "") + "</td>" +
        '<td class="col-coin">' +
        '<div class="coin-cell">' +
        (c.image ? '<img src="' + c.image + '" alt="' + c.name + '" loading="lazy" />' : "") +
        '<div><div class="coin-name">' + c.name + "</div>" +
        '<div class="coin-symbol">' + c.symbol + "</div></div>" +
        "</div></td>" +
        '<td class="col-num" data-label="Price">' + fmtPrice(c.price) + "</td>" +
        '<td class="col-num" data-label="24h"><span class="change ' + ch.cls + '">' + ch.text + "</span></td>" +
        '<td class="col-num" data-label="Market Cap">' + fmtCap(c.market_cap) + "</td>" +
        "</tr>"
      );
    });
    body.innerHTML = rows.join("");
  }

  function load() {
    setStatus("", "Updating…");
    fetch("/api/coins", { headers: { Accept: "application/json" } })
      .then(function (r) {
        if (r.status === 401) {
          window.location.href = "/login";
          throw new Error("unauthorized");
        }
        return r.json();
      })
      .then(function (data) {
        if (data.error) throw new Error(data.error);
        render(data.coins);
        var t = new Date((data.updated_at || Date.now() / 1000) * 1000);
        setStatus("live", "Live · updated " + t.toLocaleTimeString());
      })
      .catch(function (err) {
        if (err && err.message === "unauthorized") return;
        setStatus("error", "Failed to load data — will retry");
        if (!body.querySelector("tr td.col-coin")) {
          body.innerHTML = '<tr><td colspan="5" class="table-error">Could not load market data. Retrying…</td></tr>';
        }
      });
  }

  function schedule() {
    if (timer) clearInterval(timer);
    timer = setInterval(load, REFRESH_MS);
  }

  refreshBtn.addEventListener("click", function () {
    load();
    schedule();
  });

  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) load();
  });

  load();
  schedule();
})();
