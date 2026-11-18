// Relays player reports from the content scripts to Default Island, and
// reports the browser's downloads on its own.
const BRIDGE_URL = "http://127.0.0.1:28491/";
const PROTOCOL_VERSION = 3;
// The name is kept from the SoundCloud-only version (see content.js).
const MESSAGE_TYPE = "soundcloud-state";
// A player report is a few hundred characters; anything far beyond is not one.
const MAX_REPORT_LENGTH = 8 * 1024;

// ---------- Player reports from pages ----------

// The sites the content scripts are declared for in the manifest, e.g.
// "https://*.bandcamp.com/*" -> { host: "bandcamp.com", subdomains: true }.
const PAGE_HOSTS = [];
for (const script of chrome.runtime.getManifest().content_scripts || []) {
  for (const pattern of script.matches || []) {
    const match = /^https:\/\/(\*\.)?([^/*]+)\/\*$/.exec(pattern);
    if (match) {
      PAGE_HOSTS.push({ host: match[2].toLowerCase(), subdomains: Boolean(match[1]) });
    }
  }
}

function isListedPage(url) {
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.protocol !== "https:") {
    return false;
  }
  const hostname = parsed.hostname.toLowerCase();
  return PAGE_HOSTS.some(
    ({ host, subdomains }) =>
      hostname === host || (subdomains && hostname.endsWith(`.${host}`)),
  );
}

// Only this extension's own content script, running in a tab on one of the
// listed sites, may send anything.
function fromListedTab(sender) {
  return sender.id === chrome.runtime.id && Boolean(sender.tab) && isListedPage(sender.url || "");
}

// The island asked the page to open a window (SoundCloud's "Add to
// playlist"): bring that tab and its browser window to the front. Only the
// sending tab itself can be shown, at most once a second.
const SHOW_TAB_TYPE = "default-island-show-tab";
const SHOW_TAB_MIN_GAP_MS = 1000;
let lastShowTabAt = 0;

chrome.runtime.onMessage.addListener((message, sender) => {
  if (message?.type !== SHOW_TAB_TYPE || !fromListedTab(sender)) {
    return false;
  }
  const now = Date.now();
  if (now - lastShowTabAt < SHOW_TAB_MIN_GAP_MS || typeof sender.tab.id !== "number") {
    return false;
  }
  lastShowTabAt = now;
  chrome.tabs.update(sender.tab.id, { active: true }).catch(() => {});
  chrome.windows.update(sender.tab.windowId, { focused: true }).catch(() => {});
  return false;
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message?.type !== MESSAGE_TYPE || !fromListedTab(sender)) {
    return false;
  }
  const state = message.state;
  // A page report never has a "kind": that is how the island tells the
  // worker's own messages (downloads) from a page's.
  if (!state || typeof state !== "object" || Array.isArray(state) || "kind" in state) {
    return false;
  }
  let body;
  try {
    body = JSON.stringify(state);
  } catch {
    return false;
  }
  if (body.length > MAX_REPORT_LENGTH) {
    return false;
  }

  fetch(BRIDGE_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body,
  })
    .then((response) => {
      if (!response.ok) {
        throw new Error(`Bridge returned HTTP ${response.status}`);
      }
      return response.json();
    })
    .then(sendResponse)
    .catch((error) => {
      sendResponse({ commands: [], error: error.message });
    });

  return true;
});

// ---------- Downloads ----------
//
// While at least one download is in progress the worker asks the browser for
// the list once a second and posts it to the island; when none are left it
// posts one empty list and stops.
//
// The browser suspends an idle service worker after about 30 seconds, and
// the downloads API has no event for progress. What keeps the reports
// flowing is the loop itself: every round calls chrome.downloads.search, and
// each extension API call restarts the worker's idle countdown. The loop is
// started by the worker waking up (browser start, a page report) and by the
// download events, which wake a suspended worker. Once nothing is
// downloading the loop ends, nothing restarts the countdown, and the worker
// is suspended as usual.

const DOWNLOADS_INTERVAL_MS = 1000;
// The island is not running: look for it less often.
const DOWNLOADS_OFFLINE_INTERVAL_MS = 5000;
const DOWNLOADS_POST_TIMEOUT_MS = 4000;
const MAX_DOWNLOAD_ITEMS = 20;
const MAX_NAME_LENGTH = 200;

let downloadsTimer = null;
let downloadsRunning = false;
let downloadsAgain = false;
// True while the island may still be showing downloads. It starts true so
// that a worker that was restarted clears whatever was reported before.
let downloadsShown = true;

function downloadName(item) {
  // The full path stays in the browser: only the file's name is reported.
  let name = String(item.filename || "").split(/[\\/]/).pop();
  if (!name) {
    try {
      const path = new URL(item.finalUrl || item.url).pathname;
      name = decodeURIComponent(path.split("/").pop() || "");
    } catch {
      name = "";
    }
  }
  return name.slice(0, MAX_NAME_LENGTH);
}

function describeDownload(item) {
  return {
    id: item.id,
    name: downloadName(item),
    received: Math.max(0, Number(item.bytesReceived) || 0),
    total: Math.max(0, Number(item.totalBytes) || 0),
    paused: Boolean(item.paused),
  };
}

async function postDownloads(items) {
  try {
    const response = await fetch(BRIDGE_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ v: PROTOCOL_VERSION, kind: "downloads", items }),
      signal: AbortSignal.timeout(DOWNLOADS_POST_TIMEOUT_MS),
    });
    return response.ok;
  } catch {
    return false; // The island is not running.
  }
}

function scheduleDownloads(delay) {
  if (downloadsTimer === null) {
    downloadsTimer = setTimeout(reportDownloads, delay);
  }
}

async function reportDownloads() {
  clearTimeout(downloadsTimer);
  downloadsTimer = null;
  if (downloadsRunning) {
    downloadsAgain = true;
    return;
  }
  downloadsRunning = true;
  let nextDelay = null;
  try {
    const active = await chrome.downloads.search({ state: "in_progress" });
    if (active.length > 0) {
      active.sort((a, b) => String(a.startTime).localeCompare(String(b.startTime)));
      const delivered = await postDownloads(
        active.slice(-MAX_DOWNLOAD_ITEMS).map(describeDownload),
      );
      downloadsShown = downloadsShown || delivered;
      nextDelay = delivered ? DOWNLOADS_INTERVAL_MS : DOWNLOADS_OFFLINE_INTERVAL_MS;
    } else if (downloadsShown) {
      // The last one finished or was cancelled. If the island is not there
      // to hear it, it has nothing left to clear either.
      downloadsShown = false;
      await postDownloads([]);
    }
  } catch {
    nextDelay = DOWNLOADS_OFFLINE_INTERVAL_MS;
  }
  downloadsRunning = false;
  if (downloadsAgain) {
    // Something changed while this round was under way.
    downloadsAgain = false;
    nextDelay = 0;
  }
  if (nextDelay !== null) {
    scheduleDownloads(nextDelay);
  }
}

chrome.downloads.onCreated.addListener(() => {
  reportDownloads();
});

chrome.downloads.onChanged.addListener((change) => {
  // Finished, failed, cancelled, paused, resumed or named: report at once.
  if (change.state || change.paused || change.filename || change.totalBytes) {
    reportDownloads();
  }
});

reportDownloads();
