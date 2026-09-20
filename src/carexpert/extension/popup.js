/* The popup: is CarExpert listening, where, and what did this page give. */

(function () {
  "use strict";

  var api = globalThis.browser || globalThis.chrome;
  var dot = document.getElementById("dot");
  var state = document.getElementById("state");
  var server = document.getElementById("server");
  var auto = document.getElementById("auto");
  var found = document.getElementById("found");
  var dashboard = document.getElementById("dashboard");

  function send(message) {
    return new Promise(function (resolve) {
      api.runtime.sendMessage(message, function (answer) {
        resolve(api.runtime.lastError ? { ok: false, message: api.runtime.lastError.message }
                                      : answer || { ok: false, message: "Aucune reponse." });
      });
    });
  }

  function activeTab() {
    return new Promise(function (resolve) {
      api.tabs.query({ active: true, currentWindow: true }, function (tabs) {
        resolve(tabs && tabs[0] ? tabs[0] : null);
      });
    });
  }

  function number(value) {
    return Number(value || 0).toLocaleString("fr-FR").replace(/ | /g, " ");
  }

  async function refresh() {
    var config = await send({ type: "settings" });
    server.value = config.server || "";
    auto.checked = config.auto !== false;
    dashboard.href = config.server || "#";

    var status = await send({ type: "status" });
    if (status && status.ok) {
      dot.className = "dot on";
      state.textContent = number(status.listings) + " annonces en base";
      if (!status.deep) state.textContent += ", sans cle Claude";
    } else {
      dot.className = "dot off";
      state.textContent = (status && status.message) || "CarExpert ne repond pas.";
    }

    var tab = await activeTab();
    var last = tab ? await send({ type: "last", tabId: tab.id }) : null;
    if (last && last.last && last.last.results) {
      var great = last.last.results.filter(function (item) {
        return item.verdict === "grab";
      }).length;
      found.textContent =
        "Cette page: " + last.last.results.length + " annonces analysees, " + great + " a saisir.";
    } else {
      found.textContent = "";
    }
  }

  function save() {
    return send({ type: "save-settings", server: server.value.trim(), auto: auto.checked });
  }

  server.addEventListener("change", function () {
    save().then(refresh);
  });
  auto.addEventListener("change", function () {
    save();
  });

  document.getElementById("analyse").addEventListener("click", async function () {
    var tab = await activeTab();
    if (!tab) return;
    api.tabs.sendMessage(tab.id, { type: "analyse-now" }, function () {
      if (api.runtime.lastError) {
        found.textContent = "CarExpert ne suit pas ce site.";
        return;
      }
      window.close();
    });
  });

  refresh();
})();
