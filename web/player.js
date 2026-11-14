/* Lil Wayne Tracker -- player.
 *
 * One queue, three sources per track:
 *   spotify  Spotify's embedded player (iFrame API): full tracks for listeners
 *            signed in to Spotify in this browser, 30 s previews otherwise;
 *   apple    Apple Music's embedded player (no script control: play / pause
 *            happen inside it; subscribers hear full tracks);
 *   file     a file of the owner's own library, streamed by the local server
 *            (127.0.0.1 only; never in the static / public copy).
 * The source is the one chosen in Settings ("Play with", Spotify by default),
 * else the next available one.
 * Queue (add / remove / clear), shuffle, repeat (off / queue / song forever /
 * song once), volume (local files: the embeds have their own) and synced
 * lyrics (local server only: /api/lyrics/<version>, LRCLIB, never stored).
 * Uses app.js globals ($, esc, state, duration, eraOf, loadSettings, STATIC).
 */

const PLAYER_ORDER = ["spotify", "apple", "file"];
const SOURCE_NAMES = { spotify: "Spotify", apple: "Apple Music", file: "Local file" };
const REPEAT_MODES = ["off", "all", "one", "once"];
const REPEAT_LABELS = { off: "Repeat: off", all: "Repeat the queue", one: "Repeat this song (forever)",
                        once: "Repeat this song once" };
// Monochrome icons (Material Design shapes), drawn in the text colour.
const ICON_PATHS = {
  play: "M8 5v14l11-7z", pause: "M6 19h4V5H6v14zm8-14v14h4V5h-4z",
  prev: "M6 6h2v12H6zm3.5 6 8.5 6V6z", next: "M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z",
  shuffle: "M10.59 9.17 5.41 4 4 5.41l5.17 5.17 1.42-1.41zM14.5 4l2.04 2.04L4 18.59 5.41 20 17.96 7.46 20 9.5V4h-5.5zm.33 9.41-1.41 1.41 3.13 3.13L14.5 20H20v-5.5l-2.04 2.04-3.13-3.13z",
  repeat: "M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4z",
  repeatOne: "M7 7h10v3l4-4-4-4v3H5v6h2V7zm10 10H7v-3l-4 4 4 4v-3h12v-6h-2v4zm-4-2V9h-1l-2 1v1h1.5v4H13z",
  volUp: "M3 9v6h4l5 5V4L7 9H3zm13.5 3A4.5 4.5 0 0 0 14 7.97v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z",
  volDown: "M18.5 12A4.5 4.5 0 0 0 16 7.97v8.05c1.48-.73 2.5-2.25 2.5-4.02zM5 9v6h4l5 5V4L9 9H5z",
  volOff: "M16.5 12A4.5 4.5 0 0 0 14 7.97v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51A8.8 8.8 0 0 0 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3 3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06a8.99 8.99 0 0 0 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4 9.91 6.09 12 8.18V4z",
  queue: "M15 6H3v2h12V6zm0 4H3v2h12v-2zM3 16h8v-2H3v2zM17 6v8.18A3 3 0 1 0 19 17V8h3V6h-5z",
  // speech bubble with lines of text
  lyrics: "M20 2H4a2 2 0 0 0-2 2v18l4-4h14a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2zM6 6h12v2H6zm0 4h12v2H6zm0 4h8v2H6z",
};
const icon = (name) => `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="${ICON_PATHS[name]}"/></svg>`;
const PLAYER_KEY = "lwt-player";  // volume, repeat, shuffle (this browser only)
const player = { queue: [], original: null, index: -1, source: null, paused: true, position: 0, duration: 0,
                 spotify: null, spotifyApi: null, pendingPlay: false, ended: false, repeatedOnce: false,
                 repeat: "off", shuffle: false, volume: 80, muted: false, side: "queue",
                 lyrics: null, lyricsFor: null, lyricLine: -1, nextId: 1 };
const audio = $("pl-audio");
const lyricsCache = new Map();

