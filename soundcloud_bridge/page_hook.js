// Runs in the page's own JavaScript world (MAIN) at document_start, on every
// site listed in the manifest. It does three things content.js cannot do from
// its isolated world:
//
//   1. Finds the media element that is really playing. Some players (SoundCloud)
//      use an <audio> element that is never attached to the document, so the
//      hook remembers every element the page calls play() on, and also picks
//      up the <video>/<audio> elements that are in the document.
//   2. Reads navigator.mediaSession (title, artist, artwork, playback state).
//   3. Remembers the handlers the page registers with
//      navigator.mediaSession.setActionHandler, so "next", "previous", "seek",
//      "play" and "pause" can be asked of any site the same way the keyboard's
//      media keys would ask.
//
// The hook only ever talks to its own window: it reports with postMessage and
// accepts a fixed set of commands, each of which controls this page's player
// and nothing else.
(() => {
  if (window.__defaultIslandHook) {
    return;
  }
  window.__defaultIslandHook = true;

  const SOURCE_PAGE = "default-island-page";
  const SOURCE_CONTENT = "default-island-content";
  const REPORT_INTERVAL_MS = 250;
  // Look for new <video>/<audio> elements in the document this often.
  const SCAN_EVERY_REPORTS = 4;
  const HAVE_FUTURE_DATA = 3;
  const MAX_TEXT_LENGTH = 300;
  const MAX_ART_LENGTH = 500;
  const MAX_SELECTOR_LENGTH = 400;
  // A silent video narrower than this share of the window, that was never
  // heard, is treated as a preview (thumbnail hover, animated cover).
  const BIG_VIDEO_SHARE = 0.45;
  // State changes that must reach the island right away instead of waiting
  // for the next periodic report.
  const MEDIA_EVENTS = [
    "play",
    "pause",
    "playing",
    "waiting",
    "seeked",
    "ended",
    "emptied",
    "durationchange",
    "ratechange",
  ];
  // Media Session actions worth remembering, by the name content.js uses.
  const SESSION_ACTIONS = {
    nexttrack: "next",
    previoustrack: "prev",
    seekto: "seek",
    play: "play",
    pause: "pause",
  };

  const tracked = new WeakSet();
  const heard = new WeakSet(); // elements that have played with sound
  const startOrder = new WeakMap(); // element -> when it last started
  let known = []; // WeakRef of every element seen so far
  let startCounter = 0;
  let current = null;
  let reportCount = 0;
  // Set by content.js for the site: elements to leave alone, and the element
  // that is the site's main player when several are on the page.
  let ignoreSelector = "";
  let configVersion = 0;
  const sessionHandlers = Object.create(null);

  // ---------- Media Session ----------

  function pickArtwork(metadata) {
    let best = "";
    let bestArea = -1;
    try {
      for (const image of metadata.artwork || []) {
        const src = String((image && image.src) || "");
        if (!src.startsWith("https://") || src.length > MAX_ART_LENGTH) {
          continue;
        }
        // "512x512", also SoundCloud's "t500x500". No size: keep the order.
        const size = /(\d+)x(\d+)/.exec(String(image.sizes || ""));
        const area = size ? Number(size[1]) * Number(size[2]) : 0;
        if (area > bestArea) {
          best = src;
          bestArea = area;
        }
      }
    } catch (error) {
      return "";
    }
    return best;
  }

  function readSession() {
    try {
      const session = navigator.mediaSession;
      const metadata = session && session.metadata;
      return {
        state: session ? session.playbackState : "none",
        title: String((metadata && metadata.title) || "").slice(0, MAX_TEXT_LENGTH),
        artist: String((metadata && metadata.artist) || "").slice(0, MAX_TEXT_LENGTH),
        album: String((metadata && metadata.album) || "").slice(0, MAX_TEXT_LENGTH),
        art: metadata ? pickArtwork(metadata) : "",
      };
    } catch (error) {
      return { state: "none", title: "", artist: "", album: "", art: "" };
    }
  }

  try {
    const prototype = window.MediaSession && window.MediaSession.prototype;
    const originalSetActionHandler = prototype && prototype.setActionHandler;
    if (typeof originalSetActionHandler === "function") {
      prototype.setActionHandler = function (action, handler) {
        const result = originalSetActionHandler.apply(this, arguments);
        try {
          if (Object.prototype.hasOwnProperty.call(SESSION_ACTIONS, action)) {
            sessionHandlers[action] = typeof handler === "function" ? handler : null;
          }
        } catch (error) {
          // Never let the hook break the page.
        }
        return result;
      };
    }
  } catch (error) {
    // No Media Session in this browser: the media element is still enough.
  }

  function sessionActions() {
    const names = [];
    for (const action of Object.keys(SESSION_ACTIONS)) {
      if (typeof sessionHandlers[action] === "function") {
        names.push(SESSION_ACTIONS[action]);
      }
    }
    return names;
  }

  // Calls the handler the page itself registered. True if there was one.
  function callSession(action, details) {
    const handler = sessionHandlers[action];
    if (typeof handler !== "function") {
      return false;
    }
    try {
      handler({ action, ...details });
      return true;
    } catch (error) {
      return false;
    }
  }

  // ---------- Choosing the media element ----------

  function isSilent(media) {
    return media.muted || media.volume === 0;
  }

  function isPlaying(media) {
    return !media.paused && !media.ended;
  }

  function isBigVideo(media) {
    if (!media.isConnected) {
      return false;
    }
    if (document.fullscreenElement && document.fullscreenElement.contains(media)) {
      return true;
    }
    const width = media.getBoundingClientRect().width;
    return width > 0 && width >= window.innerWidth * BIG_VIDEO_SHARE;
  }

  function isIgnored(media) {
    if (!ignoreSelector || !media.isConnected) {
      return false;
    }
    try {
      return media.matches(ignoreSelector);
    } catch (error) {
      return false;
    }
  }

  // Thumbnail previews and animated covers: silent videos nobody has heard.
  // An <audio> element is never a preview, and neither is a video the
  // listener muted after it had been playing with sound.
  function isPreview(media) {
    if (media.tagName !== "VIDEO" || !isSilent(media) || heard.has(media)) {
      return false;
    }
    return media.loop || !isBigVideo(media);
  }

  // Higher wins: playing with sound, playing silently, the element already
  // chosen, anything else that has media loaded, anything else.
  function score(media) {
    if (isIgnored(media) || isPreview(media)) {
      return -1;
    }
    if (isPlaying(media)) {
      if (!isSilent(media)) {
        heard.add(media);
        return 4;
      }
      return 3;
    }
    if (media === current) {
      return 2;
    }
    return media.readyState > 0 ? 1 : 0;
  }

  function finiteDuration(media) {
    return Number.isFinite(media.duration) ? media.duration : 0;
  }

  // "The one playing, else the one already chosen, else the longest."
  // Among several playing elements the one started last wins, as a player
  // that crossfades starts the new track before the old one stops.
  function pick() {
    let best = null;
    let bestScore = -1;
    const alive = [];
    for (const reference of known) {
      const media = reference.deref();
      if (!media) {
        continue;
      }
      alive.push(reference);
      const value = score(media);
      if (value < 0 || value < bestScore) {
        continue;
      }
      if (value === bestScore) {
        const better =
          value === 1
            ? finiteDuration(media) > finiteDuration(best)
            : (startOrder.get(media) || 0) > (startOrder.get(best) || 0);
        if (!better) {
          continue;
        }
      }
      best = media;
      bestScore = value;
    }
    known = alive;
    const changed = best !== current;
    current = best;
    return changed;
  }

  function watch(media) {
    if (tracked.has(media)) {
      return;
    }
    tracked.add(media);
    known.push(new WeakRef(media));
    for (const type of MEDIA_EVENTS) {
      media.addEventListener(type, () => {
        if (type === "play" || type === "playing") {
          startCounter += 1;
          startOrder.set(media, startCounter);
        }
        pick();
        if (media === current) {
          report(true);
        }
      });
    }
    // Muting or unmuting can change which element counts as the player.
    media.addEventListener("volumechange", () => {
      if (pick()) {
        report(true);
      }
    });
  }

  function started(media) {
    watch(media);
    startCounter += 1;
    startOrder.set(media, startCounter);
    pick();
  }

  function scanDocument() {
    try {
      for (const media of document.querySelectorAll("video, audio")) {
        watch(media);
      }
    } catch (error) {
      // The document is not ready yet.
    }
  }

  // ---------- Reporting ----------

  function report(urgent) {
    const media = current;
    const present = Boolean(media) && (media.readyState > 0 || Boolean(media.currentSrc));
    const usable =
      present &&
      media.readyState > 0 &&
      Number.isFinite(media.duration) &&
      media.duration > 0 &&
      Number.isFinite(media.currentTime);
    window.postMessage(
      {
        source: SOURCE_PAGE,
        urgent: Boolean(urgent),
        hasMedia: usable,
        paused: usable ? media.paused : true,
        // True only while the position is really moving (not buffering,
        // seeking or finished), so the island's clock never runs ahead.
        ticking:
          usable &&
          !media.paused &&
          !media.ended &&
          !media.seeking &&
          media.readyState >= HAVE_FUTURE_DATA,
        pos: usable ? media.currentTime : null,
        dur: usable ? media.duration : null,
        rate: usable && media.playbackRate > 0 ? media.playbackRate : 1,
        at: Date.now(),
        session: readSession(),
        // The element as it is, also when it has no usable timeline:
        present,
        // a live stream has no end, so it has no position to show either
        live: present && media.readyState > 0 && !Number.isFinite(media.duration),
        active: present && isPlaying(media),
        // it has played with sound at some point (so it is not a preview)
        heard: present && heard.has(media),
        // still loading the track (or just finished it), so a "paused"
        // reading may only be the gap between two tracks
        settling: present && (media.ended || media.readyState < HAVE_FUTURE_DATA),
        actions: sessionActions(),
        config: configVersion,
      },
      window.location.origin,
    );
  }

  const originalPlay = HTMLMediaElement.prototype.play;
  HTMLMediaElement.prototype.play = function (...args) {
    const result = originalPlay.apply(this, args);
    try {
      // After the call the element already counts as playing.
      started(this);
    } catch (error) {
      // Never let the hook break the page's own playback.
    }
    return result;
  };

  // Elements that start on their own (autoplay) never go through play().
  // Media events do not bubble, but they can be captured on the document.
  for (const type of ["play", "playing", "loadedmetadata"]) {
    document.addEventListener(
      type,
      (event) => {
        const media = event.target;
        if (media instanceof HTMLMediaElement && !tracked.has(media)) {
          if (type === "loadedmetadata") {
            watch(media);
            pick();
          } else {
            started(media);
          }
          if (media === current) {
            report(true);
          }
        }
      },
      true,
    );
  }

  // ---------- Commands from content.js ----------

  function seekElement(media, seconds) {
    const limit = Number.isFinite(media.duration) ? media.duration : seconds;
    media.currentTime = Math.max(0, Math.min(seconds, limit));
  }

  function playElement(media) {
    const result = originalPlay.call(media);
    if (result && typeof result.catch === "function") {
      result.catch(() => {});
    }
  }

  window.addEventListener("message", (event) => {
    const data = event.data;
    if (event.source !== window || !data || data.source !== SOURCE_CONTENT) {
      return;
    }
    if (data.type === "config") {
      ignoreSelector =
        typeof data.ignore === "string" ? data.ignore.slice(0, MAX_SELECTOR_LENGTH) : "";
      configVersion = Number.isFinite(data.version) ? data.version : 0;
      pick();
      return;
    }
    // "element": act on the media element. "session": ask the page through
    // its Media Session handler, and fall back to the element without one.
    const viaSession = data.via === "session";
    const media = current;
    try {
      if (data.type === "next") {
        callSession("nexttrack");
      } else if (data.type === "prev") {
        callSession("previoustrack");
      } else if (data.type === "seek" && Number.isFinite(data.sec)) {
        const seconds = Math.max(0, data.sec);
        if (!(viaSession && callSession("seekto", { seekTime: seconds, fastSeek: false }))) {
          if (media) {
            seekElement(media, seconds);
          }
        }
      } else if (data.type === "play") {
        if (!(viaSession && callSession("play")) && media) {
          playElement(media);
        }
      } else if (data.type === "pause") {
        if (!(viaSession && callSession("pause")) && media) {
          media.pause();
        }
      } else {
        return;
      }
    } catch (error) {
      // The element may have been reset while switching tracks; ignore.
    }
    report(true);
  });

  setInterval(() => {
    reportCount += 1;
    if (reportCount % SCAN_EVERY_REPORTS === 0) {
      scanDocument();
    }
    pick();
    report(false);
  }, REPORT_INTERVAL_MS);
})();
