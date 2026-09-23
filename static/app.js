(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var COLORS = ["#ffd23f", "#ff7aa8", "#4be0a8", "#6cc4ff", "#c9a7ff", "#ffa94d"];
  var STEP_COLORS = ["#4be0a8", "#ffd23f", "#ffa94d", "#ff7aa8"];

  var pair = null;      // выбранная пара
  var mode = "target";
  var timer = null;

  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  function colorFor(name) {
    var h = 0;
    for (var i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
    return COLORS[h % COLORS.length];
  }

  function fmt(value, places) {
    return Number(value).toFixed(places);
  }

  function showError(text) {
    $("error").textContent = text || "";
    $("error").hidden = !text;
  }

  // ------------------------------------------------------------ список пар
  function loadPairs(q) {
    fetch("/api/pairs?q=" + encodeURIComponent(q || ""))
      .then(function (r) { return r.json(); })
      .then(function (list) {
        if (list.error) throw new Error(list.error);
        var body = $("pair-rows");
        body.replaceChildren();

        if (!list.length) {
          var none = el("tr");
          none.append(el("td", "muted", "Ничего не нашлось"));
          body.append(none);
          return;
        }

        list.forEach(function (p) {
          var tr = el("tr", "pair" + (pair && pair.symbol === p.symbol ? " is-on" : ""));
          tr.dataset.symbol = p.symbol;

          var left = el("td");
          var ball = el("span", "ball", p.base.slice(0, 3));
          ball.style.background = colorFor(p.base);
          left.append(ball);
          var name = el("span", "name");
          name.append(el("b", null, p.base));
          name.append(el("span", "usdt", "USDT"));
          if (p.leverage) name.append(el("span", "lev-badge", p.leverage + "x"));
          left.append(name);
          tr.append(left);

          var right = el("td", "right");
          right.append(el("div", "price", p.price ? fmt(p.price, p.places) : "—"));
          if (p.change !== null && p.change !== undefined) {
            right.append(el("div", "chg " + (p.change < 0 ? "dn" : "up"),
              (p.change >= 0 ? "+" : "") + p.change.toFixed(2) + "%"));
          }
          tr.append(right);

          tr.addEventListener("click", function () { pick(p); });
          body.append(tr);
        });
      })
      .catch(function () {
        var body = $("pair-rows");
        body.replaceChildren();
        var tr = el("tr");
        tr.append(el("td", "muted", "MEXC не отвечает. Проверь интернет и обнови страницу."));
        body.append(tr);
      });
  }

  function pick(p) {
    pair = p;
    $("empty").hidden = true;
    $("panel").hidden = false;
    $("title").textContent = p.base + " / USDT";
    $("p1").value = p.price ? fmt(p.price, p.places) : "";
    $("result").hidden = true;
    showError("");

    document.querySelectorAll(".pair").forEach(function (row) {
      row.classList.toggle("is-on", row.dataset.symbol === p.symbol);
    });
    (mode === "target" ? $("target") : $("leverage")).focus();
  }

  // ---------------------------------------------------------------- режим
  document.querySelectorAll(".tab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      mode = tab.dataset.mode;
      document.querySelectorAll(".tab").forEach(function (t) {
        t.classList.toggle("is-on", t === tab);
      });
      $("box-target").hidden = mode !== "target";
      $("box-leverage").hidden = mode !== "leverage";
    });
  });

  // -------------------------------------------------------------- расчёт
  $("form").addEventListener("submit", function (e) {
    e.preventDefault();
    showError("");

    fetch("/api/grid", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: pair ? pair.symbol : "",
        p1: $("p1").value,
        mode: mode,
        target_liq: $("target").value,
        leverage: $("leverage").value,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) throw new Error(d.error);
        render(d);
      })
      .catch(function (err) {
        $("result").hidden = true;
        showError(err.message);
      });
  });

  function render(d) {
    $("lev").textContent = d.leverage + "x";

    var mmr = $("mmr");
    if (d.mmr.known) {
      mmr.textContent = "MMR " + (d.mmr.value * 100).toFixed(3) +
        "% · MEXC, тир " + d.mmr.tier;
      mmr.className = "mmr";
    } else {
      mmr.textContent = "MMR: нет данных — ликвидация без резерва биржи";
      mmr.className = "mmr no-data";
    }

    var body = $("rows");
    body.replaceChildren();
    d.rows.forEach(function (r, i) {
      var tr = el("tr");

      var first = el("td");
      var ball = el("span", "ball step", String(r.step));
      ball.style.background = STEP_COLORS[i];
      first.append(ball);
      tr.append(first);

      tr.append(el("td", "right", fmt(r.price, d.places)));

      var cost = el("td", "right money");
      cost.append(el("div", null, r.margin + " $"));
      if (d.exchange_lots && Math.abs(r.margin_used - r.margin) > 0.005) {
        cost.append(el("div", "sub", "факт " + r.margin_used.toFixed(2)));
      }
      tr.append(cost);

      tr.append(el("td", "right qty", fmt(r.coins, d.qty_places)));
      tr.append(el("td", "right liq", fmt(r.liq, d.places)));
      tr.append(el("td", "right pct", r.pct.toFixed(2) + "%"));

      body.append(tr);
    });

    var notes = $("notes");
    notes.replaceChildren();
    (d.notes || []).forEach(function (t) { notes.append(el("div", "note", t)); });

    $("result").hidden = false;
  }

  $("search").addEventListener("input", function () {
    clearTimeout(timer);
    var q = this.value;
    timer = setTimeout(function () { loadPairs(q); }, 200);
  });

  loadPairs("");
})();