try { Object.assign(player, JSON.parse(localStorage.getItem(PLAYER_KEY)) || {}); } catch (e) { /* defaults */ }
if (!REPEAT_MODES.includes(player.repeat)) player.repeat = "off";
function savePlayer() {
  const { volume, muted, repeat, shuffle } = player;
  try { localStorage.setItem(PLAYER_KEY, JSON.stringify({ volume, muted, repeat, shuffle })); } catch (e) { /* storage unavailable */ }
}

function preferredSource() {
  const choice = loadSettings().player;
  return PLAYER_ORDER.includes(choice) ? choice : "spotify";
}

// The sources of one queue item that this copy of the site can play.
function sourcesOf(item) {
  const have = { spotify: item.spotify, apple: item.apple, file: !STATIC && item.file_id };
  const preferred = preferredSource();
  return [preferred, ...PLAYER_ORDER.filter((s) => s !== preferred)].filter((s) => have[s]);
}
const playable = (play) => Boolean(play) && sourcesOf(play).length > 0;

function coverUrl(song) {
  if (song.cover) return `covers/${song.cover}`;
  const era = eraOf(song);
  return `covers/${era && era.cover_file ? era.cover_file : "artist.jpg"}`;
}

// Queue items: {qid, id (song), v (version), title, artist, cover, spotify, apple, file_id}.
function songItem(song) {
  return { id: song.id, title: song.title, artist: song.artist, cover: coverUrl(song), album: song.album, ...song.play };
}

// "▶ Listen" and "+" (add to queue) for an item that can be played; nothing otherwise.
function playButton(play, attrs, label = "Listen") {
  if (!playable(play)) return "";
  return `<span class="play-group"><button type="button" class="play-btn" ${attrs}
    title="Play (${esc(SOURCE_NAMES[sourcesOf(play)[0]])})">▶${label ? ` ${esc(label)}` : ""}</button><button type="button"
    class="play-btn add" data-add="1" ${attrs} title="Add to queue" aria-label="Add to queue">+</button></span>`;
}

/* ---------- queue ---------- */

const withId = (item) => ({ ...item, qid: player.nextId++ });

function shuffled(items) {
  const list = items.slice();
  for (let i = list.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [list[i], list[j]] = [list[j], list[i]];
  }
  return list;
}

function startQueue(items, index = 0) {
  const start = items[index];
  const list = items.filter((item) => sourcesOf(item).length).map(withId);
  if (!list.length) return;
  let first = Math.max(0, items.filter((item) => sourcesOf(item).length).indexOf(start));
  player.original = list.slice();
  if (player.shuffle) {
    const current = list[first];
    player.queue = [current, ...shuffled(list.filter((item) => item !== current))];
    first = 0;
  } else {
    player.queue = list;
  }
  player.index = first;
  playCurrent();
}

function addToQueue(item) {
  if (!sourcesOf(item).length) return;
  const entry = withId(item);
  player.queue.push(entry);
  if (player.original) player.original.push(entry);
  if (player.index < 0) { player.index = player.queue.length - 1; playCurrent(); return; }
  renderQueue();
  flash(`Added to queue: ${item.title}`);
}

function removeFromQueue(qid) {
  const at = player.queue.findIndex((item) => item.qid === qid);
  if (at < 0 || at === player.index) return;
  player.queue.splice(at, 1);
  if (player.original) player.original = player.original.filter((item) => item.qid !== qid);
  if (at < player.index) player.index -= 1;
  renderQueue();
}

function clearUpcoming() {
  const keep = new Set(player.queue.slice(0, player.index + 1).map((item) => item.qid));
  player.queue = player.queue.slice(0, player.index + 1);
  if (player.original) player.original = player.original.filter((item) => keep.has(item.qid));
  renderQueue();
}

function setShuffle(on) {
  player.shuffle = on;
  const current = player.queue[player.index];
  if (current) {
    if (on) {
      player.original = player.original || player.queue.slice();
      player.queue = [...player.queue.slice(0, player.index + 1), ...shuffled(player.queue.slice(player.index + 1))];
    } else if (player.original) {  // back to the order the songs were added in
      const inQueue = new Set(player.queue.map((item) => item.qid));
      player.queue = player.original.filter((item) => inQueue.has(item.qid));
      player.index = player.queue.indexOf(current);
    }
  }
  savePlayer();
  renderModes();
  renderQueue();
}

