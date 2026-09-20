/* The only part allowed to talk to CarExpert.
 *
 * A content script runs inside the site's page, so the site's own security
 * policy decides who it may call - and leboncoin's forbids an unknown
 * address. The service worker is not the page: it carries the extension's
 * host permissions instead, which is exactly why this file exists.
 */

chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  if (message.kind !== "capture") return false;

  fetch(`${message.endpoint}/api/capture`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url: message.url, html: message.html }),
  })
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then(respond)
    .catch((error) => respond({ error: String(error) }));

  // true: la reponse arrive plus tard. Sans ca, le canal se ferme avant.
  return true;
});

// Pas d'ecouteur `action.onClicked` ici: le manifeste declare un
// `default_popup`, et Chrome n'emet alors jamais cet evenement. La relance
// manuelle passe par popup.js, qui envoie le meme message.
