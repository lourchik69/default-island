// Isolated-world content script: reads the page's player state and relays it
// to Default Island through the service worker.
//
// One script serves every site in the manifest. What differs between sites
// lives in the SITES table below; everything a site does not describe falls
// back to the generic adapter:
//   title/artist/artwork - navigator.mediaSession.metadata (read by page_hook.js)
//   playing/position     - the page's dominant media element (page_hook.js)
//   next/previous/seek   - the handlers the page registered with
//                          navigator.mediaSession.setActionHandler
//
// Where SoundCloud's values come from (unchanged):
//   playing   - the player bar's play button (what the listener sees), so a
//               track change does not flash "paused" on the island.
//   position  - page_hook.js (the real <audio> element); if the hook is not
//               available, the progress bar's aria-valuenow / aria-valuemax.
//   title     - the player bar's sound badge, then the Media Session metadata.
//
// Pacing: the island keeps every report open for a moment and answers as soon
// as it has a command (a long poll), and the next report leaves when the
// answer arrives. No page timer is involved, because the browser slows page
// timers down to once a second or less while the tab is in the background,
// which would make the island's buttons react late.

const PROTOCOL_VERSION = 3;
const SOURCE_PAGE = "default-island-page";
const SOURCE_CONTENT = "default-island-content";
// The service worker checks this name. It is kept from the SoundCloud-only
// version because the browser may still run that version's cached worker.
const MESSAGE_TYPE = "soundcloud-state";
const SHOW_TAB_TYPE = "default-island-show-tab";
const HOOK_STALE_MS = 1500;
const MAX_TEXT_LENGTH = 300;
const MAX_ART_LENGTH = 500;
// Never report more often than this, even if the island answers instantly.
const MIN_POLL_GAP_MS = 150;
const MIN_URGENT_GAP_MS = 40;
// The island is not running: check for it again this often.
const OFFLINE_RETRY_MS = 2000;
// A report that got no answer for this long is considered lost.
const POLL_TIMEOUT_MS = 5000;
const WATCHDOG_INTERVAL_MS = 1000;

// How long the previous track's audio may linger after a track change.
const STALE_MEDIA_MS = 1500;

// Identifies this tab, so the island can tell two player tabs apart.
const TAB_ID = Math.random().toString(36).slice(2, 10);

let hookState = null;
let hookStateAt = 0;
let sequence = 0;
let lastTitle = "";
let staleMediaDuration = null;
let staleMediaUntil = 0;
let pollInFlight = false;
let pollStartedAt = 0;
let pollTimer = null;
let lastUrgentAt = 0;
let urgentTimer = null;
let offlineUntil = 0;
let watchdogTimer = null;
let observedControl = null;
let controlObserver = null;
let stopped = false;

// ---------- Reading the page: helpers ----------

function find(selector) {
  if (!selector) {
    return null;
  }
  try {
    return document.querySelector(selector);
  } catch (error) {
    return null;
  }
}

// The first match that is laid out, else the first match. Sites often keep a
// hidden copy of a button for another layout.
function findVisible(selector) {
  if (!selector) {
    return null;
  }
  let matches;
  try {
    matches = document.querySelectorAll(selector);
  } catch (error) {
    return null;
  }
  for (const element of matches) {
    if (element.getClientRects().length > 0) {
      return element;
    }
  }
  return matches[0] || null;
}

function limitText(value) {
  return typeof value === "string" ? value.trim().slice(0, MAX_TEXT_LENGTH) : "";
}

// Text from a page's markup: line breaks and runs of spaces become one space.
function cleanText(value) {
  return typeof value === "string" ? limitText(value.replace(/\s+/g, " ")) : "";
}

function readText(selectors) {
  for (const selector of selectors) {
    const element = find(selector);
    const value = element?.getAttribute("title") || element?.textContent || "";
    const text = value.trim();
    if (text) {
      return text.slice(0, MAX_TEXT_LENGTH);
    }
  }
  return "";
}

// Visible text only (some sites put a different text in the title attribute).
function readPlainText(selectors) {
  for (const selector of selectors) {
    const text = cleanText(findVisible(selector)?.textContent);
    if (text) {
      return text;
    }
  }
  return "";
}