function cycleRepeat() {
  player.repeat = REPEAT_MODES[(REPEAT_MODES.indexOf(player.repeat) + 1) % REPEAT_MODES.length];
  player.repeatedOnce = false;
  savePlayer();
  renderModes();
}

/* ---------- playback ---------- */

function stopAll() {
  audio.pause();
  if (player.spotify && player.source === "spotify") player.spotify.pause();
  $("pf-apple").innerHTML = "";
  player.pendingPlay = false;
}

function playCurrent(replay = false) {
  const item = player.queue[player.index];
  if (!item) return;
  stopAll();
  if (!replay) player.repeatedOnce = false;
  player.source = sourcesOf(item)[0];
  player.position = 0;
  player.duration = 0;
  player.paused = true;
  player.ended = false;
  player.lyricLine = -1;
  showHint("");
  $("player").hidden = false;
  document.body.classList.add("has-player");
  if (player.source === "file") {
    audio.src = `/api/audio/${item.file_id}`;
    applyVolume();
    audio.play().catch((error) => {  // AbortError: the source changed meanwhile (not a failure)
      if (error.name === "NotAllowedError") showHint("The browser blocked playback: press ▶.");
    });
  } else if (player.source === "spotify") {
    playSpotify(`spotify:track:${item.spotify}`);
  } else if (player.source === "apple") {
    const url = item.apple.replace("://music.apple.com/", "://embed.music.apple.com/");
    $("pf-apple").innerHTML = `<iframe title="Apple Music player" src="${esc(url)}" height="175" loading="lazy"
      allow="autoplay *; encrypted-media *; clipboard-write" sandbox="allow-forms allow-popups allow-same-origin allow-scripts allow-storage-access-by-user-activation allow-top-navigation-by-user-activation"></iframe>`;
    showHint("Press play in the Apple Music player below.");
    openFull(true);
  }
  mediaSession(item);
  renderPlayer();
  if (isFullOpen() && player.side === "lyrics") loadLyrics();
}

function loadSpotifyApi() {
  if (player.spotifyApi) return player.spotifyApi;
  player.spotifyApi = new Promise((resolve, reject) => {
    window.onSpotifyIframeApiReady = resolve;
    const script = document.createElement("script");
    script.src = "https://open.spotify.com/embed/iframe-api/v1";
    script.async = true;
    script.onerror = () => { player.spotifyApi = null; reject(new Error("Spotify player could not be loaded")); };
    document.head.appendChild(script);
  });
  return player.spotifyApi;
}

async function playSpotify(uri) {
  player.pendingPlay = true;
  try {
    const api = await loadSpotifyApi();
    if (!player.spotify) {
      await new Promise((resolve) => api.createController($("pf-spotify"), { uri, width: "100%", height: 152 }, (controller) => {
        player.spotify = controller;
        controller.addListener("ready", () => {
          if (player.pendingPlay && player.source === "spotify") { player.pendingPlay = false; controller.play(); }
        });
        controller.addListener("playback_update", (event) => onSpotifyUpdate(event.data));
        resolve();
      }));
    } else {
      player.spotify.loadUri(uri);
    }
    // When the browser does not let the embed start by itself, ask for one click inside it.
    setTimeout(() => {
      if (player.source === "spotify" && player.paused && !player.position) {
        if (player.pendingPlay) { player.pendingPlay = false; player.spotify.play(); }
        setTimeout(() => {
          if (player.source === "spotify" && player.paused && !player.position) {
            showHint("Press play in the Spotify player below.");
            openFull(true);
          }
        }, 2000);
      }
    }, 1500);
  } catch (error) {
    showHint(error.message);
    openFull(true);
  }
}

