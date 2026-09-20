/* Annotate the adverts on the page the person is already looking at.
 *
 * The extension never fetches a listing. It reads the page the site served
 * to this visitor, hands it to CarExpert running on their own machine, and
 * puts one badge per advert back onto the cards. No crawling, no protection
 * worked around: the site already decided to show this page to a human.
 */

const ENDPOINT_KEY = "carexpertEndpoint";
const DEFAULT_ENDPOINT = "http://127.0.0.1:8000";

/* An advert's own identifier is the only reliable bridge between what
 * CarExpert read and what is on screen. Class names change at every deploy;
 * the id in the href does not, because the site routes on it. */
function identifiersIn(href) {
  const found = [];
  const uuid = href.match(
    /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i
  );
  if (uuid) found.push(uuid[0].toLowerCase());
  const tail = href.split("?")[0].split("#")[0].replace(/\/$/, "").split("/").pop() || "";
  const stem = tail.replace(/\.[a-z]{2,5}$/i, "");
  if (stem) found.push(stem);
  for (const run of href.match(/\d{5,}/g) || []) found.push(run);
  return found;
}

/* The card is whichever ancestor holds the whole advert. Rather than naming
 * a class, climb until the block is big enough to be a card and no bigger:
 * a rule that survives a redesign. */
function cardOf(anchor) {
  let node = anchor;
  for (let depth = 0; depth < 6 && node.parentElement; depth += 1) {
    node = node.parentElement;
    const box = node.getBoundingClientRect();
    if (box.height >= 80 && box.width >= 180) return node;
  }
  return anchor;
}

const LABELS = {
  grab: { text: "A SAISIR", tone: "grab" },
  check: { text: "A VERIFIER", tone: "check" },
  avoid: { text: "A FUIR", tone: "avoid" },
  unknown: { text: "A ESTIMER", tone: "unknown" },
};

function badgeFor(verdict) {
  const style = LABELS[verdict.verdict] || LABELS.unknown;
  const badge = document.createElement("div");
  badge.className = `carexpert-badge carexpert-${style.tone}`;

  const head = document.createElement("span");
  head.className = "carexpert-head";
  head.textContent = `${style.text} · ${verdict.score}`;
  badge.appendChild(head);

  const detail = document.createElement("span");
  detail.className = "carexpert-detail";
  if (verdict.verdict === "unknown" || !verdict.fair_price_eur) {
    // Un ecart s'affiche seulement quand on le tient. Une pastille grise
    // suivie de "12,4 % sous le marche" se lit comme une bonne affaire: le
    // chiffre l'emporte sur le mot, et il vient d'un echantillon qu'on
    // vient justement de juger trop maigre (invariants 7 et 13).
    detail.textContent = "pas assez de comparables pour situer ce prix";
  } else {
    const sense = verdict.delta_percent >= 0 ? "sous" : "au-dessus du";
    detail.textContent =
      `${Math.abs(verdict.delta_percent)} % ${sense} marche · ` +
      `cote ${verdict.fair_price_eur.toLocaleString("fr-FR")} EUR`;
  }
  badge.appendChild(detail);
  return badge;
}

function annotate(verdicts) {
  const byIdentifier = new Map();
  for (const verdict of verdicts) {
    byIdentifier.set(String(verdict.source_id).toLowerCase(), verdict);
  }

  let placed = 0;
  for (const anchor of document.querySelectorAll("a[href]")) {
    const href = anchor.getAttribute("href") || "";
    const match = identifiersIn(href)
      .map((id) => byIdentifier.get(id.toLowerCase()))
      .find(Boolean);
    if (!match) continue;

    const card = cardOf(anchor);
    if (card.dataset.carexpert === String(match.source_id)) continue;
    card.dataset.carexpert = String(match.source_id);
    card.classList.add("carexpert-card", `carexpert-card-${match.verdict}`);
    card.appendChild(badgeFor(match));
    placed += 1;
  }
  return placed;
}

function banner(message, tone) {
  document.querySelector(".carexpert-banner")?.remove();
  const bar = document.createElement("div");
  bar.className = `carexpert-banner carexpert-banner-${tone}`;
  bar.textContent = message;
  bar.addEventListener("click", () => bar.remove());
  document.body.appendChild(bar);
  if (tone === "ok") setTimeout(() => bar.remove(), 6000);
}

async function run() {
  const stored = await chrome.storage.local.get(ENDPOINT_KEY);
  const endpoint = stored[ENDPOINT_KEY] || DEFAULT_ENDPOINT;

  banner("CarExpert lit cette page...", "busy");
  let answer;
  try {
    answer = await chrome.runtime.sendMessage({
      kind: "capture",
      endpoint,
      url: location.href,
      html: document.documentElement.outerHTML,
    });
  } catch (error) {
    // Le canal se rompt des que le service worker est evince, ou apres un
    // rechargement de l'extension - courant quand on l'installe a la main.
    // Sans ce filet, la banniere "lit cette page..." reste sur le site pour
    // toujours: seules les bannieres vertes s'effacent seules.
    banner(`CarExpert: ${error}. Rechargez la page.`, "error");
    return;
  }

  if (!answer || answer.error) {
    banner(
      `CarExpert injoignable sur ${endpoint}. Lancez "carexpert serve".`,
      "error"
    );
    return;
  }
  if (!answer.count) {
    banner("Aucune annonce reconnue sur cette page.", "error");
    return;
  }
  const placed = annotate(answer.verdicts);
  banner(
    `${answer.count} annonces lues, ${placed} marquees sur la page.`,
    "ok"
  );
}

chrome.runtime.onMessage.addListener((message) => {
  if (message.kind === "run") run();
});

// Une page de resultats se charge par morceaux: attendre que le premier lot
// soit rendu evite de marquer une page a moitie vide.
if (document.readyState === "complete") {
  setTimeout(run, 1200);
} else {
  window.addEventListener("load", () => setTimeout(run, 1200));
}
