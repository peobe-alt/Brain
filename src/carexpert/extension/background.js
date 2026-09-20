/* The only place that talks to CarExpert.
 *
 * The page's own JavaScript could call http://127.0.0.1 directly, but a
 * request from an https site to a local address goes through Chrome's
 * private network checks and, on some versions, is simply dropped. A
 * request made here carries the extension's own origin and its host
 * permission, so it is not subject to either. */

const api = globalThis.browser || globalThis.chrome;

const DEFAULTS = {
  server: "http://127.0.0.1:8000",
  auto: true,
};

/* Ce que la derniere analyse a donne, par onglet: le popup l'affiche sans
 * avoir a redemander la page. */
const lastByTab = new Map();

/* Les onglets ouverts depuis une page de resultats, et la page qui les a
 * ouverts. Quand ils ont tous ete lus, elle se remet a jour toute seule: un
 * descriptif fait tomber une annonce de "a saisir" a "a fuir", et il serait
 * absurde de laisser l'ancien verdict affiche derriere. */
const openedFrom = new Map();
const waitingOn = new Map();

/* Jamais plus que ca en un clic. Au-dela ce ne sont plus des pages qu'on
 * ouvre pour les lire, c'est une collecte. */
const MAX_OPEN = 5;

async function settings() {
  try {
    return Object.assign({}, DEFAULTS, await api.storage.sync.get(DEFAULTS));
  } catch (error) {
    return Object.assign({}, DEFAULTS, await api.storage.local.get(DEFAULTS));
  }
}

function unreachable(server) {
  return {
    ok: false,
    message:
      "CarExpert ne repond pas sur " + server +
      ". Lancez CarExpert, puis rechargez la page.",
  };
}

async function call(path, body) {
  const { server } = await settings();
  const base = server.replace(/\/+$/, "");
  let response;
  try {
    response = await fetch(base + path, {
      method: body === undefined ? "GET" : "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (error) {
    return unreachable(base);
  }
  if (!response.ok) {
    let detail = "";
    try {
      detail = (await response.json()).detail || "";
    } catch (error) {
      detail = "";
    }
    return { ok: false, message: detail || "CarExpert a repondu " + response.status + "." };
  }
  const payload = await response.json();
  payload.server = base;
  if (payload.ok === undefined) payload.ok = true;
  return payload;
}

function paintBadge(tabId, results) {
  if (tabId === undefined || !api.action) return;
  const great = results.filter((item) => item.verdict === "grab").length;
  api.action.setBadgeText({ tabId, text: great ? String(great) : "" });
  if (api.action.setBadgeBackgroundColor) {
    api.action.setBadgeBackgroundColor({ tabId, color: "#3ddc97" });
  }
}

async function analysePage(message, tabId) {
  const answer = await call("/api/extension/page", {
    url: message.url,
    html: message.html,
    title: message.title || "",
  });
  if (answer.ok && Array.isArray(answer.results)) {
    lastByTab.set(tabId, { url: message.url, answer, at: Date.now() });
    paintBadge(tabId, answer.results);
    noteDetailRead(tabId);
  }
  return answer;
}

async function openBest(urls, originTabId) {
  if (!api.tabs || !Array.isArray(urls) || !urls.length) {
    return { ok: false, message: "Rien a ouvrir." };
  }
  const wanted = urls.slice(0, MAX_OPEN);
  let opened = 0;
  for (const url of wanted) {
    try {
      // En arriere-plan: la page de resultats reste celle qu'on regarde.
      const tab = await api.tabs.create({ url, active: false });
      openedFrom.set(tab.id, originTabId);
      opened += 1;
    } catch (error) {
      // Un onglet refuse ne doit pas emporter les autres.
    }
  }
  if (opened) waitingOn.set(originTabId, (waitingOn.get(originTabId) || 0) + opened);
  return { ok: opened > 0, opened, message: opened + " annonces ouvertes" };
}

/* Un onglet ouvert vient d'etre lu - ou ferme avant de l'etre. Quand il ne
 * reste plus rien a attendre, la page de resultats relit ses annonces. */
function noteDetailRead(tabId) {
  const origin = openedFrom.get(tabId);
  if (origin === undefined) return;
  openedFrom.delete(tabId);

  const left = (waitingOn.get(origin) || 1) - 1;
  if (left > 0) {
    waitingOn.set(origin, left);
    return;
  }
  waitingOn.delete(origin);
  try {
    api.tabs.sendMessage(origin, { type: "analyse-now" }, function () {
      void api.runtime.lastError;   // l'onglet d'origine a pu etre ferme
    });
  } catch (error) {
    // idem
  }
}

api.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tabId = sender.tab ? sender.tab.id : message.tabId;

  const work = (async () => {
    switch (message.type) {
      case "status":
        return call("/api/extension/status");
      case "page":
        return analysePage(message, tabId);
      case "deep":
        return call("/api/extension/deep", { id: message.id });
      case "open":
        return openBest(message.urls, tabId);
      case "settings":
        return Object.assign({ ok: true }, await settings());
      case "save-settings": {
        const values = { server: message.server, auto: !!message.auto };
        try {
          await api.storage.sync.set(values);
        } catch (error) {
          await api.storage.local.set(values);
        }
        return Object.assign({ ok: true }, values);
      }
      case "last": {
        const seen = lastByTab.get(tabId);
        return { ok: true, last: seen ? seen.answer : null };
      }
      default:
        return { ok: false, message: "Demande inconnue." };
    }
  })();

  work.then(sendResponse).catch((error) =>
    sendResponse({ ok: false, message: String(error && error.message ? error.message : error) })
  );
  return true; // la reponse arrive plus tard
});

if (api.tabs && api.tabs.onRemoved) {
  api.tabs.onRemoved.addListener((tabId) => {
    lastByTab.delete(tabId);
    waitingOn.delete(tabId);
    // Ferme avant d'avoir ete lu: ne pas faire attendre la page d'origine.
    noteDetailRead(tabId);
  });
}