function onSpotifyUpdate(data) {
  if (player.source !== "spotify" || !data) return;
  const wasPlaying = !player.paused, last = player.position, length = player.duration;
  player.paused = data.isPaused;
  player.position = data.position / 1000;
  player.duration = data.duration / 1000;
  if (!data.isPaused) showHint("");
  // The embed stops at the end of the track (or of the 30 s preview).
  const atEnd = data.duration && data.position >= data.duration - 400;
  const wrapped = wasPlaying && length && last >= length - 1.5 && data.position < 1000;  // reset to 0 at the end
  if (data.isPaused && (atEnd || wrapped) && !player.ended) {
    player.ended = true;
    trackEnded();
    return;
  }
  renderProgress();
}

function trackEnded() {
  if (player.repeat === "one") return playCurrent(true);
  if (player.repeat === "once" && !player.repeatedOnce) { player.repeatedOnce = true; return playCurrent(true); }
  next();
}

function toggle() {
  if (player.index < 0) return;
  if (player.source === "file") { if (audio.paused) audio.play(); else audio.pause(); }
  else if (player.source === "spotify" && player.spotify) player.spotify.togglePlay();
  else if (player.source === "apple") openFull(true);
}

function next() {
  if (player.index < player.queue.length - 1) { player.index += 1; playCurrent(); }
  else if (player.repeat === "all" && player.queue.length) { player.index = 0; playCurrent(); }
  else { stopAll(); player.paused = true; renderProgress(); }
}

function previous() {
  if (player.position > 3 || (player.index === 0 && player.repeat !== "all")) seek(0);
  else { player.index = (player.index - 1 + player.queue.length) % player.queue.length; playCurrent(); }
}

function seek(seconds) {
  if (player.source === "file") audio.currentTime = seconds;
  else if (player.source === "spotify" && player.spotify) player.spotify.seek(seconds);
}

function applyVolume() {
  audio.volume = player.muted ? 0 : player.volume / 100;
  const level = icon(player.muted || !player.volume ? "volOff" : player.volume < 50 ? "volDown" : "volUp");
  const own = player.source === "file" || !player.source;
  for (const el of document.querySelectorAll('[data-pl="mute"]')) { el.innerHTML = level; el.disabled = !own; }
  for (const el of document.querySelectorAll(".pl-vol")) {
    el.value = player.muted ? 0 : player.volume;
    el.disabled = !own;
    el.title = own ? `Volume ${player.muted ? 0 : player.volume}%` : `Use the volume of the ${SOURCE_NAMES[player.source]} player or of the system`;
  }
}

function closePlayer() {
  stopAll();
  player.queue = [];
  player.original = null;
  player.index = -1;
  player.source = null;
  openFull(false);
  $("player").hidden = true;
  document.body.classList.remove("has-player");
  updateCoverAccent();
}

audio.addEventListener("play", () => { player.paused = false; showHint(""); renderProgress(); });
audio.addEventListener("pause", () => { player.paused = true; renderProgress(); });
audio.addEventListener("timeupdate", () => { player.position = audio.currentTime; player.duration = audio.duration || 0; renderProgress(); });
audio.addEventListener("ended", trackEnded);
audio.addEventListener("error", () => { if (player.source === "file" && audio.src) showHint("This file could not be played."); });

/* ---------- lyrics (local server only) ---------- */

async function loadLyrics() {
  const item = player.queue[player.index];
  const box = $("pf-lyrics");
  if (!item || !item.v) { box.innerHTML = `<p class="muted">No lyrics for this track.</p>`; return; }
  if (player.lyricsFor === item.qid) return;
  player.lyricsFor = item.qid;
  player.lyricLine = -1;
  box.innerHTML = `<p class="muted">Looking for lyrics…</p>`;
  let data = lyricsCache.get(item.v);
  if (!data) {
    try { data = await getJSON(`/api/lyrics/${item.v}`); } catch (e) { data = { error: e.message }; }
    if (!data.error) lyricsCache.set(item.v, data);
  }
  if (player.queue[player.index] !== item) return;  // the track changed meanwhile
  player.lyrics = data;
  const credit = `<p class="lyrics-credit">Lyrics: <a href="https://lrclib.net" target="_blank" rel="noopener noreferrer">LRCLIB</a>
    · shown on this computer only, not stored</p>`;
  if (data.error) {
    player.lyricsFor = null;
    box.innerHTML = `<p class="muted">Lyrics could not be loaded (${esc(data.error)}).</p>`;
  } else if (data.synced) {
    box.innerHTML = data.synced.map(([t, line], i) =>
      `<p class="lyric${line ? "" : " gap"}" data-line="${i}" data-t="${t}">${esc(line) || "♪"}</p>`).join("") + credit;
    if (player.source === "apple") box.insertAdjacentHTML("afterbegin", `<p class="muted">Apple Music does not tell the page the position, so the lines are not followed.</p>`);
  } else if (data.plain) {
    box.innerHTML = `<p class="muted">Not synced: plain lyrics.</p><div class="lyrics-plain">${esc(data.plain)}</div>${credit}`;
  } else {
    box.innerHTML = `<p class="muted">No lyrics found for this track.</p>`;
  }
  followLyrics(true);
}

