/* Draw CarExpert's verdict over the site's own page.
 *
 * The page is read, never re-fetched: what is sent to CarExpert is the HTML
 * the browser has already rendered. A results page that only exists after
 * JavaScript has run is therefore readable like any other, and the site
 * receives not one extra request. */

(function () {
  "use strict";

  var api = globalThis.browser || globalThis.chrome;
  var MARK = "data-carexpert";
  var VERDICTS = { grab: "A SAISIR", check: "A VERIFIER", avoid: "A FUIR", unknown: "A ESTIMER" };

  var state = { url: "", busy: false, deep: false, answer: null, collapsed: false,
                retried: false };

  // --- Lire la page --------------------------------------------------------

  /* Le JSON-LD est dans un <script>: le garder est tout l'interet de
   * l'operation. Le reste - feuilles de style, images en base64, cadres -
   * ne sert a rien a l'extraction et multiplie le poids envoye. */
  function pageHtml() {
    var clone = document.documentElement.cloneNode(true);
    var dead = clone.querySelectorAll(
      'script:not([type="application/ld+json"]), style, link[rel="stylesheet"], ' +
      "svg, canvas, iframe, noscript, template, video, audio, " +
      ".carexpert-root"
    );
    Array.prototype.forEach.call(dead, function (node) {
      node.remove();
    });
    Array.prototype.forEach.call(clone.querySelectorAll("img[srcset], source"), function (node) {
      node.removeAttribute("srcset");
    });
    Array.prototype.forEach.call(clone.querySelectorAll('img[src^="data:"]'), function (node) {
      node.removeAttribute("src");
    });
    return "<!doctype html><html>" + clone.innerHTML + "</html>";
  }

  /* Attendre que la page arrete de bouger: sur ces sites les annonces
   * arrivent apres le chargement, et capturer trop tot ne montre rien. */
  function settle(quiet, cap) {
    return new Promise(function (resolve) {
      var observer = new MutationObserver(function () {
        clearTimeout(timer);
        timer = setTimeout(done, quiet);
      });
      var timer = setTimeout(done, quiet);
      var hard = setTimeout(done, cap);

      function done() {
        clearTimeout(timer);
        clearTimeout(hard);
        observer.disconnect();
        resolve();
      }

      if (document.body) observer.observe(document.body, { childList: true, subtree: true });
    });
  }

  function send(message) {
    return new Promise(function (resolve) {
      try {
        api.runtime.sendMessage(message, function (answer) {
          if (api.runtime.lastError) {
            resolve({ ok: false, message: api.runtime.lastError.message });
            return;
          }
          resolve(answer || { ok: false, message: "Aucune reponse de CarExpert." });
        });
      } catch (error) {
        resolve({ ok: false, message: String(error) });
      }
    });
  }

  async function analyse() {
    if (state.busy) return;
    state.busy = true;
    state.url = location.href;
    hud({ message: "Analyse en cours..." });
    await settle(700, 5000);

    var answer = await send({ type: "page", url: location.href, html: pageHtml() });
    state.busy = false;
    state.answer = answer;
    clear();
    if (!answer.ok) {
      hud(answer);
      return;
    }

    /* Rien de lisible au premier passage veut souvent dire "pas encore":
     * ces sites affichent un bandeau de consentement avant leurs annonces,
     * et la page reste vide tant qu'il est la. Une seule reprise, trois
     * secondes plus tard, evite d'avoir a cliquer soi-meme. */
    if (!answer.count && !state.retried) {
      state.retried = true;
      hud({ message: "Page pas encore prete, nouvelle lecture dans 3 secondes..." });
      setTimeout(analyse, 3000);
      return;
    }
    paint(answer);
  }

  // --- Dessiner ------------------------------------------------------------

  function node(tag, className, text) {
    var element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = String(text);
    return element;
  }

  function money(value) {
    if (value === null || value === undefined) return "-";
    return Math.round(value).toLocaleString("fr-FR").replace(/\u202f|\u00a0/g, " ") + " EUR";
  }

  /* Pas de prix de marche tant que la base ne le porte pas.
   *
   * Un bandeau qui annonce "a estimer" et "45% sous le marche" dans la meme
   * ligne est lu comme un prix de marche: c'est le chiffre qu'on retient, pas
   * l'etiquette. Tant que l'estimation n'existe pas, on dit ce qui manque. */
  function marketLine(result) {
    if (result.fair_price_eur) {
      var pct = Math.round((result.delta_pct || 0) * 100);
      var sense = pct >= 0 ? "sous le marche" : "au-dessus du marche";
      return Math.abs(pct) + "% " + sense + ", estime a " + money(result.fair_price_eur);
    }
    var basis = result.basis || {};
    if (!basis.comps) return "prix non situe: aucune annonce comparable en base";
    return (
      "prix non situe: " + basis.comps + " annonce" + (basis.comps > 1 ? "s" : "") +
      " comparable" + (basis.comps > 1 ? "s" : "") + " en base sur " + basis.needed
    );
  }

  function alertLine(result) {
    var flags = (result.red_flags || []).length;
    if (!flags) return "";
    return flags + " alerte" + (flags > 1 ? "s" : "") + " dans le texte de l'annonce";
  }

  function clear() {
    Array.prototype.forEach.call(
      document.querySelectorAll(".carexpert-badge, .carexpert-panel"),
      function (element) {
        element.remove();
      }
    );
    Array.prototype.forEach.call(document.querySelectorAll("[" + MARK + "]"), function (card) {
      card.removeAttribute(MARK);
    });
  }

  /* Retrouver la carte d'une annonce dans la page.
   *
   * Par identifiant d'abord: sur AutoScout24 les <a> de titre n'ont pas
   * d'attribut href tant que le site n'a pas fini de s'hydrater, alors que
   * l'<article> porte l'UUID de l'annonce des le depart. */
  function findCard(result) {
    var id = result.source_id || "";
    var safe = window.CSS && CSS.escape ? CSS.escape(id) : id.replace(/["\\]/g, "");
    var found = null;
    if (id) {
      found =
        document.getElementById(id) ||
        document.querySelector(
          '[data-guid="' + safe + '"], [data-listing-id="' + safe + '"], ' +
          '[data-id="' + safe + '"], [data-article-id="' + safe + '"]'
        );
      if (!found) {
        var link = document.querySelector('a[href*="' + safe + '"]');
        if (link) found = link;
      }
    }
    if (!found && result.url) {
      var path = "";
      try {
        path = new URL(result.url).pathname;
      } catch (error) {
        path = "";
      }
      if (path) found = document.querySelector('a[href="' + path + '"], a[href$="' + path + '"]');
    }
    if (!found) return null;
    return found.closest("article, li") || found.parentElement;
  }

  function badge(result) {
    var tone = "carexpert-" + (result.verdict || "unknown");
    var box = node("a", "carexpert-root carexpert-badge " + tone);
    box.href = dashboard() + "/listing/" + result.id;
    box.target = "_blank";
    box.rel = "noreferrer";
    box.title = result.headline || "";

    box.appendChild(node("span", "carexpert-score", scoreLabel(result)));
    var text = node("span", "carexpert-text");
    text.appendChild(node("b", "carexpert-verdict", VERDICTS[result.verdict] || "A ESTIMER"));
    text.appendChild(node("span", "carexpert-market", marketLine(result)));
    box.appendChild(text);

    var alert = alertLine(result);
    if (result.fair_price_eur && result.net_gain_eur > 0) {
      box.appendChild(node("span", "carexpert-gain", "+" + money(result.net_gain_eur)));
    } else if (alert) {
      box.appendChild(node("span", "carexpert-alert", alert));
    }
    return box;
  }

  /* Un score sans position prix n'est pas une note d'affaire: c'est la
   * lecture du texte et de l'etat, et rien de plus. L'afficher en gros a
   * cote de "A ESTIMER" le ferait lire comme un verdict. */
  function scoreLabel(result) {
    if (!result.fair_price_eur) return "?";
    return result.score === null || result.score === undefined ? "?" : result.score;
  }

  function basisNote(result) {
    var basis = result.basis || {};
    var box = node("div", "carexpert-basis");
    box.appendChild(node("b", "carexpert-basis-title", "Prix non situe"));
    if (basis.comps) {
      box.appendChild(node("span", null,
        basis.comps + " annonce" + (basis.comps > 1 ? "s" : "") + " comparable" +
        (basis.comps > 1 ? "s" : "") + " en base, il en faut " + basis.needed +
        " a ce niveau de similitude."));
    } else {
      box.appendChild(node("span", null,
        "Aucune annonce comparable en base pour cette voiture."));
    }
    if (basis.known) {
      box.appendChild(node("span", null,
        "CarExpert connait " + basis.known + " " + (basis.model || "annonces") +
        ". Ouvrez d'autres pages de resultats: chaque page lue nourrit la base."));
    }
    return box;
  }

  function panel(result) {
    var tone = "carexpert-" + (result.verdict || "unknown");
    var box = node("aside", "carexpert-root carexpert-panel " + tone);

    var head = node("header", "carexpert-head");
    head.appendChild(node("span", "carexpert-score", scoreLabel(result)));
    var title = node("span", "carexpert-text");
    title.appendChild(node("b", "carexpert-verdict", VERDICTS[result.verdict] || "A ESTIMER"));
    title.appendChild(node("span", "carexpert-market", marketLine(result)));
    head.appendChild(title);
    var fold = node("button", "carexpert-fold", state.collapsed ? "+" : "-");
    head.appendChild(fold);
    box.appendChild(head);

    var body = node("div", "carexpert-body");
    if (state.collapsed) body.hidden = true;
    fold.addEventListener("click", function () {
      state.collapsed = !state.collapsed;
      body.hidden = state.collapsed;
      fold.textContent = state.collapsed ? "+" : "-";
    });

    if (result.fair_price_eur && result.net_gain_eur > 0) {
      var gain = "Gain estime " + money(result.net_gain_eur);
      body.appendChild(node("p", "carexpert-gain-line", gain));
    }
    if (!result.fair_price_eur) body.appendChild(basisNote(result));
    if (result.headline) body.appendChild(node("p", "carexpert-headline", result.headline));

    (result.factors || []).slice(0, 5).forEach(function (factor) {
      var line = node("div", "carexpert-factor");
      var points = Math.round(factor.points);
      line.appendChild(
        node("span", "carexpert-points " + (points >= 0 ? "carexpert-up" : "carexpert-down"),
          (points > 0 ? "+" : "") + points)
      );
      line.appendChild(node("span", "carexpert-label", factor.label));
      body.appendChild(line);
    });

    if ((result.red_flags || []).length) {
      body.appendChild(node("h4", "carexpert-h4", "Points d'alerte"));
      var flags = node("ul", "carexpert-list");
      result.red_flags.slice(0, 5).forEach(function (flag) {
        flags.appendChild(node("li", null, flag.label));
      });
      body.appendChild(flags);
    }

    if ((result.questions || []).length) {
      body.appendChild(node("h4", "carexpert-h4", "A demander au vendeur"));
      var asks = node("ul", "carexpert-list");
      result.questions.slice(0, 5).forEach(function (question) {
        asks.appendChild(node("li", null, question));
      });
      body.appendChild(asks);
    }

    var actions = node("div", "carexpert-actions");
    if (state.deep && !result.photos_analyzed) {
      var deep = node("button", "carexpert-button carexpert-primary", "Expertise approfondie");
      deep.addEventListener("click", function () {
        deep.disabled = true;
        deep.textContent = "Claude regarde les photos...";
        send({ type: "deep", id: result.id }).then(function (answer) {
          if (answer && answer.ok && answer.result) {
            state.answer = { kind: "listing", results: [answer.result], ok: true };
            clear();
            panel(answer.result);
          } else {
            deep.disabled = false;
            deep.textContent = "Expertise approfondie";
            hud(answer || { message: "Expertise indisponible." });
          }
        });
      });
      actions.appendChild(deep);
    } else if (result.photos_analyzed) {
      actions.appendChild(
        node("span", "carexpert-note", result.photos_analyzed + " photos examinees")
      );
    }
    var open = node("a", "carexpert-button", "Ouvrir dans CarExpert");
    open.href = dashboard() + "/listing/" + result.id;
    open.target = "_blank";
    open.rel = "noreferrer";
    actions.appendChild(open);
    body.appendChild(actions);

    box.appendChild(body);
    document.body.appendChild(box);
    return box;
  }

  function hud(info) {
    var existing = document.querySelector(".carexpert-hud");
    if (existing) existing.remove();

    var box = node("div", "carexpert-root carexpert-hud");
    box.appendChild(node("span", "carexpert-dot", ""));
    box.appendChild(node("span", "carexpert-hud-text", info.message || ""));

    var toOpen = info.open || [];
    if (toOpen.length) box.appendChild(openButton(toOpen));

    if (!state.busy) {
      var again = node("button", "carexpert-button", "Analyser");
      again.addEventListener("click", function () {
        state.retried = false;
        analyse();
      });
      box.appendChild(again);
    }
    document.body.appendChild(box);
  }

  /* Le descriptif n'existe que sur la fiche, et c'est la que sont les
   * pieges. Plutot que d'aller les chercher - ce que l'extension ne fait
   * pas - elle dit lesquelles valent le clic, et les ouvre a la demande. */
  function openButton(urls) {
    var button = node("button", "carexpert-button carexpert-primary",
                      "Ouvrir les " + urls.length + " meilleures");
    button.title = "Le descriptif n'est que sur la fiche de l'annonce: " +
                   "c'est la que se trouvent les pieges.";
    button.addEventListener("click", function () {
      button.disabled = true;
      button.textContent = "Ouverture...";
      send({ type: "open", urls: urls }).then(function (answer) {
        if (answer && answer.ok) {
          button.textContent = answer.opened + " ouvertes, lecture en cours";
        } else {
          button.disabled = false;
          button.textContent = "Ouvrir les " + urls.length + " meilleures";
        }
      });
    });
    return button;
  }

  /* Les annonces qui valent d'etre ouvertes: celles qu'on sait situer sur le
   * marche - sinon "les meilleures" ne veut rien dire - et dont on n'a pas
   * encore lu le descriptif. */
  function worthOpening(results) {
    return results
      .filter(function (item) { return item.fair_price_eur && !item.has_detail; })
      .sort(function (a, b) { return (b.score || 0) - (a.score || 0); })
      .slice(0, 3)
      .map(function (item) { return item.url; });
  }

  function paint(answer) {
    var results = answer.results || [];
    if (answer.kind === "listing" && results.length) {
      panel(results[0]);
    } else {
      results.forEach(function (result) {
        var card = findCard(result);
        if (!card || card.hasAttribute(MARK)) return;
        card.setAttribute(MARK, result.source_id || String(result.id));
        card.insertBefore(badge(result), card.firstChild);
      });
    }
    var placed = document.querySelectorAll(".carexpert-badge").length;
    var great = results.filter(function (item) {
      return item.verdict === "grab";
    }).length;
    var message = answer.message || "";
    var toOpen = [];
    if (answer.kind === "search") {
      if (great) message += " " + great + " a saisir.";
      if (results.length && !placed) {
        message += " Cartes non reconnues: voir le tableau de bord.";
      }
      toOpen = worthOpening(results);
    }
    hud({ message: message, open: toOpen });
  }

  // --- Reglages et cycle de vie -------------------------------------------

  var dashboardUrl = "http://127.0.0.1:8000";

  function dashboard() {
    return dashboardUrl;
  }

  async function start() {
    var config = await send({ type: "settings" });
    if (config && config.server) dashboardUrl = config.server.replace(/\/+$/, "");
    var status = await send({ type: "status" });
    state.deep = !!(status && status.deep);
    if (!status || !status.ok) {
      hud(status || { message: "CarExpert ne repond pas." });
      return;
    }
    if (config && config.auto === false) {
      hud({ message: "Analyse automatique desactivee." });
      return;
    }
    analyse();
  }

  /* Ces sites changent de page sans la recharger: sans cela, la deuxieme
   * page de resultats resterait celle d'avant, annotee de travers. */
  function watchUrl() {
    setInterval(function () {
      if (location.href !== state.url && !state.busy) {
        state.url = location.href;
        state.retried = false;
        clear();
        analyse();
      }
    }, 1500);
  }

  api.runtime.onMessage.addListener(function (message, sender, sendResponse) {
    if (message && message.type === "analyse-now") {
      state.retried = false;
      analyse();
      sendResponse({ ok: true });
    }
    return false;
  });

  watchUrl();
  start();
})();
