(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  var searchInput = $("pair-search");
  var pairList = $("pair-list");
  var pickerCount = $("picker-count");
  var reloadBtn = $("picker-reload");

  var form = $("calc-form");
  var symbolInput = $("symbol");
  var p1Input = $("p1");
  var calcSymbol = $("calc-symbol");
  var calcMeta = $("calc-meta");
  var priceTag = $("price-tag");
  var priceValue = $("price-value");
  var usePriceBtn = $("use-price");

  var fieldTarget = $("field-target");
  var fieldLeverage = $("field-leverage");
  var fieldK = $("field-k");
  var kHint = $("k-hint");

  var errorBox = $("calc-error");
  var warnBox = $("calc-warnings");
  var result = $("result");
  var gridBody = $("grid-body");
  var detailBody = $("detail-body");
  var resLeverage = $("res-leverage");
  var resStats = $("res-stats");

  var selected = null;   // выбранный контракт
  var lastPrice = null;
  var searchTimer = null;

  // ----------------------------------------------------------------- utils
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function fixed(value, places) {
    if (value === null || value === undefined || isNaN(value)) return "—";
    return Number(value).toFixed(places);
  }

  // Точка как разделитель дробной части — как в интерфейсе биржи,
  // неразрывный тонкий пробел для тысяч.
  function compact(value, places) {
    if (value === null || value === undefined || isNaN(value)) return "—";
    var parts = Number(value).toFixed(places).split(".");
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, "\u202F");
    return parts.join(".");
  }

  function show(node, visible) {
    if (node) node.hidden = !visible;
  }

  function setError(message) {
    if (!message) { show(errorBox, false); return; }
    errorBox.textContent = message;
    show(errorBox, true);
  }

  // -------------------------------------------------- панель инструментов
  function renderPairs(contracts) {
    pairList.replaceChildren();
    if (!contracts.length) {
      pairList.append(el("li", "pair-empty", "Ничего не найдено."));
      return;
    }

    contracts.forEach(function (c) {
      var li = document.createElement("li");
      var row = el("button", "pair-row");
      row.type = "button";
      row.dataset.symbol = c.symbol;
      if (selected && selected.symbol === c.symbol) row.classList.add("is-active");

      row.append(el("span", "pair-badge", (c.base || "?").slice(0, 3)));

      var mid = el("span", "pair-text");
      mid.append(el("span", "pair-symbol", c.symbol));
      mid.append(el("span", "pair-desc", c.display_name));
      row.append(mid);

      row.append(el("span", "pair-lev",
        c.max_leverage ? "до " + c.max_leverage + "x" : "—"));

      row.addEventListener("click", function () { selectPair(c.symbol); });
      li.append(row);
      pairList.append(li);
    });
  }

  function loadPairs(query) {
    pickerCount.textContent = "Загружаю…";
    fetch("/api/mexc/search?q=" + encodeURIComponent(query || ""))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) throw new Error(data.error);
        renderPairs(data.contracts);
        var note = data.contracts.length + " контрактов";
        if (!data.fresh) note += " · кэш, биржа не ответила";
        pickerCount.textContent = note;
      })
      .catch(function (err) {
        pairList.replaceChildren();
        pairList.append(el("li", "pair-error",
          "Список не загрузился: " + err.message +
          ". Калькулятор работает и без пары — введите цену вручную."));
        pickerCount.textContent = "MEXC недоступна";
      });
  }

  // ------------------------------------------------------- выбор контракта
  function selectPair(symbol) {
    fetch("/api/mexc/contract/" + encodeURIComponent(symbol))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) throw new Error(data.error);
        selected = data.contract;
        symbolInput.value = selected.symbol;
        calcSymbol.textContent = selected.symbol;

        var bits = [selected.display_name, "макс. плечо " + selected.max_leverage + "x"];
        if (selected.taker_fee) {
          bits.push("taker " + (selected.taker_fee * 100).toFixed(3) + "%");
        }
        if (data.funding && data.funding.rate) {
          bits.push("фандинг " + (data.funding.rate * 100).toFixed(4) + "%");
        }
        calcMeta.textContent = bits.join(" · ");

        lastPrice = data.ticker && data.ticker.last ? data.ticker.last : null;
        if (lastPrice) {
          priceValue.textContent = fixed(lastPrice, selected.price_scale);
          show(priceTag, true);
          if (!p1Input.value) p1Input.value = fixed(lastPrice, selected.price_scale);
        } else {
          show(priceTag, false);
        }

        Array.prototype.forEach.call(
          pairList.querySelectorAll(".pair-row"),
          function (row) {
            row.classList.toggle("is-active", row.dataset.symbol === selected.symbol);
          }
        );
        setError("");
      })
      .catch(function (err) { setError(err.message); });
  }

  // ------------------------------------------------------------- рендеринг
  function pathCell(pct) {
    var cell = el("td", "path-cell");
    var wrap = el("div", "path-wrap");
    var track = el("div", "path-track");
    var fill = el("div", "path-fill");
    fill.style.width = Math.max(0, Math.min(100, pct)) + "%";
    track.append(fill);
    wrap.append(track);
    wrap.append(el("span", "path-num", pct.toFixed(2) + "%"));
    cell.append(wrap);
    return cell;
  }

  function renderRows(data) {
    var places = data.places;

    gridBody.replaceChildren();
    data.rows.forEach(function (row) {
      var tr = document.createElement("tr");
      tr.append(el("td", "col-step", row.step));
      tr.append(el("td", null, fixed(row.price, places)));
      tr.append(el("td", null, row.margin.toFixed(0)));
      tr.append(el("td", null, row.leverage + "x"));
      tr.append(el("td", "liq", fixed(row.liq, places)));
      tr.append(pathCell(row.pct_path));
      gridBody.append(tr);
    });

    detailBody.replaceChildren();
    data.rows.forEach(function (row) {
      var tr = document.createElement("tr");
      tr.append(el("td", "col-step", row.step));
      tr.append(el("td", null, compact(row.coins, 4)));
      tr.append(el("td", null, compact(row.cum_coins, 4)));
      tr.append(el("td", null, fixed(row.avg, places)));
      tr.append(el("td", null, compact(row.position_value, 2)));
      tr.append(el("td", null, (row.k * 100).toFixed(2) + "%"));
      tr.append(el("td", null, row.mmr === null
        ? "—"
        : (row.mmr * 100).toFixed(3) + "% / т" + row.tier));
      tr.append(el("td", null, row.fee ? row.fee.toFixed(4) : "—"));
      detailBody.append(tr);
    });
  }

  function stat(label, value) {
    var wrap = document.createElement("div");
    wrap.append(el("dt", null, label));
    wrap.append(el("dd", null, value));
    return wrap;
  }

  function renderStats(data) {
    resLeverage.textContent = data.leverage + "x";
    resStats.replaceChildren();

    if (data.leverage_exact !== null && data.leverage_exact !== undefined) {
      resStats.append(stat("Точное плечо", data.leverage_exact.toFixed(4)));
    }
    if (data.target_liq !== null && data.target_liq !== undefined) {
      resStats.append(stat("Цель ликвидации", fixed(data.target_liq, data.places)));
      resStats.append(stat("Отклонение после округления",
        (data.liq_delta >= 0 ? "+" : "") + fixed(data.liq_delta, data.places)));
    }
    resStats.append(stat("Маржа всего", data.total_margin.toFixed(0) + " USDT"));
    resStats.append(stat("Монет в позиции", compact(data.total_coins, 4)));
    if (data.total_fee) {
      resStats.append(stat("Комиссия taker", data.total_fee.toFixed(4) + " USDT"));
    }
  }

  function renderWarnings(warnings) {
    warnBox.replaceChildren();
    if (!warnings || !warnings.length) { show(warnBox, false); return; }
    warnings.forEach(function (text) { warnBox.append(el("div", "warn", text)); });
    show(warnBox, true);
  }

  // ------------------------------------------------------------- поведение
  function currentMode() {
    var checked = form.querySelector('input[name="mode"]:checked');
    return checked ? checked.value : "target";
  }

  function currentKMode() {
    var checked = form.querySelector('input[name="k_mode"]:checked');
    return checked ? checked.value : "ideal";
  }

  function syncMode() {
    var mode = currentMode();
    show(fieldTarget, mode === "target");
    show(fieldLeverage, mode === "leverage");
  }

  var K_HINTS = {
    ideal: "Идеализация: ликвидация ровно при исчерпании всей маржи. " +
           "На бирже она наступает раньше — часть маржи резервируется.",
    mexc: "k = 1 − MMR × плечо, где MMR берётся из тира под размер позиции. " +
          "Позиция растёт по шагам, поэтому k пересчитывается на каждом шаге.",
    manual: "Калибровка по факту: k = плечо × (1 − реальная ликвидация ÷ цена входа). " +
            "Реальную ликвидацию возьмите из интерфейса биржи после первого шага.",
  };

  function syncKMode() {
    var mode = currentKMode();
    show(fieldK, mode === "manual");
    kHint.textContent = K_HINTS[mode];
    if (mode === "mexc" && !symbolInput.value) {
      kHint.textContent += " Сейчас пара не выбрана — расчёт пойдёт с k = 1.0.";
    }
  }

  form.addEventListener("change", function (event) {
    if (event.target.name === "mode") syncMode();
    if (event.target.name === "k_mode") syncKMode();
  });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    setError("");

    var payload = {
      symbol: symbolInput.value,
      p1: p1Input.value,
      mode: currentMode(),
      target_liq: $("target_liq").value,
      leverage: $("leverage").value,
      k_mode: currentKMode(),
      k_manual: $("k_manual").value,
    };

    fetch("/api/grid", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) throw new Error(data.error);
        renderStats(data);
        renderRows(data);
        renderWarnings(data.warnings);
        show(result, true);
        result.scrollIntoView({ behavior: "smooth", block: "nearest" });
      })
      .catch(function (err) {
        show(result, false);
        show(warnBox, false);
        setError(err.message);
      });
  });

  $("reset-btn").addEventListener("click", function () {
    form.reset();
    selected = null;
    symbolInput.value = "";
    calcSymbol.textContent = "Пара не выбрана";
    calcMeta.textContent = "Можно считать и без пары: введите цену входа вручную.";
    show(priceTag, false);
    show(result, false);
    show(warnBox, false);
    setError("");
    syncMode();
    syncKMode();
    Array.prototype.forEach.call(
      pairList.querySelectorAll(".pair-row"),
      function (row) { row.classList.remove("is-active"); }
    );
  });

  usePriceBtn.addEventListener("click", function () {
    if (lastPrice && selected) {
      p1Input.value = fixed(lastPrice, selected.price_scale);
      p1Input.focus();
    }
  });

  searchInput.addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(function () { loadPairs(searchInput.value); }, 250);
  });

  reloadBtn.addEventListener("click", function () { loadPairs(searchInput.value); });

  syncMode();
  syncKMode();
  loadPairs("");
})();