function followLyrics(force = false) {
  const lines = player.lyrics && player.lyrics.synced;
  if (!lines || $("pf-lyrics").hidden || player.source === "apple") return;
  let current = -1;
  for (let i = 0; i < lines.length && lines[i][0] <= player.position + 0.25; i++) current = i;
  if (current === player.lyricLine && !force) return;
  player.lyricLine = current;
  const box = $("pf-lyrics");
  box.querySelector(".lyric.now")?.classList.remove("now");
  const el = box.querySelector(`[data-line="${current}"]`);
  if (el) {
    el.classList.add("now");
    box.scrollTo({ top: el.offsetTop - box.clientHeight / 2 + el.clientHeight / 2, behavior: force ? "auto" : "smooth" });
  }
}

/* ---------- colours from the playing cover (Settings → Colours → From the playing cover) ---------- */

const coverColors = new Map();
function dominantColor(url) {
  if (coverColors.has(url)) return coverColors.get(url);
  const promise = new Promise((resolve) => {
    const image = new Image();
    image.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = canvas.height = 32;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      context.drawImage(image, 0, 0, 32, 32);
      const data = context.getImageData(0, 0, 32, 32).data;
      let r = 0, g = 0, b = 0, weight = 0;
      for (let i = 0; i < data.length; i += 4) {  // colourful pixels count more than grey / black / white ones
        const max = Math.max(data[i], data[i + 1], data[i + 2]), min = Math.min(data[i], data[i + 1], data[i + 2]);
        const w = (max - min) / 255 + 0.03;
        r += data[i] * w; g += data[i + 1] * w; b += data[i + 2] * w; weight += w;
      }
      resolve([r / weight, g / weight, b / weight]);
    };
    image.onerror = () => resolve(null);
    image.src = url;
  });
  coverColors.set(url, promise);
  return promise;
}

function toHsl([r, g, b]) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
  if (max === min) return [0, 0, l * 100];
  const d = max - min, s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [h * 60, s * 100, l * 100];
}

function hslToRgb(h, s, l) {
  s /= 100; l /= 100;
  const k = (n) => (n + h / 30) % 12, a = s * Math.min(l, 1 - l);
  const f = (n) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return [f(0) * 255, f(8) * 255, f(4) * 255];
}
function luminance([r, g, b]) {
  const c = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * c(r) + 0.7152 * c(g) + 0.0722 * c(b);
}
function contrast(a, b) {
  const [x, y] = [luminance(a), luminance(b)].sort((m, n) => n - m);
  return (x + 0.05) / (y + 0.05);
}
// The lightness closest to `start` whose colour has >= 4.5:1 contrast with the surface (WCAG AA text).
function readableLightness(h, s, start, surface, step) {
  let l = start;
  while (l > 5 && l < 95 && contrast(hslToRgb(h, s, l), surface) < 4.5) l += step;
  return l;
}

function darkTheme() {
  const theme = document.documentElement.dataset.theme;
  return theme ? theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
}