function isDisabled(element) {
  return (
    element.disabled === true ||
    element.getAttribute("aria-disabled") === "true" ||
    element.classList.contains("disabled")
  );
}

function clickFirst(selector) {
  const element = findVisible(selector);
  if (!element || isDisabled(element)) {
    return false;
  }
  element.click();
  return true;
}

// Toggle buttons say whether they are on with aria-pressed or aria-checked.
function ariaOn(element) {
  const value = element.getAttribute("aria-pressed") ?? element.getAttribute("aria-checked");
  if (value === "true") return true;
  if (value === "false") return false;
  return null;
}

function artUrl(value) {
  return typeof value === "string" && value.startsWith("https://") && value.length <= MAX_ART_LENGTH
    ? value
    : "";
}

function cssImageUrl(element) {
  const match = /url\(["']?([^"')]+)["']?\)/.exec(element?.style?.backgroundImage || "");
  return match ? match[1] : "";
}

// ---------- SoundCloud ----------

function scPlayControl() {
  return find(".playControls .playControl") || find(".playControl");
}

// SoundCloud renders "<span class=sc-visuallyhidden>Current time: 1:23</span>
// <span aria-hidden=true>1:23</span>", so textContent is not a bare time.
function scParseTime(selector) {
  const element = find(selector);
  if (!element) {
    return null;
  }
  const visible = element.querySelector('[aria-hidden="true"]');
  const text = (visible || element).textContent || "";
  const matches = text.match(/\d{1,2}(?::\d{2}){1,2}/g);
  if (!matches) {
    return null;
  }
  const parts = matches[matches.length - 1].split(":").map(Number);
  return parts.reduce((seconds, part) => seconds * 60 + part, 0);
}

function scProgressBar() {
  const bar = find(
    '.playbackTimeline__progressWrapper[role="progressbar"], .playbackTimeline [role="progressbar"]',
  );
  if (!bar || !bar.hasAttribute("aria-valuemax")) {
    return { position: null, duration: null };
  }
  const now = Number(bar.getAttribute("aria-valuenow"));
  const max = Number(bar.getAttribute("aria-valuemax"));
  return {
    position: Number.isFinite(now) && now >= 0 ? now : null,
    duration: Number.isFinite(max) && max > 0 ? max : null,
  };
}

function scSeekWithProgressBar(seconds) {
  const bar = find(".playbackTimeline__progressWrapper");
  const { duration } = scProgressBar();
  if (!bar || !duration) {
    return;
  }
  const rect = bar.getBoundingClientRect();
  const ratio = Math.max(0, Math.min(1, seconds / duration));
  const init = {
    bubbles: true,
    cancelable: true,
    clientX: rect.left + rect.width * ratio,
    clientY: rect.top + rect.height / 2,
    button: 0,
  };
  bar.dispatchEvent(new MouseEvent("mousedown", init));
  bar.dispatchEvent(new MouseEvent("mouseup", init));
  bar.dispatchEvent(new MouseEvent("click", init));
}

// Calls back with the first visible element matching `selector`, waiting up
// to `timeout` ms for the page to render it (null if it never appears).
function waitFor(selector, timeout, callback) {
  const started = Date.now();
  const check = () => {
    const element = findVisible(selector);
    if (element && element.getClientRects().length > 0) {
      callback(element);
    } else if (Date.now() - started < timeout) {
      setTimeout(check, 100);
    } else {
      callback(null);
    }
  };
  check();
}

function scAddToPlaylist() {
  const queueOpen = Boolean(find(".playControls .queue.m-visible"));
  if (!queueOpen && !clickFirst(".playControls .playbackSoundBadge__showQueue")) {
    return;
  }
  const closeQueue = () => {
    if (!queueOpen) {
      clickFirst(".playControls .queue__hide");
    }
  };
  waitFor(".playControls .queue .queueItemView.m-active .queueItemView__more", 2000, (more) => {
    if (!more) {
      closeQueue();
      return;
    }
    more.click();
    waitFor(".dropdownMenu .sc-button-addtoset", 1500, (add) => {
      if (add) {
        add.click();
      }
      closeQueue();
    });
  });
}

