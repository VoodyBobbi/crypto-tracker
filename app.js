(function () {
  "use strict";

  var PRICE_EVERY = 3000;     // цены — каждые 3 секунды
  var ORDER_EVERY = 60000;    // порядок списка по объёму — раз в минуту

  var $ = function (id) { return document.getElementById(id); };
  var COLORS = ["#ffd23f", "#ff7aa8", "#4be0a8", "#6cc4ff", "#c9a7ff", "#ffa94d"];
  var STEP_COLORS = ["#4be0a8", "#ffd23f", "#ffa94d", "#ff7aa8"];

  var pair = null;          // выбранная пара
  var mode = "target";
  var live = true;          // цена входа следует за рынком
  var lastParams = null;    // параметры последнего нажатия «Посчитать»
  var busy = false;
  var calcP1 = null;        // цена, по которой посчитана текущая таблица
  var rows = {};            // symbol -> ячейки строки списка, для обновления на месте
  var dataTs = 0;           // когда биржа последний раз отдала цены
  var online = false;
  var searchTimer = null;

  // --------------------------------------------------------------- утилиты
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

  function fmt(value, places) { return Number(value).toFixed(places); }

  function volume(v) {
    if (!v) return "";
    if (v >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (v >= 1e6) return (v / 1e6).toFixed(1) + "M";
    if (v >= 1e3) return (v / 1e3).toFixed(0) + "K";
    return v.toFixed(0);
  }

  function showError(text) {
    $("error").textContent = text || "";
    $("error").hidden = !text;
  }

  // ---------------------------------------------------------- статус связи
  function paintStatus() {
    var box = $("status");
    var age = dataTs ? Math.max(0, Math.round(Date.now() / 1000 - dataTs)) : null;
    if (online && age !== null && age < 15) {
      box.className = "status ok";
      box.lastChild.textContent = "MEXC · обновлено " + age + " с назад";
    } else if (age !== null) {
      box.className = "status bad";
      box.lastChild.textContent = "Нет связи с MEXC · цены " + age + " с назад";
    } else {
      box.className = "status bad";
      box.lastChild.textContent = "Нет связи с MEXC";
    }
  }

  // ------------------------------------------------------------ список пар
  function renderPairs(list) {
    var body = $("pair-rows");
    var keepScroll = body.parentNode.scrollTop;
    body.replaceChildren();
    rows = {};

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
      var top = el("span", "name-top");
      top.append(el("b", null, p.base));
      top.append(el("span", "usdt", "USDT"));
      if (p.leverage) top.append(el("span", "lev-badge", p.leverage + "x"));
      name.append(top);
      var vol = el("span", "vol", p.volume ? "объём " + volume(p.volume) : "");
      name.append(vol);
      left.append(name);
      tr.append(left);

      var right = el("td", "right");
      var price = el("div", "price", p.price ? fmt(p.price, p.places) : "—");
      var chg = el("div", "chg");
      right.append(price);
      right.append(chg);
      tr.append(right);

      rows[p.symbol] = { data: p, price: price, chg: chg, vol: vol };
      paintChange(chg, p.change);

      tr.addEventListener("click", function () { pick(rows[p.symbol].data); });
      body.append(tr);
    });
    body.parentNode.scrollTop = keepScroll;
  }

  function paintChange(node, change) {
    if (change === null || change === undefined) { node.textContent = ""; return; }
    node.textContent = (change >= 0 ? "+" : "") + change.toFixed(2) + "%";
    node.className = "chg " + (change < 0 ? "dn" : "up");
  }

  function loadPairs() {
    var q = $("search").value;
    return fetch("/api/pairs?q=" + encodeURIComponent(q))
      .then(function (r) { return r.json(); })
      .then(function (list) {
        if (list.error) throw new Error(list.error);
        renderPairs(list);
      })
      .catch(function () {
        if (Object.keys(rows).length) return;   // старый список лучше пустого
        var body = $("pair-rows");
        body.replaceChildren();
        var tr = el("tr");
        tr.append(el("td", "muted", "MEXC не отвечает. Проверь интернет — список появится сам."));
        body.append(tr);
      });
  }

  // ---------------------------------------------------- живые цены на месте
  function pollPrices() {
    var symbols = Object.keys(rows);
    if (pair && symbols.indexOf(pair.symbol) < 0) symbols.push(pair.symbol);
    if (!symbols.length) return;

    fetch("/api/prices?symbols=" + encodeURIComponent(symbols.join(",")))
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) throw new Error(d.error);
        online = d.fresh;
        dataTs = d.ts;

        Object.keys(d.prices).forEach(function (sym) {
          var fresh = d.prices[sym];
          var row = rows[sym];
          if (row) {
            var before = row.data.price;
            row.data.price = fresh.price;
            row.data.change = fresh.change;
            row.data.volume = fresh.volume;
            row.price.textContent = fmt(fresh.price, row.data.places);
            paintChange(row.chg, fresh.change);
            if (fresh.volume) row.vol.textContent = "объём " + volume(fresh.volume);
            if (before && fresh.price !== before) flash(row.price, fresh.price > before);
          }
          if (pair && sym === pair.symbol) onPairPrice(fresh.price);
        });
        paintStatus();
      })
      .catch(function () { online = false; paintStatus(); });
  }

  function flash(node, up) {
    node.classList.remove("flash-up", "flash-dn");
    void node.offsetWidth;                      // перезапуск анимации
    node.classList.add(up ? "flash-up" : "flash-dn");
  }

  // ------------------------------------------------- выбранная пара и цена
  function onPairPrice(price) {
    pair.price = price;
    if (!live) return;
    $("p1").value = fmt(price, pair.places);
    // Таблица следует за рынком. Сравниваем с ценой, по которой она посчитана,
    // а не с прошлым тиком: pair и строка списка — один объект.
    if (lastParams && $("p1").value !== calcP1) calculate(true);
  }

  function setLive(on) {
    live = on;
    var btn = $("live");
    btn.classList.toggle("is-on", on);
    btn.textContent = on ? "● по рынку" : "вернуть цену рынка";
    if (on && pair && pair.price) {
      $("p1").value = fmt(pair.price, pair.places);
      if (lastParams) calculate(true);
    }
  }

  function pick(p) {
    pair = p;
    lastParams = null;
    calcP1 = null;
    $("empty").hidden = true;
    $("panel").hidden = false;
    $("title").textContent = p.base + " / USDT";
    $("result").hidden = true;
    showError("");
    setLive(true);
    if (!p.price) $("p1").value = "";

    document.querySelectorAll(".pair").forEach(function (row) {
      row.classList.toggle("is-on", row.dataset.symbol === p.symbol);
    });
    (mode === "target" ? $("target") : $("leverage")).focus();
  }

  // ------------------------------------------------------------------ режим
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

  // ---------------------------------------------------------------- расчёт
  function calculate(auto) {
    if (busy) return;
    var params = auto ? lastParams : {
      mode: mode,
      target_liq: $("target").value,
      leverage: $("leverage").value,
    };
    if (!params) return;
    busy = true;
    var sentP1 = $("p1").value;

    fetch("/api/grid", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: pair ? pair.symbol : "",
        p1: $("p1").value,
        mode: params.mode,
        target_liq: params.target_liq,
        leverage: params.leverage,
      }),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d.error) throw new Error(d.error);
        if (!auto) lastParams = params;
        calcP1 = sentP1;
        showError("");
        render(d);
      })
      .catch(function (err) {
        if (!auto) $("result").hidden = true;
        showError(err.message);
      })
      .then(function () { busy = false; });
  }

  $("form").addEventListener("submit", function (e) {
    e.preventDefault();
    calculate(false);
  });

  function render(d) {
    $("lev").textContent = d.leverage + "x";

    var mmr = $("mmr");
    if (d.mmr.known) {
      mmr.textContent = "MMR " + (d.mmr.value * 100).toFixed(3) + "% · MEXC, тир " + d.mmr.tier;
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

  // ---------------------------------------------------------------- события
  $("p1").addEventListener("input", function () { if (live) setLive(false); });
  $("live").addEventListener("click", function () { setLive(!live); });

  $("search").addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadPairs, 200);
  });

  loadPairs().then(pollPrices);
  setInterval(pollPrices, PRICE_EVERY);
  setInterval(loadPairs, ORDER_EVERY);
  setInterval(paintStatus, 1000);
})();