// Accent (readable: light 36 % on white, 72 % on the dark surface) and a soft page tint.
async function updateCoverAccent() {
  const root = document.documentElement.style;
  const item = player.queue[player.index];
  const names = ["--cover-accent", "--cover-on-dark", "--cover-tint"];
  if (document.documentElement.dataset.accent !== "cover" || !item) { names.forEach((n) => root.removeProperty(n)); return; }
  const rgb = await dominantColor(item.cover);
  if (!rgb || player.queue[player.index] !== item) return;
  const [h, s0] = toHsl(rgb);
  const s = Math.round(Math.max(s0 < 8 ? 0 : 40, Math.min(s0, 80)));  // grey covers stay grey
  const dark = darkTheme();
  const l = dark ? readableLightness(h, s, 68, [29, 28, 26], 2) : readableLightness(h, s, 40, [255, 255, 255], -2);
  root.setProperty("--cover-accent", `hsl(${Math.round(h)} ${s}% ${l}%)`);
  root.setProperty("--cover-on-dark", `hsl(${Math.round(h)} ${s}% 80%)`);
  root.setProperty("--cover-tint", dark ? `hsl(${Math.round(h)} ${Math.round(s * 0.6)}% 16%)` : `hsl(${Math.round(h)} ${Math.round(s * 0.7)}% 90%)`);
}
window.updateCoverAccent = updateCoverAccent;
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", updateCoverAccent);

/* ---------- views ---------- */

function showHint(text) {
  $("pf-hint").textContent = text;
  $("pf-hint").hidden = !text;
}

function flash(text) {
  let note = document.querySelector(".pl-flash");
  if (!note) { note = document.createElement("div"); note.className = "pl-flash"; document.body.appendChild(note); }
  note.textContent = text;
  note.classList.add("show");
  clearTimeout(flash.timer);
  flash.timer = setTimeout(() => note.classList.remove("show"), 1800);
}

const isFullOpen = () => $("player-full").classList.contains("open");
function openFull(open) {
  $("player-full").classList.toggle("open", open);
  $("player-full").setAttribute("aria-hidden", String(!open));
  document.body.classList.toggle("player-open", open);
  if (open && player.side === "lyrics") loadLyrics();
}

function setSide(side) {
  player.side = STATIC ? "queue" : side;
  for (const tab of document.querySelectorAll("[data-side]")) tab.setAttribute("aria-selected", String(tab.dataset.side === player.side));
  $("pf-queue").hidden = player.side !== "queue";
  $("pf-clear").hidden = player.side !== "queue";
  $("pf-lyrics").hidden = player.side !== "lyrics";
  if (player.side === "lyrics" && isFullOpen()) loadLyrics();
}

function renderPlayer() {
  const item = player.queue[player.index];
  if (!item) return;
  $("pl-cover").src = item.cover;
  $("pf-cover").src = item.cover;
  $("pf-bg").style.backgroundImage = `url("${item.cover}")`;
  $("pl-title").textContent = item.title;
  $("pf-title").textContent = item.title;
  $("pl-artist").textContent = item.artist;
  $("pl-album").hidden = !item.album;
  if (item.album) {  // the release the song is on: a link next to the title
    $("pl-album").href = `#/release/${item.album.id}`;
    $("pl-album").textContent = item.album.title;
    $("pl-album").title = `Open ${item.album.title}`;
  }
  $("pf-artist").innerHTML = `${esc(item.artist)}${item.album ? ` · <a href="#/release/${item.album.id}">${esc(item.album.title)}</a>` : ""}
    · <a href="#/song/${item.id}">Song details</a>`;
  for (const el of document.querySelectorAll("[data-pl-source]")) {
    el.textContent = `via ${SOURCE_NAMES[player.source] || ""}${player.source === "spotify" ? " (preview unless signed in)" : ""}`;
  }
  $("pf-embed").dataset.source = player.source || "";  // (the Spotify API replaces #pf-spotify by its iframe)
  const seekable = player.source !== "apple";
  for (const el of document.querySelectorAll(".pl-seek")) el.disabled = !seekable;
  applyVolume();
  renderModes();
  renderQueue();
  renderProgress();
  updateCoverAccent();
}