const SOUNDCLOUD = {
  id: "soundcloud",
  hosts: ["soundcloud.com"],
  // The player bar's badge is the truth; it exists only once a track is loaded.
  title: () => readText([".playbackSoundBadge__titleLink", ".playbackSoundBadge__title"]),
  artist: () => readText([".playbackSoundBadge__lightLink", ".playbackSoundBadge__artist"]),
  titleNeedsMedia: false,
  playing(hook) {
    const control = scPlayControl();
    if (control) {
      return control.classList.contains("playing");
    }
    const sessionState = hook?.session?.state;
    if (sessionState === "playing") {
      return true;
    }
    if (sessionState === "paused") {
      return false;
    }
    return hook ? !hook.paused : null;
  },
  // Right after a track change the bar already shows the new track while the
  // audio element still holds the previous one for a moment.
  staleMedia: true,
  // Whole seconds from the progress bar, used while the hook has no audio.
  timeline() {
    const bar = scProgressBar();
    return {
      duration: bar.duration ?? scParseTime(".playbackTimeline__duration"),
      position: bar.position ?? scParseTime(".playbackTimeline__timePassed"),
    };
  },
  // The play button changes before the audio element does.
  playControl: scPlayControl,
  setPlaying(wanted) {
    const control = scPlayControl();
    if (control && !control.classList.contains("disabled")) {
      if (control.classList.contains("playing") !== wanted) {
        control.click();
      }
      return true;
    }
    return false;
  },
  via: "element",
  next: ".playControls .skipControl__next, .skipControl__next",
  prev: ".playControls .skipControl__previous, .skipControl__previous",
  controlsFirst: true,
  seekFallback: scSeekWithProgressBar,
  // Island modes: 0 = off, 1 = repeat all, 2 = repeat one.
  // SoundCloud marks its button with m-one / m-all (no marker means off).
  repeat: {
    button: ".playControls .repeatControl, .repeatControl",
    read(control) {
      if (control.classList.contains("m-one")) return 2;
      if (control.classList.contains("m-all")) return 1;
      return 0;
    },
  },
  // The heart in the player bar. Signed out, the heart only opens the sign-in
  // window, so no like state is reported then. The "selected" class is how
  // SoundCloud's own scripts and styles mark a pressed button; with a signed-in
  // account it is UNVERIFIED (tests ran signed out).
  like: {
    button: ".playControls .playbackSoundBadge__like",
    on: (button) => button.classList.contains("sc-button-selected"),
    signedOut: ".header__loginMenu",
  },
  // "Add to playlist" for the current track. The player bar has no menu of
  // its own (only like, follow and "Next up"), so it goes through the
  // "Next up" list: the playing entry's "More" menu -> "Add to Playlist".
  // Seen live signed out, where it opens SoundCloud's sign-in window (which
  // continues to the playlist window after signing in); the playlist window
  // itself, signed in, is UNVERIFIED.
  playlist: scAddToPlaylist,
  art(hook, title) {
    // The Media Session artwork (500x500) when it belongs to the track in the
    // bar, else the bar's own thumbnail in the same size.
    if (hook?.session?.art && hook.session.title === title) {
      return hook.session.art;
    }
    const thumbnail = cssImageUrl(find(".playControls .playbackSoundBadge__avatar span.sc-artwork"));
    return thumbnail.replace(/-t\d+x\d+\.(\w+)$/, "-t500x500.$1");
  },
};

// ---------- Other sites ----------
//
// To add a site: add its host patterns to manifest.json (both content script
// entries) and, only if the generic adapter is not enough, an entry here.
// Every field except id and hosts is optional:
//
//   hosts            host names; subdomains match too
//   title(hook), artist(hook), art(hook, title)
//                    read from the page instead of the Media Session
//   titleNeedsMedia  false: a title counts even before any media is loaded
//   requireMedia     true: no media element means nothing is loaded, whatever
//                    the Media Session says
//   playing(hook)    true/false/null, instead of the media element's state
//   blocked()        true while what plays is not the listener's track (an ad)
//   live()           true for a stream without a timeline
//   ignoreMedia      selector of <video> elements that are never the player
//   via              "session" (default): play/pause/seek go through the page's
//                    Media Session handlers when it has them, else the element;
//                    "element": always the media element
//   setPlaying(wanted)  click the site's own button; return true if handled
//   next, prev       selectors of the site's buttons, used when the page has
//                    no Media Session handler (controlsFirst: always)
//   repeat           { button, read(button) -> 0|1|2 }
//   like             { button, on(button) -> bool (default: aria-pressed /
//                    aria-checked), signedOut: selector that exists only for
//                    a visitor without an account }
//   playlist()       open the site's "add to playlist" window for the track
//
// Selectors marked UNVERIFIED could not be exercised with real playback (the
// site needs an account); where noted they were found in the site's public
// page or shipped scripts.

