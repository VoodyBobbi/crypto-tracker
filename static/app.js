(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var pair = null, live = true, mode = "leverage", lastParams = null, busy = false;
  var pairRows = {}, online = false, dataTs = 0, searchTimer = null, launchUsed = false;
  var colors = ["#ffd23f", "#ff7aa8", "#4be0a8", "#6cc4ff", "#c9a7ff", "#ffa94d"];

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function fmt(value, places) {
    var number = Number(value);
    return Number.isFinite(number) ? number.toFixed(places) : "—";
  }
  function volume(value) {
    if (!value) return "";
    if (value >= 1e9) return (value / 1e9).toFixed(2) + "B";
    if (value >= 1e6) return (value / 1e6).toFixed(1) + "M";
    if (value >= 1e3) return (value / 1e3).toFixed(0) + "K";
    return value.toFixed(0);
  }
  function colorFor(text) {
    var hash = 0;
    for (var i = 0; i < text.length; i++) hash = (hash * 31 + text.charCodeAt(i)) >>> 0;
    return colors[hash % colors.length];
  }
  function showError(message) {
    $("error").textContent = message || "";
    $("error").hidden = !message;
  }
  function paintStatus() {
    var age = dataTs ? Math.max(0, Math.round(Date.now() / 1000 - dataTs)) : null;
    var box = $("status");
    box.className = online && age !== null && age < 15 ? "status ok" : "status bad";
    box.lastChild.textContent = age === null ? "Нет связи с MEXC" :
      (online ? "MEXC · обновлено " : "Нет связи · данные ") + age + " с назад";
  }
  function renderPairs(list) {
    var body = $("pair-rows");
    body.replaceChildren();
    pairRows = {};
    if (!list.length) {
      var empty = el("tr");
      empty.append(el("td", "muted", "Пары не найдены"));
      body.append(empty);
      return;
    }
    list.forEach(function (p) {
      var tr = el("tr", "pair" + (pair && pair.symbol === p.symbol ? " is-on" : ""));
      tr.dataset.symbol = p.symbol;
      var left = el("td"), ball = el("span", "ball", p.base.slice(0, 3));
      ball.style.background = colorFor(p.base);
      var name = el("span", "name"), top = el("span", "name-top");
      top.append(el("b", null, p.base), el("span", "usdt", "USDT"));
      var volumeLabel = el("span", "vol", p.volume ? "объём " + volume(p.volume) : "");
      name.append(top, volumeLabel);
      left.append(ball, name);
      tr.append(left);
      var right = el("td", "right");
      var price = el("div", "price", p.price ? fmt(p.price, p.places) : "—");
      var change = el("div", "chg");
      if (p.change !== null && p.change !== undefined) {
        change.textContent = (p.change >= 0 ? "+" : "") + p.change.toFixed(2) + "%";
        change.className = "chg " + (p.change < 0 ? "dn" : "up");
      }
      right.append(price, change);
      tr.append(right);
      pairRows[p.symbol] = { data: p, price: price, change: change, volume: volumeLabel };
      tr.addEventListener("click", function () { pick(pairRows[p.symbol].data); });
      body.append(tr);
    });
    if (!pair && list[0].price) pick(list[0]);
  }
  function loadPairs() {
    return fetch("/api/pairs?q=" + encodeURIComponent($("search").value))
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (!Array.isArray(data)) throw new Error(data.error || "Не удалось загрузить пары MEXC.");
        renderPairs(data);
      }).catch(function (error) {
        if (!Object.keys(pairRows).length) {
          var row = el("tr");
          row.append(el("td", "muted", error.message));
          $("pair-rows").replaceChildren(row);
        }
      });
  }
  function showExchangeContext(data) {
    var box = $("exchange-info");
    box.className = "status ok";
    var marginMode = Number(data.open_type) === 1 ? "изолированная" : "кросс-маржа";
    box.textContent = "MEXC · fair price " + fmt(data.fair_price, pair.places) +
      " · фандинг " + fmt(Number(data.funding_rate) * 100, 4) + "% (лимит " +
      fmt(Number(data.max_funding_rate) * 100, 4) + "%) · цикл " + data.collect_cycle + " ч" +
      " · maker/taker " + fmt(Number(data.maker_fee) * 100, 4) + "%/" +
      fmt(Number(data.taker_fee) * 100, 4) + "% · плечо " + (data.leverage || "—") +
      "x (макс. " + data.max_leverage + "x) · " + marginMode +
      " · доступно " + fmt(data.available, 2) + " USDT";
    if (!$("leverage").value && data.leverage) $("leverage").value = data.leverage;
  }
  function loadExchangeContext() {
    if (!pair) return;
    var box = $("exchange-info");
    box.className = "status bad";
    box.textContent = "Загружаю параметры рынка и аккаунта MEXC…";
    fetch("/api/context?symbol=" + encodeURIComponent(pair.symbol))
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (data.error) throw new Error(data.error);
        showExchangeContext(data);
      }).catch(function (error) {
        box.className = "status bad";
        box.textContent = error.message;
      });
  }
  function pollPrices() {
    var symbols = Object.keys(pairRows);
    if (pair && symbols.indexOf(pair.symbol) < 0) symbols.push(pair.symbol);
    if (!symbols.length) return;
    fetch("/api/prices?symbols=" + encodeURIComponent(symbols.join(",")))
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (data.error) throw new Error(data.error);
        online = data.fresh;
        dataTs = data.ts;
        Object.keys(data.prices).forEach(function (symbol) {
          var fresh = data.prices[symbol], row = pairRows[symbol];
          if (row) {
            row.data.price = fresh.price;
            row.price.textContent = fmt(fresh.price, row.data.places);
            row.data.change = fresh.change;
            row.data.volume = fresh.volume;
            row.volume.textContent = fresh.volume ? "объём " + volume(fresh.volume) : "";
            if (fresh.change !== null && fresh.change !== undefined) {
              row.change.textContent = (fresh.change >= 0 ? "+" : "") + fresh.change.toFixed(2) + "%";
              row.change.className = "chg " + (fresh.change < 0 ? "dn" : "up");
            }
          }
          if (pair && symbol === pair.symbol) onPrice(fresh.price);
        });
        paintStatus();
      }).catch(function () { online = false; paintStatus(); });
  }
  function onPrice(price) {
    pair.price = price;
    if (!live) return;
    $("p1").value = fmt(price, pair.places);
    if (lastParams) calculate(true);
  }
  function setLive(value) {
    live = value;
    $("live").classList.toggle("is-on", value);
    $("live").textContent = value ? "● fair price MEXC" : "вернуть fair price";
    if (value && pair && pair.price) onPrice(pair.price);
  }
  function pick(p) {
    pair = p;
    lastParams = null;
    $("empty").hidden = true;
    $("panel").hidden = false;
    $("title").textContent = p.base + " / USDT";
    $("result").hidden = true;
    launchUsed = false;
    $("launch").disabled = false;
    $("launch-status").hidden = true;
    $("leverage").value = "";
    showError("");
    setLive(true);
    if (p.price) $("p1").value = fmt(p.price, p.places);
    document.querySelectorAll(".pair").forEach(function (row) {
      row.classList.toggle("is-on", row.dataset.symbol === p.symbol);
    });
    loadExchangeContext();
  }
  document.querySelectorAll(".tab").forEach(function (tab) {
    tab.addEventListener("click", function () {
      mode = tab.dataset.mode;
      document.querySelectorAll(".tab").forEach(function (item) {
        item.classList.toggle("is-on", item === tab);
      });
      $("box-target").hidden = mode !== "target";
      $("box-leverage").hidden = mode !== "leverage";
      lastParams = null;
    });
  });
  function calculate(auto) {
    if (busy || !pair) return;
    var params = auto ? lastParams : {
      symbol: pair.symbol,
      mode: mode,
      target_liq: $("target").value,
      leverage: $("leverage").value
    };
    if (!params) return;
    busy = true;
    fetch("/api/grid", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(Object.assign({ p1: $("p1").value }, params))
    }).then(function (response) { return response.json(); })
      .then(function (data) {
        if (data.error) throw new Error(data.error);
        if (!auto) lastParams = params;
        showError("");
        render(data);
      }).catch(function (error) {
        $("result").hidden = true;
        showError(error.message);
      }).then(function () { busy = false; });
  }
  $("form").addEventListener("submit", function (event) {
    event.preventDefault();
    calculate(false);
  });
  $("launch").addEventListener("click", function () {
    if (busy || !pair || launchUsed) return;
    busy = true;
    launchUsed = true;
    $("launch").disabled = true;
    var status = $("launch-status");
    status.className = "launch-status";
    status.textContent = "Проверяю баланс и параметры MEXC…";
    status.hidden = false;
    fetch("/api/grid/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: pair.symbol,
        mode: mode,
        target_liq: $("target").value,
        leverage: $("leverage").value
      })
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok || data.error) {
          launchUsed = Boolean(data.first_order_id || data.status_unknown);
          $("launch").disabled = launchUsed;
          var message = data.error || "MEXC отклонила запуск сетки.";
          if (data.first_order_id) message += " Первый ордер: " + data.first_order_id + ".";
          throw new Error(message);
        }
        return data;
      });
    }).then(function (data) {
      var lines = ["Ордер " + data.first_order_id + " исполнен по " +
        fmt(data.first_fill_price, pair.places) + "."];
      data.limits.forEach(function (order) {
        lines.push("Шаг " + order.step + ": лимит " + fmt(order.price, pair.places) +
          ", ордер " + order.order_id + ".");
      });
      lines.push("Резерв: " + fmt(data.budget_required, 4) + " из " +
        fmt(data.budget_limit, 2) + " USDT (маржа, комиссия и фандинг до шага 4).");
      status.className = "launch-status ok";
      status.textContent = lines.join("\n");
    }).catch(function (error) {
      status.className = "launch-status bad";
      status.textContent = error.message + " Проверь ордера на MEXC перед повторным запуском.";
    }).then(function () {
      busy = false;
    });
  });
  function render(data) {
    $("lev").textContent = data.leverage + "x";
    $("mmr-label").textContent = data.mode + " · MMR " + (data.mmr * 100).toFixed(3) +
      "% · комиссия до " + (data.fee_rate * 100).toFixed(4) +
      "% · фандинг сейчас " + (Number(data.funding_rate) * 100).toFixed(4) +
      "% · в резерв заложено " + (data.funding_stress_rate * 100).toFixed(4) +
      "% × " + data.funding_events + " спис.";
    var body = $("rows");
    body.replaceChildren();
    data.rows.forEach(function (row) {
      var tr = el("tr");
      tr.append(el("td", null, String(row.step)));
      tr.append(el("td", "right", fmt(row.price, data.places)));
      tr.append(el("td", "right money", fmt(row.actual_margin, 4)));
      tr.append(el("td", "right", fmt(row.contracts, data.volume_places)));
      tr.append(el("td", "right", fmt(row.notional, 4)));
      tr.append(el("td", "right liq", fmt(row.liq, data.places)));
      tr.append(el("td", "right", (row.mmr * 100).toFixed(3) + "%"));
      body.append(tr);
    });
    $("estimate-note").textContent = "Резерв фандинга рассчитан на внутренний срок 24 ч плюс одно дополнительное списание. " +
      "Для расчёта берётся запас по текущей ставке MEXC; фактическая ставка и расписание могут измениться. " +
      "Оценка не гарантирует защиту от ликвидации.";
    $("result").hidden = false;
  }
  $("p1").addEventListener("input", function () { if (live) setLive(false); });
  $("live").addEventListener("click", function () { setLive(!live); });
  ["target", "leverage"].forEach(function (id) {
    $(id).addEventListener("input", function () { lastParams = null; });
  });
  $("search").addEventListener("input", function () {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(loadPairs, 200);
  });
  loadPairs().then(pollPrices);
  setInterval(pollPrices, 3000);
  setInterval(paintStatus, 1000);
})();