function renderQueue() {
  $("pf-queue").innerHTML = player.queue.map((q, i) => `<li class="${i === player.index ? "now" : ""}" data-queue="${i}">
    <img class="cover small" src="${esc(q.cover)}" alt="" loading="lazy"><span><span class="title">${esc(q.title)}</span>
    <span class="muted">${esc(q.artist)}</span></span>
    ${i === player.index ? `<span class="now-mark" title="Now playing">♪</span>`
      : `<button class="pl-btn small remove" data-remove="${q.qid}" title="Remove from queue" aria-label="Remove from queue">×</button>`}</li>`).join("")
    || `<li class="muted">The queue is empty.</li>`;
  $("pf-queue").querySelector("li.now")?.scrollIntoView({ block: "nearest" });
}

function renderModes() {
  for (const el of document.querySelectorAll('[data-pl="repeat"]')) {
    // queue: repeat · song forever: repeat + ∞ · song once: repeat-one (the 1 is in the icon)
    el.innerHTML = player.repeat === "once" ? icon("repeatOne") : icon("repeat") + (player.repeat === "one" ? "<sup>∞</sup>" : "");
    el.classList.toggle("on", player.repeat !== "off");
    el.title = REPEAT_LABELS[player.repeat];
    el.setAttribute("aria-label", REPEAT_LABELS[player.repeat]);
  }
  for (const el of document.querySelectorAll('[data-pl="shuffle"]')) {
    el.classList.toggle("on", player.shuffle);
    el.setAttribute("aria-pressed", String(player.shuffle));
    el.title = player.shuffle ? "Shuffle: on" : "Shuffle: off";
  }
}

function renderProgress() {
  const fmt = (s) => (s && isFinite(s) ? duration(s * 1000) : "0:00");
  for (const el of document.querySelectorAll("[data-pl-time]")) el.textContent = fmt(player.position);
  for (const el of document.querySelectorAll("[data-pl-duration]")) el.textContent = fmt(player.duration);
  for (const el of document.querySelectorAll(".pl-seek")) {
    if (document.activeElement !== el) el.value = player.duration ? Math.round(1000 * player.position / player.duration) : 0;
  }
  const toggleIcon = icon(player.paused ? "play" : "pause");
  for (const el of document.querySelectorAll('[data-pl="toggle"]')) if (el.dataset.icon !== toggleIcon) { el.innerHTML = toggleIcon; el.dataset.icon = toggleIcon; }
  followLyrics();
}

function mediaSession(item) {
  if (!("mediaSession" in navigator)) return;
  try {
    navigator.mediaSession.metadata = new MediaMetadata({ title: item.title, artist: item.artist,
      artwork: [{ src: new URL(item.cover, location.href).href }] });
    navigator.mediaSession.setActionHandler("previoustrack", previous);
    navigator.mediaSession.setActionHandler("nexttrack", next);
  } catch (e) { /* not supported */ }
}

/* ---------- wiring ---------- */

const ACTIONS = { prev: previous, toggle, next, shuffle: () => setShuffle(!player.shuffle), repeat: cycleRepeat,
                  mute: () => { player.muted = !player.muted; savePlayer(); applyVolume(); } };
for (const el of document.querySelectorAll("[data-pl]")) {
  el.addEventListener("click", (event) => { event.stopPropagation(); ACTIONS[el.dataset.pl](); });
}
for (const el of document.querySelectorAll(".pl-seek")) {
  el.addEventListener("change", () => { if (player.duration) seek(player.duration * el.value / 1000); });
}
for (const el of document.querySelectorAll(".pl-vol")) {
  el.addEventListener("input", () => {
    player.volume = Number(el.value);
    player.muted = player.volume === 0;
    applyVolume();
  });
  el.addEventListener("change", savePlayer);
}
$("pl-expand").addEventListener("click", () => openFull(true));
$("pl-title").addEventListener("click", () => openFull(true));
$("player").addEventListener("click", (event) => { if (event.target === $("player")) openFull(true); });
$("pl-queue-open").addEventListener("click", () => { setSide("queue"); openFull(true); });
$("pl-lyrics-open").addEventListener("click", () => { setSide("lyrics"); openFull(true); });
$("pl-stop").addEventListener("click", closePlayer);
$("pf-close").addEventListener("click", () => openFull(false));
$("pf-bg").addEventListener("click", () => openFull(false));
$("pf-clear").addEventListener("click", clearUpcoming);
for (const tab of document.querySelectorAll("[data-side]")) tab.addEventListener("click", () => setSide(tab.dataset.side));
$("pf-queue").addEventListener("click", (event) => {
  const remove = event.target.closest("[data-remove]");
  if (remove) { removeFromQueue(Number(remove.dataset.remove)); return; }
  const row = event.target.closest("[data-queue]");
  if (row && Number(row.dataset.queue) !== player.index) { player.index = Number(row.dataset.queue); playCurrent(); }
});
$("pf-lyrics").addEventListener("click", (event) => {
  const line = event.target.closest("[data-t]");
  if (line && player.source !== "apple") seek(Number(line.dataset.t));
});
$("pf-artist").addEventListener("click", (event) => { if (event.target.closest("a")) openFull(false); });
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && isFullOpen()) openFull(false);
});