function ytThumbnail() {
  const id = location.pathname === "/watch" ? new URLSearchParams(location.search).get("v") : "";
  return id && /^[\w-]{6,20}$/.test(id) ? `https://i.ytimg.com/vi/${id}/hqdefault.jpg` : "";
}

const SITES = [
  SOUNDCLOUD,
  {
    id: "youtube",
    hosts: ["www.youtube.com", "m.youtube.com"],
    // Thumbnail previews on the home and search pages.
    ignoreMedia: "#inline-preview-player video, ytd-video-preview video, #video-preview video",
    // An ad plays in the same <video>; its timeline is not the video's.
    blocked: () => Boolean(find("#movie_player.ad-showing, #movie_player.ad-interrupting")),
    live: () => Boolean(find("#movie_player .ytp-time-display.ytp-live")),
    title: () =>
      location.pathname === "/watch"
        ? readPlainText(["ytd-watch-metadata h1 yt-formatted-string", "ytd-watch-metadata h1"])
        : "",
    artist: () =>
      location.pathname === "/watch"
        ? readPlainText(["ytd-watch-metadata ytd-channel-name a", "#owner ytd-channel-name a"])
        : "",
    // On a watch page the page's own title is used, not the Media Session:
    // the session describes the ad while an ad plays, and the thumbnail
    // preview under the mouse while one of those plays (both seen live).
    art: ytThumbnail,
    // "Video unavailable" still sets a Media Session that says "playing".
    requireMedia: true,
    next: "#movie_player .ytp-next-button",
    prev: "#movie_player .ytp-prev-button",
    like: {
      button:
        "ytd-watch-metadata like-button-view-model button[aria-pressed], " +
        "#top-level-buttons-computed like-button-view-model button[aria-pressed]",
      // Signed out the button only opens a sign-in prompt.
      signedOut: 'ytd-masthead a[href*="ServiceLogin"]',
    },
  },
  {
    id: "bandcamp",
    hosts: ["bandcamp.com"],
    title: () => readPlainText([".inline_player .title", "#name-section .trackTitle"]),
    artist: () => readPlainText(["#name-section h3 span:last-of-type a", "#band-name-location .title"]),
    playing: (hook) => {
      const button = find(".inline_player .playbutton");
      return button ? button.classList.contains("playing") : null;
    },
    playControl: () => find(".inline_player .playbutton"),
    // Through the page's own button: pausing the <audio> element behind the
    // player's back made it start again on the next seek (seen live).
    setPlaying(wanted) {
      const button = find(".inline_player .playbutton");
      if (!button) {
        return false;
      }
      if (button.classList.contains("playing") !== wanted) {
        button.click();
      }
      return true;
    },
    via: "element",
    next: ".inline_player .nextbutton:not(.hiddenelem)",
    prev: ".inline_player .prevbutton:not(.hiddenelem)",
    art: () => find("#tralbumArt a.popupImage")?.href || find("#tralbumArt img")?.src || "",
  },
  {
    id: "ytmusic",
    hosts: ["music.youtube.com"],
    title: () => readPlainText(["ytmusic-player-bar .content-info-wrapper .title"]),
    artist: () => readPlainText(["ytmusic-player-bar .content-info-wrapper .byline a"]),
    blocked: () => Boolean(find("#movie_player.ad-showing")),
    next: "ytmusic-player-bar .next-button",
    prev: "ytmusic-player-bar .previous-button",
    // ytmusic-player-bar carries repeat-mode="NONE" | "ALL" | "ONE".
    repeat: {
      button: "ytmusic-player-bar .repeat",
      read() {
        const mode = find("ytmusic-player-bar")?.getAttribute("repeat-mode");
        return { NONE: 0, ALL: 1, ONE: 2 }[mode] ?? null;
      },
    },
    like: {
      button: "ytmusic-player-bar ytmusic-like-button-renderer #button-shape-like button",
      signedOut: 'ytmusic-nav-bar a[href*="ServiceLogin"]',
    },
  },
  {
    // UNVERIFIED with playback (needs an account). Metadata, position,
    // play/pause, seek and next/previous come from the generic adapter; the
    // data-testid names below are from the shipped web player script.
    id: "spotify",
    hosts: ["open.spotify.com"],
    next: '[data-testid="control-button-skip-forward"]',
    prev: '[data-testid="control-button-skip-back"]',
    like: {
      button: '[data-testid="now-playing-widget"] [data-testid="add-button"]',
      signedOut: '[data-testid="login-button"]',
    },
  },
  // UNVERIFIED with playback (they need an account): the generic adapter
  // only, with no site selectors, so no like state or repeat is reported.
  { id: "yandex", hosts: ["music.yandex.ru", "music.yandex.com", "music.yandex.by", "music.yandex.kz"] },
  { id: "deezer", hosts: ["www.deezer.com"] },
  { id: "applemusic", hosts: ["music.apple.com"] },
  { id: "tidal", hosts: ["listen.tidal.com"] },
  { id: "vk", hosts: ["vk.com", "vk.ru"] },
  {
    id: "twitch",
    hosts: ["www.twitch.tv", "m.twitch.tv"],
  },
];

