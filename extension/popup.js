const KEY = "carexpertEndpoint";
const field = document.getElementById("endpoint");

chrome.storage.local.get(KEY).then((stored) => {
  field.value = stored[KEY] || "http://127.0.0.1:8000";
});

document.getElementById("run").addEventListener("click", async () => {
  await chrome.storage.local.set({ [KEY]: field.value.trim().replace(/\/$/, "") });
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  chrome.tabs.sendMessage(tab.id, { kind: "run" });
  window.close();
});