// The items a play button stands for: {items, index} (the list on screen, the release, one version).
function itemsFor(button) {
  const { playSong, playRelease, playVersion } = button.dataset;
  if (playSong) {
    const list = (state.shown || []).filter((s) => playable(s.play));
    const song = list.find((s) => String(s.id) === playSong) || state.songs.find((s) => String(s.id) === playSong);
    return list.includes(song) ? { items: list.map(songItem), index: list.indexOf(song) } : { items: [songItem(song)], index: 0 };
  }
  if (playRelease !== undefined && state.release) {
    const release = state.release;
    const cover = `covers/${release.cover_file || "artist.jpg"}`;
    const tracks = release.tracks.filter((t) => playable(t.play));
    const album = { id: release.id, title: release.title };
    const items = tracks.map((t) => ({ id: t.recording_id, v: t.version_id, title: t.title, artist: t.artist || "Lil Wayne",
                                       cover, album, ...t.play }));
    const start = playRelease === "" ? 0 : tracks.findIndex((t) => String(t.version_id) === playRelease);
    return { items, index: Math.max(0, start) };
  }
  if (playVersion && state.song) {
    const song = state.song;
    const version = song.versions.find((v) => String(v.id) === playVersion);
    const listed = state.songs.find((s) => s.id === song.id) || {};
    // the album version: the song's album (albums before singles, as in the song list); other versions: their own release
    const on = version.tracks.find((t) => t.discography === "official") || version.tracks[0];
    const album = version.version_type === "album" && listed.album ? listed.album
      : on ? { id: on.release_id, title: on.release } : listed.album;
    return { items: [{ id: song.id, v: version.id, album, title: version.version_type === "album" ? song.title
      : `${song.title} (${version.version_name || VERSION_TYPES[version.version_type] || "version"})`,
      artist: song.artist, cover: song.cover_file ? `covers/${song.cover_file}` : coverUrl(listed), ...version.play }], index: 0 };
  }
  return { items: [], index: 0 };
}

// Play / add buttons anywhere on the page (capture: runs before the row / card links).
document.addEventListener("click", (event) => {
  const button = event.target.closest(".play-btn");
  if (!button) return;
  event.preventDefault();
  event.stopPropagation();
  const { items, index } = itemsFor(button);
  if (!items.length) return;
  if (button.dataset.add) addToQueue(items[index]);
  else startQueue(items, index);
}, true);

if (STATIC) {
  document.querySelector('#set-player option[value="file"]').remove();
  $("pf-lyrics-tab").remove();
  $("pl-lyrics-open").remove();
}
for (const el of document.querySelectorAll('[data-pl="prev"]')) el.innerHTML = icon("prev");
for (const el of document.querySelectorAll('[data-pl="next"]')) el.innerHTML = icon("next");
for (const el of document.querySelectorAll('[data-pl="shuffle"]')) el.innerHTML = icon("shuffle");
$("pl-queue-open").innerHTML = icon("queue");
if (!STATIC) $("pl-lyrics-open").innerHTML = icon("lyrics");
renderModes();
applyVolume();
renderProgress();