const GENERIC = { id: "web", hosts: [] };

function siteFor(hostname) {
  const host = hostname.toLowerCase();
  for (const site of SITES) {
    if (site.hosts.some((name) => host === name || host.endsWith(`.${name}`))) {
      return site;
    }
  }
  return GENERIC;
}

const site = siteFor(window.location.hostname);

// ---------- Reading the page ----------

function number(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

// The hook's messages travel through the page, so the page could forge them:
// keep only the expected fields, in the expected types.
function cleanHookState(data) {
  const session = data.session && typeof data.session === "object" ? data.session : {};
  const position = number(data.pos);
  const duration = number(data.dur);
  const hasMedia = data.hasMedia === true && position !== null && duration !== null && duration > 0;
  const rate = number(data.rate);
  return {
    urgent: data.urgent === true,
    hasMedia,
    paused: data.paused !== false,
    ticking: hasMedia && data.ticking === true,
    pos: hasMedia ? position : null,
    dur: hasMedia ? duration : null,
    rate: rate !== null && rate > 0 ? rate : 1,
    at: number(data.at) ?? Date.now(),
    present: data.present === true,
    live: data.live === true,
    active: data.active === true,
    heard: data.heard === true,
    settling: data.settling === true,
    actions: Array.isArray(data.actions)
      ? data.actions.filter((name) => typeof name === "string").slice(0, 8)
      : [],
    config: number(data.config) ?? 0,
    session: {
      state: typeof session.state === "string" ? session.state : "none",
      title: limitText(session.title),
      artist: limitText(session.artist),
      album: limitText(session.album),
      art: artUrl(session.art),
    },
  };
}

// The hook's last message, if it is recent. Its Media Session data stays
// useful even when no audio is loaded.
function recentHookState() {
  return hookState && Date.now() - hookStateAt < HOOK_STALE_MS ? hookState : null;
}

function freshHookState() {
  if (hookState && hookState.hasMedia && Date.now() - hookStateAt < HOOK_STALE_MS) {
    return hookState;
  }
  return null;
}

function sendToPage(message) {
  window.postMessage({ source: SOURCE_CONTENT, ...message }, window.location.origin);
}

function hookCan(action) {
  return Boolean(recentHookState()?.actions.includes(action));
}

function readPlaying(hook) {
  if (site.playing) {
    const playing = site.playing(hook);
    if (playing !== null) {
      return playing;
    }
  }
  if (!hook) {
    return null;
  }
  const sessionState = hook.session.state;
  if (hook.present) {
    if (hook.active) {
      return true;
    }
    // Between two tracks the element is paused for a moment while the page
    // still says "playing": do not flash a pause on the island.
    return hook.settling && sessionState === "playing";
  }
  if (sessionState === "playing") return true;
  if (sessionState === "paused") return false;
  return null;
}

function readRepeatMode() {
  const control = find(site.repeat?.button);
  if (!control) {
    return null;
  }
  const mode = site.repeat.read(control);
  return mode === 0 || mode === 1 || mode === 2 ? mode : null;
}

// The site's like button and whether it is on; null if the site has none for
// the current track or its state cannot be read (for example signed out).
function readLike() {
  const spec = site.like;
  if (!spec || (spec.signedOut && find(spec.signedOut))) {
    return null;
  }
  const button = findVisible(spec.button);
  if (!button || isDisabled(button)) {
    return null;
  }
  const on = spec.on ? spec.on(button) : ariaOn(button);
  return typeof on === "boolean" ? { button, on } : null;
}

// Returns the hook state only when its audio belongs to the track called
// `title` (see staleMedia in the SoundCloud entry).
function mediaForTrack(title) {
  const hook = freshHookState();
  if (title !== lastTitle) {
    if (lastTitle && hook) {
      staleMediaDuration = hook.dur;
      staleMediaUntil = Date.now() + STALE_MEDIA_MS;
    }
    lastTitle = title;
  }
  if (hook && staleMediaDuration !== null) {
    if (Date.now() < staleMediaUntil && hook.dur === staleMediaDuration) {
      return null;
    }
    staleMediaDuration = null;
  }
  return hook;
}

function readTitle(recentHook, blocked) {
  // During an ad the Media Session describes the ad, not the listener's track.
  const sessionTitle = blocked ? "" : recentHook?.session.title || "";
  // Media that is playing or has played with sound, not merely an element
  // that happens to be on the page.
  const mediaLoaded = Boolean(recentHook?.present && (recentHook.active || recentHook.heard));
  const fromPage = site.title ? limitText(site.title(recentHook)) : "";
  if (site.titleNeedsMedia === false) {
    return fromPage || sessionTitle;
  }
  if (site.requireMedia && !recentHook?.present) {
    return "";
  }
  // A title alone does not mean a player: a page of the site that plays
  // nothing would otherwise take over the island with a "paused" track.
  if (!mediaLoaded && !sessionTitle) {
    return "";
  }
  if (fromPage || sessionTitle) {
    return fromPage || sessionTitle;
  }
  // A playing element the page says nothing about: the tab's title. During
  // an ad, the track that was playing before it.
  return blocked ? lastTitle : cleanText(document.title);
}

function readArtist(recentHook, blocked) {
  const sessionArtist = blocked ? "" : recentHook?.session.artist || "";
  const fromPage = site.artist ? limitText(site.artist(recentHook)) : "";
  return fromPage || sessionArtist;
}

function getSnapshot() {
  const recentHook = recentHookState();
  const blocked = Boolean(site.blocked?.());
  const title = readTitle(recentHook, blocked);
  sequence += 1;
  const snapshot = {
    v: PROTOCOL_VERSION,
    site: site.id,
    tab: TAB_ID,
    seq: sequence,
    title,
  };
  if (!title) {
    // Nothing is loaded in the player yet.
    return snapshot;
  }
  snapshot.artist = readArtist(recentHook, blocked);

  const playing = readPlaying(recentHook);
  if (playing !== null) {
    snapshot.playing = playing;
  }

  const live = Boolean(recentHook?.live) || Boolean(site.live?.());
  const hook = site.staleMedia ? mediaForTrack(title) : freshHookState();
  if (!site.staleMedia) {
    lastTitle = title;
  }
  if (blocked || live) {
    // An ad or a live stream: there is no position worth showing.
    snapshot.ticking = false;
  } else if (hook) {
    // Add the time that passed since the hook measured the position.
    const age = hook.ticking ? ((Date.now() - hook.at) / 1000) * (hook.rate || 1) : 0;
    snapshot.dur = hook.dur;
    snapshot.pos = Math.max(0, Math.min(hook.pos + Math.max(0, age), hook.dur));
    snapshot.ticking = Boolean(hook.ticking) && playing !== false;
  } else {
    const timeline = site.timeline?.();
    if (timeline && timeline.duration > 0 && timeline.position !== null) {
      snapshot.dur = timeline.duration;
      snapshot.pos = Math.max(0, Math.min(timeline.position, timeline.duration));
      // Whole seconds only, so the island must not treat it as exact.
      snapshot.coarse = true;
    }
    // With the hook running but no audio ready (track is loading), the
    // position is not moving yet. Without the hook, trust the play button.
    snapshot.ticking = recentHook ? false : Boolean(playing);
  }

  const repeat = readRepeatMode();
  if (repeat !== null) {
    snapshot.repeat = repeat;
  }
  const like = readLike();
  if (like) {
    snapshot.liked = like.on;
  }
  const sessionArt = blocked ? "" : recentHook?.session.art;
  const art = artUrl(site.art ? site.art(recentHook, title) || sessionArt : sessionArt);
  if (art) {
    snapshot.art = art;
  }
  return snapshot;
}

// ---------- Commands from the island ----------

function setPlaying(wanted) {
  if (site.setPlaying?.(wanted)) {
    return;
  }
  const hook = recentHookState();
  // Leave a player alone that is already in the wanted state: a "play"
  // handler on a playing page is free to mean "toggle".
  if (hook && hook.present && hook.active === wanted) {
    return;
  }
  sendToPage({ type: wanted ? "play" : "pause", via: site.via || "session" });
}

function skip(direction) {
  const selector = site[direction];
  if (site.controlsFirst && selector) {
    clickFirst(selector);
  } else if (hookCan(direction)) {
    sendToPage({ type: direction });
  } else if (selector) {
    clickFirst(selector);
  }
}

function seek(seconds) {
  if (site.blocked?.() || recentHookState()?.live || site.live?.()) {
    return;
  }
  const via = site.via || "session";
  if (freshHookState() || (via === "session" && hookCan("seek"))) {
    sendToPage({ type: "seek", sec: seconds, via });
  } else {
    site.seekFallback?.(seconds);
  }
}

function setRepeatMode(mode, attempt = 0) {
  const control = find(site.repeat?.button);
  const current = readRepeatMode();
  if (!control || current === null || current === mode || attempt >= 3) {
    return;
  }
  control.click();
  setTimeout(() => setRepeatMode(mode, attempt + 1), 120);
}

function setLiked(wanted) {
  const like = readLike();
  if (like && like.on !== wanted) {
    like.button.click();
  }
}

function applyCommand(command) {
  switch (command?.type) {
    case "set_playing":
      setPlaying(Boolean(command.value));
      break;
    case "next":
      skip("next");
      break;
    case "prev":
      skip("prev");
      break;
    case "seek":
      if (Number.isFinite(command.sec)) {
        seek(command.sec);
      }
      break;
    case "repeat":
      if ([0, 1, 2].includes(command.mode)) {
        setRepeatMode(command.mode);
      }
      break;
    case "like":
      if (typeof command.value === "boolean") {
        setLiked(command.value);
      }
      break;
    case "playlist":
      if (site.playlist) {
        // Bring the tab to the front first, so the window opens where the
        // listener is looking.
        showTab();
        site.playlist();
      }
      break;
    default:
      break;
  }
}

function applyCommands(commands) {
  if (!Array.isArray(commands) || commands.length === 0) {
    return false;
  }
  for (const command of commands) {
    try {
      applyCommand(command);
    } catch (error) {
      console.warn("Default Island: could not apply a command", command, error);
    }
  }
  return true;
}

// ---------- Talking to the island ----------

function extensionAlive() {
  try {
    return Boolean(chrome.runtime?.id);
  } catch (error) {
    return false;
  }
}

function stop() {
  // The extension was reloaded or removed and this copy of the script can no
  // longer reach it. The page gets a fresh copy after it is reloaded.
  stopped = true;
  clearInterval(watchdogTimer);
  clearTimeout(pollTimer);
  clearTimeout(urgentTimer);
  controlObserver?.disconnect();
}

// Calls back with the island's answer, or null if it could not be reached.
function send(state, callback) {
  if (!extensionAlive()) {
    stop();
    return;
  }
  try {
    chrome.runtime.sendMessage({ type: MESSAGE_TYPE, state }, (response) => {
      const failed = Boolean(chrome.runtime.lastError) || !response || Boolean(response.error);
      callback(failed ? null : response);
    });
  } catch (error) {
    stop();
  }
}

// Asks the service worker to make this tab the active one and focus its
// window. A worker from before version 3 ignores the request.
function showTab() {
  if (!extensionAlive()) {
    return;
  }
  try {
    chrome.runtime.sendMessage({ type: SHOW_TAB_TYPE }, () => void chrome.runtime.lastError);
  } catch (error) {
    // The extension was reloaded.
  }
}

function poll() {
  clearTimeout(pollTimer);
  pollTimer = null;
  if (stopped || pollInFlight) {
    return;
  }
  watchPlayControl();
  pollInFlight = true;
  pollStartedAt = Date.now();
  const startedAt = pollStartedAt;
  send(getSnapshot(), (response) => {
    if (stopped || startedAt !== pollStartedAt) {
      return; // The watchdog already gave up on this report.
    }
    pollInFlight = false;
    let wait = MIN_POLL_GAP_MS - (Date.now() - startedAt);
    if (response === null) {
      offlineUntil = Date.now() + OFFLINE_RETRY_MS;
      wait = OFFLINE_RETRY_MS;
    } else {
      offlineUntil = 0;
      if (applyCommands(response.commands)) {
        // Confirm the result quickly so the island does not have to guess.
        requestUrgentSend(120);
      }
    }
    if (wait <= 0) {
      poll();
    } else {
      pollTimer = setTimeout(poll, wait);
    }
  });
}

// An extra report outside the regular cycle, answered by the island at once.
// Used when something just changed (play, pause, seek, new track).
function sendUrgent() {
  if (stopped || Date.now() < offlineUntil) {
    return;
  }
  lastUrgentAt = Date.now();
  send({ ...getSnapshot(), urgent: true }, (response) => {
    if (response && applyCommands(response.commands)) {
      requestUrgentSend(120);
    }
  });
}

// Sends right away when possible: a delayed timer would be held back by the
// browser while the tab is in the background.
function requestUrgentSend(delay = 0) {
  if (stopped) {
    return;
  }
  const sinceLast = Date.now() - lastUrgentAt;
  if (delay <= 0 && sinceLast >= MIN_URGENT_GAP_MS) {
    sendUrgent();
    return;
  }
  if (urgentTimer !== null) {
    return;
  }
  urgentTimer = setTimeout(() => {
    urgentTimer = null;
    sendUrgent();
  }, Math.max(delay, MIN_URGENT_GAP_MS - sinceLast, 0));
}

function watchPlayControl() {
  const control = site.playControl ? site.playControl() : null;
  if (control === observedControl) {
    return;
  }
  controlObserver?.disconnect();
  observedControl = control;
  if (!control) {
    return;
  }
  // The button changes before the audio element does, so watching it makes
  // play and pause show up on the island without waiting for the next report.
  controlObserver = new MutationObserver(() => requestUrgentSend());
  controlObserver.observe(control, { attributes: true, attributeFilter: ["class"] });
}

// Tells the hook which elements of this site are never the player.
const HOOK_CONFIG_VERSION = 1;

function configureHook() {
  sendToPage({ type: "config", version: HOOK_CONFIG_VERSION, ignore: site.ignoreMedia || "" });
}

window.addEventListener("message", (event) => {
  const data = event.data;
  if (
    event.source !== window ||
    event.origin !== window.location.origin ||
    !data ||
    typeof data !== "object" ||
    data.source !== SOURCE_PAGE
  ) {
    return;
  }
  hookState = cleanHookState(data);
  hookStateAt = Date.now();
  if (hookState.config !== HOOK_CONFIG_VERSION) {
    configureHook();
  }
  if (hookState.urgent) {
    requestUrgentSend();
  }
});

configureHook();
poll();
// Safety net: restart the cycle if a report was lost along the way.
watchdogTimer = setInterval(() => {
  if (!extensionAlive()) {
    stop();
    return;
  }
  if (pollInFlight && Date.now() - pollStartedAt > POLL_TIMEOUT_MS) {
    pollInFlight = false;
  }
  if (!pollInFlight && pollTimer === null) {
    poll();
  }
}, WATCHDOG_INTERVAL_MS);
