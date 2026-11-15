"use strict";

const STATUS = { released: "Released", leaked: "Leaked", unreleased: "Unreleased" };
const VERSION_TYPES = {
  album: "Main", remix: "Remix", demo: "Demo", snippet: "Snippet", original: "Original",
  alternate: "Alternate", extended: "Extended", live: "Live", acapella: "Acapella",
};
const ROLES = {
  main_artist: "Main artist", featured_artist: "Featured", producer: "Producer", co_producer: "Co-producer",
  writer: "Writer", composer: "Composer", executive_producer: "Executive producer",
  mixing_engineer: "Mixing", recording_engineer: "Recording", mastering_engineer: "Mastering",
  engineer: "Engineer", vocals: "Vocals", additional_vocals: "Additional vocals", background_vocals: "Background vocals",
  remixer: "Remixer",
};
const ROLE_ORDER = ["main_artist", "featured_artist", "producer", "co_producer", "writer", "composer"];
const RELEASE_TYPES = {
  studio_album: "Studio albums", collaboration_album: "Collaboration albums", group_album: "Group albums",
  ep: "EPs", mixtape: "Mixtapes", single: "Singles", group_single: "Group singles", bootleg: "Bootlegs",
};
const PAGE = 50;  // songs rendered at a time; more load while scrolling
const byTitle = (a, b) => a.title.localeCompare(b.title);
const SORTS = {
  date: (a, b) => (a.date || "9999").localeCompare(b.date || "9999") || byTitle(a, b),
  title: byTitle,
  versions: (a, b) => b.versions - a.versions || byTitle(a, b),
  releases: (a, b) => b.releases - a.releases || byTitle(a, b),
};

const state = { songs: [], eras: [], eraById: new Map(), releases: [],
                statuses: new Set(["released", "leaked", "unreleased"]), limit: PAGE, view: "list" };
const $ = (id) => document.getElementById(id);

function esc(text) {
  return String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fold(text) {
  return String(text ?? "").toLowerCase().normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/[’'`]/g, "");
}
function duration(ms) {
  if (!ms) return "";
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
// Simple monogram badges in each service's colour (no copied logos).
const SERVICES = [  // also the display order
  { test: /spotify\.com/, name: "Spotify", mono: "S", color: "#1DB954", ink: "#111" },
  { test: /music\.apple\.com|itunes\.apple\.com/, name: "Apple Music", mono: "♫", color: "#FA243C" },
  { test: /deezer\.com/, name: "Deezer", mono: "Dz", color: "#A238FF" },
  { test: /tidal\.com/, name: "Tidal", mono: "T", color: "#000000" },
  { test: /music\.youtube\.com/, name: "YouTube Music", mono: "▶", color: "#FF0000" },
  { test: /youtube\.com|youtu\.be/, name: "YouTube", mono: "▶", color: "#CC0000" },
  { test: /amazon\./, name: "Amazon Music", mono: "a", color: "#25D1DA", ink: "#111" },
  { test: /soundcloud\.com/, name: "SoundCloud", mono: "SC", color: "#FF5500" },
  { test: /instagram\.com/, name: "Instagram", mono: "IG", color: "#C13584" },
  { test: /twitter\.com|x\.com/, name: "X (Twitter)", mono: "X", color: "#000000" },
  { test: /facebook\.com/, name: "Facebook", mono: "f", color: "#1877F2" },
  { test: /tiktok\.com/, name: "TikTok", mono: "♪", color: "#111111" },
  { test: /allmusic\.com/, name: "AllMusic", mono: "AM", color: "#0B6EB5" },
  { test: /imdb\.com/, name: "IMDb", mono: "IM", color: "#F5C518", ink: "#111" },
  { test: /last\.fm/, name: "Last.fm", mono: "fm", color: "#D51007" },
  { test: /setlist\.fm/, name: "setlist.fm", mono: "SL", color: "#5a5a5a" },
  { test: /wikidata\.org/, name: "Wikidata", mono: "WD", color: "#006699" },
  { test: /genius\.com/, name: "Genius", mono: "G", color: "#FFFF64", ink: "#111" },
  { test: /musicbrainz\.org/, name: "MusicBrainz", mono: "MB", color: "#BA478F" },
  { test: /discogs\.com/, name: "Discogs", mono: "D", color: "#333333" },
  { test: /lilwaynehq\.com/, name: "LilWayneHQ", mono: "HQ", color: "#C9A227", ink: "#111" },
  { test: /wikipedia\.org/, name: "Wikipedia", mono: "W", color: "#6B6B6B" },
  { test: /coverartarchive\.org/, name: "Cover Art Archive", mono: "CA", color: "#7a5a3a" },
];

const NOTIFY_READ_KEY = "lwt-notify-read";  // day whose notifications were opened
const STREAMING = new Set(["Spotify", "Apple Music", "Deezer", "Tidal", "YouTube Music", "YouTube", "Amazon Music", "SoundCloud"]);
const isStreaming = (url) => STREAMING.has(serviceOf(url).name);

function serviceOf(url) {
  let host = "Link";
  try { host = new URL(url).hostname.replace(/^www\./, ""); } catch (e) { /* not a full URL */ }
  return SERVICES.find((s) => s.test.test(url)) || { name: host, mono: "↗", color: "#888" };
}

// One badge per service (links to the first URL); further URLs of that service under "+N".
function serviceLinks(urls) {
  const groups = new Map();
  for (const url of new Set(urls.filter(Boolean))) {
    const service = serviceOf(url);
    if (!groups.has(service.name)) groups.set(service.name, { service, urls: [] });
    groups.get(service.name).urls.push(url);
  }
  const order = SERVICES.map((s) => s.name);
  const sorted = [...groups.values()].sort((a, b) =>
    (order.indexOf(a.service.name) + 1 || 99) - (order.indexOf(b.service.name) + 1 || 99));
  return sorted.length ? `<div class="services">${sorted.map(({ service, urls: list }) => {
    const style = `--svc:${service.color};--ink:${service.ink || "#fff"}`;
    const main = `<a class="svc" href="${esc(list[0])}" target="_blank" rel="noopener noreferrer" title="${esc(list[0])}"
      style="${style}"><span class="mono">${esc(service.mono)}</span>${esc(service.name)}</a>`;
    if (list.length === 1) return main;
    return `<span class="svc-group">${main}<details class="svc-more"><summary title="More ${esc(service.name)} links">+${list.length - 1}</summary>
      <div class="svc-list">${list.slice(1).map((u, i) => `<a href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(service.name)} ${i + 2}</a>`).join("")}</div>
      </details></span>`;
  }).join("")}</div>` : "";
}

function badge(status) {
  return `<span class="badge ${esc(status)}">${esc(STATUS[status] || status)}</span>`;
}
function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}
// The published static copy (export_static.py) reads api/<path>.json files instead of the server.
// getJSON, API.features and STATIC come from api.js (mode told by GET /api/v1/).
API_READY.then(() => {
  if (!API.features.review) document.querySelectorAll('[data-tab="review"]').forEach((el) => el.remove());
}).catch(() => {});

/* ---------- songs ---------- */

function filteredSongs() {
  const query = fold($("search").value.trim());
  const era = $("era").value, kind = $("kind").value, vtype = $("vtype").value;
  return state.songs.filter((song) => {
    if (kind === "own" && song.guest) return false;
    if (kind === "guest" && !song.guest) return false;
    if (era && String(song.era ?? "none") !== era) return false;
    if (vtype && !song.types.includes(vtype)) return false;
    if (query) {
      const haystack = fold([song.title, song.artist, song.credit || "", ...song.aliases, ...song.producers].join(" "));
      if (!haystack.includes(query)) return false;
    }
    return true;
  });
}


function eraSummary(era) {
  if (!era.summary) return "";
  const credit = era.summary_url
    ? ` <a class="src" href="${esc(era.summary_url)}" target="_blank" rel="noopener noreferrer" title="Text from Wikipedia, CC BY-SA">Wikipedia</a>` : "";
  return `<p class="era-summary">${esc(era.summary)}${credit}</p>`;
}

// "Tha Carter III era" -- an era is named after its album, but it is a period, not the album.
function eraName(era) {
  const name = (era && (era.name || era)) || "";
  return /\bera$/i.test(name) ? name : `${name} era`;
}

function eraOf(song) {
  return state.eraById.get(song.era) || null;
}

// The era's album cover; a song without an era (or an era without a cover) shows
// the photo of Lil Wayne from the About page (Wikimedia Commons, CC BY 2.0).
function coverImg(era, size) {
  const file = era && era.cover_file ? era.cover_file : "artist.jpg";
  return `<img class="cover ${size}" src="covers/${esc(file)}" alt="" loading="lazy">`;
}

// The cover of the release a song came out on; the era's album cover otherwise.
function songCover(song, size) {
  return song.cover
    ? `<img class="cover ${size}" src="covers/${esc(song.cover)}" alt="" loading="lazy">`
    : coverImg(eraOf(song), size);
}

// Who a song is by: the credit of its album ("Hot Boy$", "Birdman & Lil Wayne"), a guest
// song's own artist, nothing for Lil Wayne's own solo songs.
function songArtist(song) {
  return song.credit || (song.guest || song.artist !== "Lil Wayne" ? song.artist : "");
}

function songMeta(song) {
  return `${songArtist(song) ? `<div class="artist">${esc(songArtist(song))}</div>` : ""}
    ${song.aliases.length ? `<div class="alias">also: ${esc(song.aliases.slice(0, 3).join(", "))}</div>` : ""}
    ${song.producers.length ? `<div class="artist">prod. ${esc(song.producers.slice(0, 3).join(", "))}${song.producers.length > 3 ? "…" : ""}</div>` : ""}`;
}

const VIEWS = {
  list: (songs) => `<table class="rows">${songs.map((song) => {
    const era = eraOf(song);
    return `<tr data-id="${song.id}">
      <td class="thumb">${songCover(song, "small")}</td>
      <td><div class="title">${esc(song.title)}</div>${songMeta(song)}${playButton(song.play, `data-play-song="${song.id}"`)}</td>
      <td class="era-cell">${song.album ? `<a class="album-link" href="#/release/${song.album.id}" title="Album / project">${esc(song.album.title)}</a>`
        : `<span class="muted">No release</span>`}
        <div class="era-line">${era ? `<a href="#/era/${era.id}">${esc(eraName(era))}</a>` : "No era"}</div></td>
      <td class="types">${song.types.map((t) => `<span class="tag">${esc(VERSION_TYPES[t] || t)}</span>`).join("")}</td>
      <td>${badge(song.status)}</td>
      <td class="date">${esc(fmtDate(song.date))}</td>
      <td class="file">${song.file ? "♪ file" : ""}</td></tr>`;
  }).join("")}</table>`,
  grid: (songs) => `<div class="grid">${songs.map((song) => {
    const era = eraOf(song);
    return `<a class="card" href="#/song/${song.id}">${songCover(song, "card-cover")}
      <div class="card-body"><div class="title">${esc(song.title)}</div>
        ${songArtist(song) ? `<div class="artist">${esc(songArtist(song))}</div>` : ""}
        <div class="card-foot">${badge(song.status)}<span class="date">${esc((song.date || "").slice(0, 4))}</span>
          ${song.file ? `<span class="file">♪</span>` : ""}</div>
        <div class="card-album" title="Album / project">${song.album ? esc(song.album.title) : "No release"}</div>
        <div class="artist">${esc(era ? eraName(era) : "No era")}</div>
        ${playButton(song.play, `data-play-song="${song.id}"`)}</div></a>`;
  }).join("")}</div>`,
  compact: (songs) => `<table class="rows compact">${songs.map((song) => {
    const era = eraOf(song);
    return `<tr data-id="${song.id}"><td class="play-cell">${playButton(song.play, `data-play-song="${song.id}"`, "")}</td><td class="title">${esc(song.title)}</td>
      <td class="artist">${esc(songArtist(song))}</td>
      <td class="artist">${song.album ? esc(song.album.title) : ""}</td>
      <td class="artist">${esc(era ? eraName(era) : "")}</td>
      <td><span class="dot ${esc(song.status)}" title="${esc(STATUS[song.status])}"></span></td>
      <td class="date">${esc(fmtDate(song.date))}</td><td class="file">${song.file ? "♪" : ""}</td></tr>`;
  }).join("")}</table>`,
};

/* ---------- filters are remembered across reloads (this browser only) ---------- */
const FILTERS_KEY = "lwt-filters";
const FILTER_FIELDS = ["search", "era", "kind", "vtype", "sort", "release-search", "discography", "release-type",
                       "people-search", "people-role", "people-sort"];
function saveFilters() {
  if (!state.filtersRestored) return;  // nothing is saved before the remembered ones are back
  const saved = Object.fromEntries(FILTER_FIELDS.map((id) => [id, $(id).value]));
  saved.statuses = [...state.statuses];
  try { localStorage.setItem(FILTERS_KEY, JSON.stringify(saved)); } catch (e) { /* storage unavailable */ }
}
function restoreFilters() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(FILTERS_KEY)) || {}; } catch (e) { saved = {}; }
  for (const id of FILTER_FIELDS) {
    const field = $(id);
    if (typeof saved[id] !== "string") continue;
    // a select only takes a value it still offers (an era or type may be gone)
    if (field.tagName !== "SELECT" || [...field.options].some((o) => o.value === saved[id])) field.value = saved[id];
  }
  if (Array.isArray(saved.statuses)) {
    state.statuses = new Set(saved.statuses.filter((s) => STATUS[s]));
    for (const chip of document.querySelectorAll("#status-filter .chip")) chip.classList.toggle("on", state.statuses.has(chip.dataset.status));
  }
  state.filtersRestored = true;
  updateFilterCount();
}

function renderSongs() {
  saveFilters();
  const base = filteredSongs();
  for (const chip of document.querySelectorAll(".chip")) {
    chip.querySelector("span").textContent = base.filter((s) => s.status === chip.dataset.status).length;
  }
  const songs = base.filter((s) => state.statuses.has(s.status)).sort(SORTS[$("sort").value] || SORTS.date);
  $("shown").textContent = `Showing ${plural(songs.length, "song")}`;
  state.shown = songs;
  state.limit = PAGE;
  $("song-list").innerHTML = (VIEWS[state.view] || VIEWS.list)(songs.slice(0, PAGE));
  requestAnimationFrame(fillViewport);
}

// Infinite scroll: the next PAGE songs are appended when the end of the list comes into view.
function appendSongs() {
  if (!state.shown || state.limit >= state.shown.length || $("songs-view").hidden) return;
  const chunk = state.shown.slice(state.limit, state.limit + PAGE);
  state.limit += chunk.length;
  const holder = document.createElement("div");
  holder.innerHTML = (VIEWS[state.view] || VIEWS.list)(chunk);
  const target = $("song-list").firstElementChild;
  const items = holder.firstElementChild.querySelectorAll(":scope > tbody > tr, :scope > tr, :scope > a");
  const body = target.tBodies ? target.tBodies[0] || target : target;
  for (const item of items) body.appendChild(item);
}
// The observer fires only when the end of the list *enters* the view; when it is
// still in view after a chunk (tall window, short rows) keep filling.
function fillViewport() {
  if ($("songs-view").hidden || !state.shown || state.limit >= state.shown.length) return;
  if ($("song-sentinel").getBoundingClientRect().top < window.innerHeight + 600) {
    appendSongs();
    requestAnimationFrame(fillViewport);
  }
}
new IntersectionObserver((entries) => {
  if (entries.some((e) => e.isIntersecting)) fillViewport();
}, { rootMargin: "600px" }).observe($("song-sentinel"));

const VIEW_LABELS = { list: "☰ List", grid: "▦ Grid", compact: "≡ Compact" };

function showViewChoice() {
  document.querySelectorAll("#view-switch button").forEach((b) => b.classList.toggle("on", b.dataset.view === state.view));
  $("view-button").textContent = VIEW_LABELS[state.view];
}

function setView(view) {
  state.view = VIEWS[view] ? view : "list";
  showViewChoice();
  $("view-menu").open = false;
  try { localStorage.setItem("lwt-view", state.view); } catch (e) { /* storage unavailable */ }
  renderSongs();
}

function updateFilterCount() {
  const active = [$("era").value, $("kind").value !== "own" ? "x" : "", $("vtype").value, $("sort").value !== "date" ? "x" : ""]
    .filter(Boolean).length;
  $("filter-count").textContent = active ? `(${active})` : "";
}

async function openSong(id) {
  const song = await getJSON(`/api/v1/songs/${id}`);
  state.song = song;
  const main = song.versions.find((v) => v.version_type === "album" && playable(v.play)) || song.versions.find((v) => playable(v.play));
  // The album version is shown as "Appears on" (its releases, editions grouped);
  // "Other versions" lists only remixes, demos, snippets ... with their own links.
  const albumTracks = song.versions.filter((v) => v.version_type === "album").flatMap((v) => v.tracks);
  const others = song.versions.filter((v) => v.version_type !== "album");
  const versions = others.map((version) => `
    <div class="version">
      <div class="head"><strong>${esc(version.version_name || VERSION_TYPES[version.version_type] || "Version")}</strong>
        <span class="tag">${esc(VERSION_TYPES[version.version_type] || version.version_type || "")}</span>
        ${badge(version.availability)} ${version.files ? `<span class="file">♪ ${plural(version.files, "file")}</span>` : ""}
        ${playButton(version.play, `data-play-version="${version.id}"`, "Play")}
        ${(version.links || []).map((l) => `<a class="mini-listen" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer"
          title="${esc(l.url)}" style="--svc:${serviceOf(l.url).color}">▶ ${esc(serviceOf(l.url).name)}</a>`).join("")}</div>
      ${version.first_known_date ? `<div class="track">Known since ${esc(version.first_known_date)}</div>` : ""}
      ${appearsOn(version.tracks, song.title) || `<div class="track">Not on a release</div>`}
    </div>`).join("");
  const appears = appearsOn(albumTracks, song.title)
    || `<p class="muted">${song.versions.length ? "Not on a release (yet)." : "No versions yet: this song is known from sources only."}</p>`;
  const roles = Object.keys(song.credits).sort((a, b) => {
    const ia = ROLE_ORDER.indexOf(a), ib = ROLE_ORDER.indexOf(b);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
  });
  const roleName = (role) => ROLES[role] || role.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
  // One entry per person and role; versions are named only when the credit is not on the song itself.
  const credits = roles.map((role) => {
    const people = new Map();
    for (const c of song.credits[role]) {
      const entry = people.get(c.id) || { id: c.id, name: c.name, song: false, versions: new Set(), sources: new Set() };
      if (c.version) entry.versions.add(c.version); else entry.song = true;
      if (c.source) entry.sources.add(c.source);
      people.set(c.id, entry);
    }
    return `<dt>${esc(roleName(role))}</dt><dd>${[...people.values()].map((p) =>
      `<a href="#/artist/${p.id}">${esc(p.name)}</a>`
      + (!p.song && p.versions.size ? ` <span class="src">(${esc([...p.versions].join(", "))})</span>` : "")
      + (p.sources.size ? ` <span class="src">${esc([...p.sources].join(", "))}</span>` : "")).join(", ")}</dd>`;
  }).join("");
  const urls = [];
  if (song.musicbrainz_recording_id) urls.push(`https://musicbrainz.org/recording/${song.musicbrainz_recording_id}`);
  for (const source of song.sources) if (source.url) urls.push(source.url);
  // "Listen on" holds the players; a Discogs release page is a source.
  const isSource = (url) => /discogs\.com/.test(url);
  urls.push(...song.listen.map((l) => l.url).filter(isSource));
  song.listen = song.listen.filter((l) => !isSource(l.url));
  const listed = state.songs.find((s) => s.id === song.id) || {};
  const headCover = song.cover_file ? `<img class="cover big" src="covers/${esc(song.cover_file)}" alt="">`
    : coverImg(eraOf(listed), "big");
  $("panel-body").innerHTML = `
    <div class="panel-head">${headCover}<div>
      <h2>${esc(song.title)}</h2>
      <div class="muted">${song.credit ? `<span class="credit" title="Credited on ${esc(listed.album ? listed.album.title : "the release")}">${esc(song.credit)}</span>`
        : `<a href="#/artist/${song.artist_id}">${esc(song.artist)}</a>`}${song.appearance === "guest" ? " · guest appearance" : ""}
        · ${listed.era ? `<a href="#/era/${listed.era}">${esc(eraName(song.era))}</a>` : esc(song.era ? eraName(song.era) : "no era")} ${badge(song.availability)}</div>
      ${song.aliases.length ? `<p class="muted">Also known as: ${esc(song.aliases.join(", "))}</p>` : ""}
      ${main ? `<div class="head-play">${playButton(main.play, `data-play-version="${main.id}"`)}</div>` : ""}
      ${song.listen.length ? `<div class="listen">${serviceLinks(song.listen.map((l) => l.url))}</div>` : ""}
      ${lyricsLink(song)}
    </div></div>
    <h3>Appears on</h3>${appears}
    ${others.length ? `<h3>Other versions</h3>${compareVersions(song)}${versions}` : ""}
    <h3>Credits</h3>${credits ? `<dl class="credits">${credits}</dl>` : `<p class="muted">No credits yet.</p>`}
    <h3>Sources</h3>${serviceLinks(urls) || `<p class="muted">No links.</p>`}`;
  showPanel();
}

// Releases a version is on: one entry per release group (its first edition), the other
// editions under it ("Target limited edition", or date and track count); bootlegs after
// the official releases, folded when there are official ones.
function appearsOn(tracks, songTitle) {
  if (!tracks.length) return "";
  const groups = new Map();
  for (const track of tracks) {
    if (!groups.has(track.group_id)) groups.set(track.group_id, []);
    groups.get(track.group_id).push(track);
  }
  const order = (a, b) => (a.release_date || "9999").localeCompare(b.release_date || "9999") || a.release_id - b.release_id;
  const entries = [...groups.values()].map((items) => items.sort(order)).sort((a, b) => order(a[0], b[0]));
  const editionName = (t) => t.edition ? `${t.edition} (${t.release_tracks} tracks)`
    : `${fmtDate(t.release_date) || "no date"} (${t.release_tracks} tracks)`;
  const entry = (items) => {
    const t = items[0];
    const type = (RELEASE_TYPES[t.release_type] || t.release_type || "").replace(/s$/, "");
    const position = `track ${t.disc_number > 1 ? `${t.disc_number}-` : ""}${t.track_number}`;
    const spelled = t.track_title && t.track_title !== songTitle ? ` · “${esc(t.track_title)}”` : "";
    return `<div class="appear">
      <a class="appear-main" href="#/release/${t.release_id}">
        <img class="cover small" src="covers/${esc(t.cover_file || "artist.jpg")}" alt="" loading="lazy">
        <span><span class="title">${esc(t.release)}</span>
          <span class="muted">${esc(type)} · ${esc(fmtDate(t.release_date) || "no date")} · ${position}${spelled}${t.duration_ms ? ` · ${duration(t.duration_ms)}` : ""}</span></span></a>
      ${items.length > 1 ? `<div class="editions">${items.length} editions: ${items.map((e) =>
        `<a href="#/release/${e.release_id}">${esc(editionName(e))}</a>`).join(" · ")}</div>` : ""}
    </div>`;
  };
  const official = entries.filter((items) => items[0].discography === "official");
  const bootlegs = entries.filter((items) => items[0].discography !== "official");
  const unofficial = bootlegs.map(entry).join("");
  return official.map(entry).join("") + (!bootlegs.length ? "" : official.length
    ? `<details class="bootlegs"><summary>Also on ${plural(bootlegs.length, "bootleg")}</summary>${unofficial}</details>` : unofficial);
}

// Lyrics stay on Genius (they are copyrighted): only a link to the song page there.
function lyricsLink(song) {
  const url = (song.sources.find((s) => /genius\.com\/.+-lyrics/.test(s.url || "")) || {}).url;
  return url ? `<a class="lyrics" href="${esc(url)}" target="_blank" rel="noopener noreferrer">✎ Lyrics on Genius</a>` : "";
}

// All versions side by side: type, status, first appearance, lengths, releases, files.
function compareVersions(song) {
  if (song.versions.length < 2) return "";
  const rows = song.versions.map((v) => {
    const dates = v.tracks.map((t) => t.release_date).filter(Boolean).sort();
    const lengths = v.tracks.map((t) => t.duration_ms).filter(Boolean);
    const length = lengths.length ? (Math.min(...lengths) === Math.max(...lengths) ? duration(lengths[0])
      : `${duration(Math.min(...lengths))}–${duration(Math.max(...lengths))}`) : "";
    return `<tr><td>${esc(v.version_name || VERSION_TYPES[v.version_type] || "")}</td>
      <td>${esc(VERSION_TYPES[v.version_type] || v.version_type || "")}</td><td>${badge(v.availability)}</td>
      <td>${esc(dates[0] || v.first_known_date || "")}</td><td>${length}</td>
      <td>${v.tracks.length || ""}</td><td>${v.files || ""}</td></tr>`;
  }).join("");
  return `<details class="table-view compare"><summary>Compare ${song.versions.length} versions</summary>
    <table><thead><tr><th>Version</th><th>Type</th><th>Status</th><th>First</th><th>Length</th><th>Releases</th>
    <th>Files</th></tr></thead><tbody>${rows}</tbody></table></details>`;
}

/* ---------- releases ---------- */

const RELEASE_VIEWS = ["list", "grid", "compact"];
const capital = (text) => text ? text.charAt(0).toUpperCase() + text.slice(1) : "";

// One entry per album: the first edition stands for its release group; the other
// editions (deluxe, bonus, Target ...) are listed in the release window.
function releaseEntries(list) {
  const groups = new Map();
  for (const release of list) {
    if (!groups.has(release.group_id)) groups.set(release.group_id, []);
    groups.get(release.group_id).push(release);
  }
  const date = (r) => (r.release_date && r.release_date.length >= 10 ? r.release_date : `${r.release_date || "9999"}~`);
  return [...groups.values()].map((items) => {
    items.sort((a, b) => date(a).localeCompare(date(b)) || a.id - b.id);
    return { ...items[0], editions: items.length };
  }).sort((a, b) => (a.release_date || "9999").localeCompare(b.release_date || "9999") || a.title.localeCompare(b.title));
}

function releaseCover(r, size) {
  return `<img class="cover ${size}" src="covers/${esc(r.cover_file || "artist.jpg")}" alt="" loading="lazy">`;
}

const RELEASE_RENDER = {
  list: (items) => `<div class="release-rows">${items.map((r) => `<div class="release-row" data-release="${r.id}">
      ${releaseCover(r, "medium")}<span class="release-text"><span class="title">${esc(r.title)}</span>
      ${r.artist_credit ? `<span class="release-credit">${esc(r.artist_credit)}</span>` : ""}
      <span class="muted">${esc(fmtDate(r.release_date) || "no date")} · ${plural(r.tracks, "track")}${r.editions > 1
        ? ` · <span class="editions-badge">${r.editions} editions</span>` : ""}</span></span></div>`).join("")}</div>`,
  grid: (items) => `<div class="grid">${items.map((r) => `<a class="card" href="#/release/${r.id}">${releaseCover(r, "card-cover")}
      <div class="card-body"><div class="title">${esc(r.title)}</div>
        ${r.artist_credit ? `<div class="artist">${esc(r.artist_credit)}</div>` : ""}
        <div class="artist">${esc((r.release_date || "").slice(0, 4) || "no date")} · ${plural(r.tracks, "track")}</div>
        ${r.editions > 1 ? `<div class="artist"><span class="editions-badge">${r.editions} editions</span></div>` : ""}</div></a>`).join("")}</div>`,
  compact: (items) => `<table class="rows compact">${items.map((r) => `<tr data-release="${r.id}">
      <td class="title">${esc(r.title)}</td><td class="date">${esc(fmtDate(r.release_date) || "")}</td>
      <td class="artist">${plural(r.tracks, "track")}</td><td class="artist">${r.editions > 1 ? `${r.editions} editions` : ""}</td></tr>`).join("")}</table>`,
};

function renderReleases() {
  saveFilters();
  try { state.releaseView = RELEASE_VIEWS.includes(state.releaseView) ? state.releaseView : localStorage.getItem("lwt-release-view") || "list"; }
  catch (e) { state.releaseView = state.releaseView || "list"; }
  if (!RELEASE_VIEWS.includes(state.releaseView)) state.releaseView = "list";
  for (const b of document.querySelectorAll("#release-views button")) b.classList.toggle("on", b.dataset.view === state.releaseView);
  $("release-view-button").textContent = RELEASE_VIEW_LABELS[state.releaseView];
  const query = fold($("release-search").value.trim());
  const discography = $("discography").value;
  const type = $("release-type").value;
  $("release-filter-count").textContent = discography !== "official" || type ? `(${(discography !== "official") + Boolean(type)})` : "";
  // a query matches any edition, the entry shows the album
  const groupsHit = new Set(state.releases.filter((r) => r.discography === discography && (!type || r.release_type === type)
    && (!query || fold(`${r.title} ${r.artist_credit || ""}`).includes(query))).map((r) => r.group_id));
  const entries = releaseEntries(state.releases.filter((r) => r.discography === discography && groupsHit.has(r.group_id)));
  const byType = new Map();
  for (const entry of entries) {
    if (!byType.has(entry.release_type)) byType.set(entry.release_type, []);
    byType.get(entry.release_type).push(entry);
  }
  state.releaseOpen = state.releaseOpen || {};
  const order = Object.keys(RELEASE_TYPES);
  $("release-total").textContent = `${plural(entries.length, "release")} · ${plural(groupsHit.size ? state.releases.filter((r) => groupsHit.has(r.group_id)).length : 0, "edition")}`;
  $("release-list").innerHTML = [...byType.entries()]
    .sort((a, b) => order.indexOf(a[0]) - order.indexOf(b[0]))
    .map(([type, items]) => `<details class="release-group" data-type="${esc(type)}" ${state.releaseOpen[type] === false ? "" : "open"}>
      <summary><span class="chevron" aria-hidden="true"></span>${esc(RELEASE_TYPES[type] || type)} <span class="muted">${items.length}</span></summary>
      ${RELEASE_RENDER[state.releaseView](items)}</details>`).join("")
    || `<p class="muted">Nothing found.</p>`;
}
const RELEASE_VIEW_LABELS = { list: "☰ List", grid: "▦ Grid", compact: "≡ Compact" };
$("release-type").addEventListener("change", renderReleases);
$("reset-release-filters").addEventListener("click", () => {
  $("discography").value = "official";
  $("release-type").value = "";
  refreshChips($("release-filter-menu"));
  renderReleases();
});
$("release-views").addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]");
  if (!button) return;
  $("release-view-menu").open = false;
  state.releaseView = button.dataset.view;
  try { localStorage.setItem("lwt-release-view", state.releaseView); } catch (e) { /* storage unavailable */ }
  renderReleases();
});
$("release-list").addEventListener("toggle", (event) => {  // remember folded categories
  const group = event.target.closest(".release-group");
  if (group) state.releaseOpen[group.dataset.type] = group.open;
}, true);

async function openRelease(id) {
  const release = await getJSON(`/api/v1/releases/${id}`);
  state.release = release;
  const anyPlayable = release.tracks.some((t) => playable(t.play));
  const urls = release.listen.map((l) => l.url);
  if (release.musicbrainz_release_id) urls.push(`https://musicbrainz.org/release/${release.musicbrainz_release_id}`);
  // Music services under the title, reference sources (MusicBrainz, Discogs, ...) below the track list.
  const streaming = urls.filter(isStreaming);
  const sources = urls.filter((url) => !isStreaming(url));
  $("panel-body").innerHTML = `
    <div class="panel-head">${release.cover_file ? `<img class="cover big" src="covers/${esc(release.cover_file)}" alt="">`
      : `<img class="cover big" src="covers/artist.jpg" alt="" loading="lazy">`}<div>
      <h2>${esc(release.title)}</h2>
      ${release.artist_credit ? `<div class="release-credit">${esc(release.artist_credit)}</div>` : ""}
      ${release.edition ? `<div class="edition-label">${esc(capital(release.edition))}</div>` : ""}
      <div class="muted">${esc((RELEASE_TYPES[release.release_type] || release.release_type).replace(/s$/, ""))} · ${esc(fmtDate(release.release_date) || "?")}
        · ${plural(release.tracks.length, "track")}
        ${release.label ? ` · ${esc(release.label)}` : ""} · ${release.discography === "official" ? "official" : "unofficial"}</div>
      ${anyPlayable ? `<div class="head-play"><button type="button" class="play-btn" data-play-release="">▶ Play all</button></div>` : ""}
      ${streaming.length ? `<div class="listen">${serviceLinks(streaming)}</div>` : ""}</div></div>
    <h3>Track list</h3>
    <table class="rows">${release.tracks.map((t) => `<tr data-id="${t.recording_id}">
      <td class="play-cell">${playButton(t.play, `data-play-release="${t.version_id}"`, "")}</td>
      <td class="date">${t.disc_number > 1 ? `${t.disc_number}-` : ""}${t.track_number}</td>
      <td><span class="title">${esc(t.title)}</span>
        ${t.version_type && t.version_type !== "album" ? `<span class="tag">${esc(VERSION_TYPES[t.version_type] || t.version_type)}</span>` : ""}</td>
      <td class="date">${duration(t.duration_ms)}</td><td class="file">${t.file ? "♪" : ""}</td></tr>`).join("")}</table>
    ${(release.editions || []).length ? `<h3>Other editions</h3><div class="other-editions">${release.editions.map((e) =>
      `<a class="mini-card" href="#/release/${e.id}">${releaseCover(release, "small")}<span><span class="title">${esc(e.edition
        ? capital(e.edition) : e.title !== release.title ? e.title : fmtDate(e.release_date) || "Edition")}</span><br>
        <span class="muted">${esc(fmtDate(e.release_date) || "no date")} · ${plural(e.tracks, "track")}${e.tracks !== release.tracks.length
          ? ` (${e.tracks > release.tracks.length ? "+" : ""}${e.tracks - release.tracks.length})` : ""}</span></span></a>`).join("")}</div>` : ""}
    ${sources.length ? `<h3>Sources</h3>${serviceLinks(sources)}` : ""}`;
  showPanel();
}

/* ---------- wiring ---------- */

async function init() {
  const [summary, songs, releases, people] = await Promise.all([
    getJSON("/api/v1/summary"), getJSON("/api/v1/songs"), getJSON("/api/v1/releases"), getJSON("/api/v1/artists")]);
  state.songs = songs;
  state.eras = summary.eras;
  state.eraById = new Map(summary.eras.map((e) => [e.id, e]));
  state.releases = releases;
  state.people = people;
  try { state.view = loadSettings().view || localStorage.getItem("lwt-view") || "list"; } catch (e) { state.view = "list"; }
  if (loadSettings().kind) $("kind").value = loadSettings().kind;
  loadToday().catch(() => { $("notify-body").textContent = "Could not load today's items."; });
  const asked = new URLSearchParams(location.search).get("view");  // ?view=grid
  if (VIEWS[asked]) state.view = asked;
  showViewChoice();
  const c = summary.counts;
  $("totals").textContent = `${c.songs} songs · ${c.guest_songs} guest appearances · ${c.versions} versions · `
    + `${c.releases} releases · ${c.credits} credits · ${c.files} local files`;
  $("era").insertAdjacentHTML("beforeend", summary.eras.map((e) => `<option value="${e.id}">${esc(eraName(e))}</option>`).join("")
    + `<option value="none">No era</option>`);
  const types = [...new Set(songs.flatMap((s) => s.types))].sort();
  $("vtype").insertAdjacentHTML("beforeend", types.map((t) => `<option value="${esc(t)}">${esc(VERSION_TYPES[t] || t)}</option>`).join(""));
  const releaseTypes = [...new Set(releases.map((r) => r.release_type))].sort((a, b) =>
    Object.keys(RELEASE_TYPES).indexOf(a) - Object.keys(RELEASE_TYPES).indexOf(b));
  $("release-type").insertAdjacentHTML("beforeend", releaseTypes.map((t) => `<option value="${esc(t)}">${esc(RELEASE_TYPES[t] || t)}</option>`).join(""));
  restoreFilters();  // the filters of the last visit win over "Songs shown at start"
  refreshChips();
  renderSongs();
  renderReleases();
  renderPeople();
  renderEraGrid();
}

/* ---------- chip groups: a <select data-chips> shown as pill buttons (the select stays the source of truth) ---------- */
function refreshChips(root = document) {
  for (const select of root.querySelectorAll("select[data-chips]")) {
    let group = select.nextElementSibling;
    if (!group || !group.classList.contains("seg")) {
      group = document.createElement("div");
      group.className = "seg";
      group.setAttribute("role", "radiogroup");
      select.after(group);
      select.hidden = true;
      group.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-value]");
        if (!button || button.dataset.value === select.value) return;
        select.value = button.dataset.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      });
      select.addEventListener("change", () => refreshChips(select.parentElement));
    }
    group.innerHTML = [...select.options].map((option) => {
      const on = option.value === select.value;
      const color = option.dataset.color;
      const swatch = color ? `<span class="swatch${color === "cover" ? " cover" : ""}"${color === "cover" ? "" : ` style="background:${esc(color)}"`}></span>` : "";
      return `<button type="button" role="radio" aria-checked="${on}" class="${on ? "on" : ""}" data-value="${esc(option.value)}">${swatch}${esc(option.textContent)}</button>`;
    }).join("");
  }
}

let timer;
const resetAndRender = () => { updateFilterCount(); renderSongs(); };
$("search").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(resetAndRender, 150); });
for (const id of ["era", "kind", "vtype", "sort"]) $(id).addEventListener("change", resetAndRender);
$("reset-filters").addEventListener("click", () => {
  $("era").value = ""; $("kind").value = "own"; $("vtype").value = ""; $("sort").value = "date";
  refreshChips($("filter-menu"));
  resetAndRender();
});
document.addEventListener("click", (event) => {  // close open menus on an outside click
  for (const menu of document.querySelectorAll("details.menu[open]")) {
    if (!menu.contains(event.target)) menu.open = false;
  }
});
$("status-filter").addEventListener("click", (event) => {
  const chip = event.target.closest(".chip");
  if (!chip) return;
  chip.classList.toggle("on");
  if (chip.classList.contains("on")) state.statuses.add(chip.dataset.status);
  else state.statuses.delete(chip.dataset.status);
  resetAndRender();
});
$("view-switch").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-view]");
  if (button) setView(button.dataset.view);
});
$("release-search").addEventListener("input", renderReleases);
$("discography").addEventListener("change", renderReleases);
$("people-search").addEventListener("input", renderPeople);

/* ---------- people ---------- */

function roleName(role) {
  return ROLES[role] || role.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

// Role filter groups (credit roles from Genius / Discogs are many and fine-grained).
const ROLE_GROUPS = {
  producer: (r) => /produc|programm|beat/.test(r),
  writer: (r) => /writer|composer|lyricist|songwriter/.test(r),
  featured: (r) => r === "featured_artist",
  main: (r) => r === "main_artist",
  engineer: (r) => /engineer|mix|master|record/.test(r),
  musician: (r) => /vocal|keyboard|guitar|bass|drum|piano|synth|instrument|perform|horn|strings|scratch/.test(r),
};

function initials(name) {
  return String(name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
}
// A person's photo (people_images.py, Genius) or their initials.
function avatar(person, size) {
  return person.image ? `<img class="avatar ${size}" src="covers/${esc(person.image)}" alt="" loading="lazy">`
    : `<span class="avatar ${size} initials" aria-hidden="true">${esc(initials(person.name))}</span>`;
}
function personYears(p) {
  return p.years && p.years.length ? (p.years[0] === p.years[1] ? esc(p.years[0]) : `${esc(p.years[0])}–${esc(p.years[1])}`) : "";
}

const PEOPLE_VIEWS = { grid: "▦ Grid", list: "☰ List" };
function renderPeople() {
  saveFilters();
  try { state.peopleView = PEOPLE_VIEWS[state.peopleView] ? state.peopleView : localStorage.getItem("lwt-people-view") || "grid"; }
  catch (e) { state.peopleView = state.peopleView || "grid"; }
  if (!PEOPLE_VIEWS[state.peopleView]) state.peopleView = "grid";
  $("people-view-button").textContent = PEOPLE_VIEWS[state.peopleView];
  for (const b of document.querySelectorAll("#people-views button")) b.classList.toggle("on", b.dataset.view === state.peopleView);
  const query = fold($("people-search").value.trim());
  const role = $("people-role").value, sort = $("people-sort").value;
  $("people-filter-count").textContent = role || sort !== "songs" ? `(${Boolean(role) + (sort !== "songs")})` : "";
  const list = state.people.filter((p) => (!query || fold(p.name).includes(query)) && (!role || p.roles.some(ROLE_GROUPS[role])))
    .sort(sort === "name" ? (a, b) => a.name.localeCompare(b.name)
      : sort === "first" ? (a, b) => (a.years[0] || "9999").localeCompare(b.years[0] || "9999") || b.songs - a.songs
        : (a, b) => b.songs - a.songs);
  $("people-total").textContent = `${list.length} ${list.length === 1 ? "person" : "people"} · the most credited people across the catalogue`;
  const roles = (p) => esc(p.roles.slice(0, 3).map(roleName).join(", "));
  $("people-list").innerHTML = !list.length ? `<p class="muted">Nothing found.</p>` : state.peopleView === "list"
    ? `<div class="people-rows">${list.map((p) => `<a class="person-row" href="#/artist/${p.id}">${avatar(p, "medium")}
        <span class="person-text"><span class="title">${esc(p.name)}</span><span class="muted">${roles(p)}</span></span>
        <span class="person-figures"><strong>${p.songs}</strong> ${p.songs === 1 ? "song" : "songs"}<br><span class="muted">${personYears(p)}</span></span></a>`).join("")}</div>`
    : `<div class="grid people-grid">${list.map((p) => `<a class="card person-card" href="#/artist/${p.id}">${avatar(p, "card-avatar")}
        <div class="card-body"><div class="title">${esc(p.name)}</div><div class="artist">${roles(p)}</div>
          <div class="artist">${plural(p.songs, "song")}${personYears(p) ? ` · ${personYears(p)}` : ""}</div></div></a>`).join("")}</div>`;
}
$("people-role").addEventListener("change", renderPeople);
$("people-sort").addEventListener("change", renderPeople);
$("reset-people-filters").addEventListener("click", () => {
  $("people-role").value = ""; $("people-sort").value = "songs";
  refreshChips($("people-filter-menu"));
  renderPeople();
});
$("people-views").addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]");
  if (!button) return;
  $("people-view-menu").open = false;
  state.peopleView = button.dataset.view;
  try { localStorage.setItem("lwt-people-view", state.peopleView); } catch (e) { /* storage unavailable */ }
  renderPeople();
});

// Songs with this person per year of first appearance (the song list's dates).
function collabByYear(person) {
  const byId = new Map(state.songs.map((s) => [s.id, s]));
  const years = {};
  for (const song of person.songs) {
    const year = ((byId.get(song.id) || {}).date || "").slice(0, 4);
    if (year) years[year] = (years[year] || 0) + 1;
  }
  const rows = Object.keys(years).sort().map((y) => ({ label: y, value: years[y] }));
  return rows.length > 1 ? simpleBars("Songs with Lil Wayne by year", rows) : "";
}

async function openArtist(id) {
  const person = await getJSON(`/api/v1/artists/${id}`);
  const roles = Object.entries(person.roles).sort((a, b) => b[1] - a[1])
    .map(([role, n]) => `<span class="tag">${esc(roleName(role))} ${n}</span>`).join("");
  $("panel-body").innerHTML = `
    <div class="panel-head">${avatar(person, "big")}<div>
      <h2>${esc(person.name)}</h2>
      <div class="muted">${plural(person.songs.length, "song")} in the catalogue</div>
      <div>${roles}</div></div></div>
    ${collabByYear(person)}
    <h3>Songs</h3>
    <table class="rows">${person.songs.map((s) => `<tr data-id="${s.id}">
      <td><div class="title">${esc(s.title)}</div>
        ${s.guest || s.artist !== "Lil Wayne" ? `<div class="artist">${esc(s.artist)}</div>` : ""}
        <div>${s.roles.map((r) => `<span class="tag">${esc(roleName(r))}</span>`).join("")}</div></td>
      <td class="date">${esc(s.era || "")}</td><td>${badge(s.status)}</td></tr>`).join("")}</table>`;
  showPanel();
}

/* ---------- charts (HTML bars; colours validated with the dataviz palette validator) ---------- */

const STATUS_SERIES = [
  { key: "released", label: "Released", color: "var(--released)" },
  { key: "leaked", label: "Leaked", color: "var(--leaked)" },
  { key: "unreleased", label: "Unreleased", color: "var(--unreleased)" },
];

function legend(series, totals) {
  return `<div class="legend">${series.map((s) => `<span class="legend-item"><span class="swatch" style="background:${s.color}"></span>
    ${esc(s.label)}${totals ? ` <span class="muted">${totals[s.key].toLocaleString()}</span>` : ""}</span>`).join("")}</div>`;
}

// Horizontal stacked bars: rows = [{label, href, values: {key: n}}].
function stackedBars(title, rows, series) {
  const max = Math.max(1, ...rows.map((r) => series.reduce((sum, s) => sum + (r.values[s.key] || 0), 0)));
  const totals = Object.fromEntries(series.map((s) => [s.key, rows.reduce((sum, r) => sum + (r.values[s.key] || 0), 0)]));
  const body = rows.map((r) => {
    const total = series.reduce((sum, s) => sum + (r.values[s.key] || 0), 0);
    const tip = [r.label, ...series.map((s) => `${s.label}: ${r.values[s.key] || 0}`)].join("|");
    const label = r.href ? `<a href="${r.href}">${esc(r.label)}</a>` : esc(r.label);
    return `<div class="bar-row" data-tip="${esc(tip)}"><div class="bar-label">${label}</div>
      <div class="bar-track"><div class="bar-stack" style="width:${(total / max) * 100}%">${series.filter((s) => r.values[s.key])
        .map((s) => `<span class="bar-seg" style="flex:${r.values[s.key]};background:${s.color}"></span>`).join("")}</div>
        <span class="bar-value">${total.toLocaleString()}</span></div></div>`;
  }).join("");
  const table = `<details class="table-view"><summary>Table</summary><table><thead><tr><th></th>${series.map((s) =>
    `<th>${esc(s.label)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr><td>${esc(r.label)}</td>${series.map((s) =>
    `<td>${(r.values[s.key] || 0).toLocaleString()}</td>`).join("")}</tr>`).join("")}</tbody></table></details>`;
  return `<section class="chart"><h3>${esc(title)}</h3>${legend(series, totals)}${body}${table}</section>`;
}

// One series: rows = [{label, href, value}] -- the title names it, so no legend.
function simpleBars(title, rows) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  return `<section class="chart"><h3>${esc(title)}</h3>${rows.map((r) => `<div class="bar-row" data-tip="${esc(`${r.label}|${r.value} songs`)}">
    <div class="bar-label">${r.href ? `<a href="${r.href}">${esc(r.label)}</a>` : esc(r.label)}</div>
    <div class="bar-track"><div class="bar-stack" style="width:${(r.value / max) * 100}%"><span class="bar-seg" style="flex:1;background:var(--accent)"></span></div>
    <span class="bar-value">${r.value.toLocaleString()}</span></div></div>`).join("")}</section>`;
}

const tooltip = document.createElement("div");
tooltip.className = "tooltip";
tooltip.hidden = true;
document.body.appendChild(tooltip);
document.addEventListener("mousemove", (event) => {
  const row = event.target.closest("[data-tip]");
  if (!row) { tooltip.hidden = true; return; }
  const [head, ...lines] = row.dataset.tip.split("|");
  tooltip.innerHTML = `<strong>${esc(head)}</strong>${lines.map((l) => `<div>${esc(l)}</div>`).join("")}`;
  tooltip.hidden = false;
  const x = Math.min(event.clientX + 14, window.innerWidth - tooltip.offsetWidth - 8);
  tooltip.style.left = `${x}px`;
  tooltip.style.top = `${event.clientY + 14}px`;
});

/* ---------- about Lil Wayne ---------- */

async function renderAbout() {
  const p = await getJSON("/api/v1/profile");
  const own = state.songs.filter((s) => !s.guest);
  const born = p.born ? new Date(`${p.born}T12:00:00`).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" }) : "";
  const number = (n, label) => `<div class="number"><strong>${n.toLocaleString()}</strong><span>${esc(label)}</span></div>`;
  const group = (title, links) => links && links.length ? `<h3>${esc(title)}</h3>${serviceLinks(links.map((l) => l.url))}` : "";
  const photo = p.photo ? `<figure class="about-photo"><img src="covers/${esc(p.photo.file)}" alt="Lil Wayne performing">
    <figcaption>Photo: ${esc(p.photo.author || "unknown")}, ${esc(p.photo.licence || "")} —
      <a href="${esc(p.photo.page)}" target="_blank" rel="noopener noreferrer">Wikimedia Commons</a></figcaption></figure>` : "";
  $("about").innerHTML = `<div class="about"><div class="about-hero">${photo}<div>
    <h2>${esc(p.name)}</h2>
    <div class="facts">Dwayne Michael Carter Jr.${born ? ` · born ${esc(born)}` : ""}${p.origin ? ` · ${esc(p.origin)}` : ""}</div>
    <p class="summary">${esc(p.summary || "")}${p.summary_url ? ` <a class="src" href="${esc(p.summary_url)}" target="_blank"
      rel="noopener noreferrer" title="Text from Wikipedia, CC BY-SA">Wikipedia</a>` : ""}</p>
    <div class="numbers">${number(own.length, "songs in the tracker")}${number(own.filter((s) => s.status === "released").length, "released")}
      ${number(own.filter((s) => s.status === "leaked").length, "leaked")}${number(state.songs.length - own.length, "guest appearances")}
      ${number(state.eras.length, "eras")}</div>
    ${group("Follow", p.links.social)}${group("Listen", p.links.listen)}</div></div>
    <h3>Eras</h3><div class="era-strip">${state.eras.filter((e) => e.cover_file).map((e) =>
      `<a href="#/era/${e.id}" title="${esc(e.name)}"><img src="covers/${esc(e.cover_file)}" alt="${esc(e.name)}" loading="lazy"></a>`).join("")}</div>
    ${group("Websites", p.links.official)}${group("More about Lil Wayne", [...p.links.reference, { url: p.musicbrainz }])}</div>`;
  state.aboutLoaded = true;
}

/* ---------- eras ---------- */

const ERA_VIEWS = ["grid", "list"];
function eraPeriod(era) {
  return `${esc(fmtDate(era.start_date) || "…")} – ${esc(era.end_date ? fmtDate(era.end_date) : "today")}`;
}
function renderEraGrid() {
  try { state.eraView = ERA_VIEWS.includes(state.eraView) ? state.eraView : localStorage.getItem("lwt-era-view") || "grid"; }
  catch (e) { state.eraView = state.eraView || "grid"; }
  for (const b of document.querySelectorAll("#era-views button")) b.classList.toggle("on", b.dataset.view === state.eraView);
  $("era-view-button").textContent = state.eraView === "list" ? "☰ List" : "▦ Grid";
  $("era-total").textContent = plural(state.eras.length, "era");
  const cards = state.eras.map((era) => {
    const songs = state.songs.filter((s) => s.era === era.id && !s.guest);
    const counts = STATUS_SERIES.map((s) => [s, songs.filter((x) => x.status === s.key).length]);
    const badges = `<div class="era-counts">${counts.map(([s, n]) => `<span class="badge ${s.key}" title="${esc(s.label)}">${n}</span>`).join("")}</div>`;
    if (state.eraView === "list") {
      return `<a class="era-row" href="#/era/${era.id}">${coverImg(era, "medium")}
        <span class="era-row-text"><span class="title">${esc(eraName(era))}</span>
          <span class="muted">${eraPeriod(era)} · ${plural(songs.length, "song")}</span>
          ${era.summary ? `<span class="era-row-summary">${esc(era.summary)}</span>` : ""}</span>${badges}</a>`;
    }
    return `<a class="era-card" href="#/era/${era.id}">${coverImg(era, "card-cover")}
      <div class="card-body"><div class="title">${esc(eraName(era))}</div>
        <div class="artist">${eraPeriod(era)}</div>${badges}</div></a>`;
  }).join("");
  $("era-grid").className = state.eraView === "list" ? "era-list" : "era-grid";
  $("era-grid").innerHTML = cards;
}
$("era-views").addEventListener("click", (event) => {
  const button = event.target.closest("[data-view]");
  if (!button) return;
  $("era-view-menu").open = false;
  state.eraView = button.dataset.view;
  try { localStorage.setItem("lwt-era-view", state.eraView); } catch (e) { /* storage unavailable */ }
  renderEraGrid();
});

async function openEra(id) {
  const era = await getJSON(`/api/v1/eras/${id}`);
  const own = era.songs.filter((s) => !s.guest);
  const counts = Object.fromEntries(STATUS_SERIES.map((s) => [s.key, own.filter((x) => x.status === s.key).length]));
  const releaseCards = era.releases.map((r) => `<a class="card" href="#/release/${r.id}">${releaseCover(r, "card-cover")}
    <div class="card-body"><div class="title">${esc(r.title)}</div>
      <div class="artist">${esc((RELEASE_TYPES[r.release_type] || r.release_type).replace(/s$/, ""))} · ${esc(fmtDate(r.release_date))}</div>
      ${r.editions > 1 ? `<div class="artist"><span class="editions-badge">${r.editions} editions</span></div>` : ""}</div></a>`).join("");
  const people = (list) => list.map((p) => `<a href="#/artist/${p.id}">${esc(p.name)}</a> <span class="muted">${p.songs}</span>`).join(" · ");
  const songList = (status) => own.filter((s) => s.status === status)
    .map((s) => `<a href="#/song/${s.id}">${esc(s.title)}</a>`).join(" · ");
  $("era-detail").innerHTML = `
    <a class="back" href="#/eras">← All eras</a>
    <div class="era-hero">${coverImg(era, "hero")}<div>
      <h2>${esc(eraName(era))}</h2>
      <div class="muted">${eraPeriod(era)} · ${plural(own.length, "song")}
        · ${plural(era.songs.length - own.length, "guest appearance")}</div>
      ${eraSummary(era)}
      ${stackedBars("Songs of this era", [{ label: eraName(era), values: counts }], STATUS_SERIES)}
    </div></div>
    ${era.releases.length ? `<h3>Official releases of the era</h3><div class="grid era-releases">${releaseCards}</div>` : ""}
    ${era.producers.length ? `<h3>Producers</h3><p>${people(era.producers)}</p>` : ""}
    ${era.featured.length ? `<h3>Featured artists</h3><p>${people(era.featured)}</p>` : ""}
    ${STATUS_SERIES.map((s) => counts[s.key] ? `<h3><span class="badge ${s.key}">${s.label}</span> ${counts[s.key]}</h3>
      <p class="song-cloud">${songList(s.key)}</p>` : "").join("")}`;
  $("era-grid").hidden = true;
  $("era-view-bar").hidden = true;
  $("era-detail").hidden = false;
  window.scrollTo({ top: 0 });
}

/* ---------- statistics ---------- */

/* ---------- statistics: interactive charts ----------
 * Legends are buttons (show / hide a status in that chart; the axis follows the shown
 * series); the figures on top keep only one status in every chart; time charts switch
 * between columns and lines, era charts between counts and shares. Colours: the
 * validated status palette; values and labels in text colours; table view per chart. */

const stats = { charts: [], view: {}, solo: null };

function niceMax(value) {
  if (value <= 0) return 1;
  const step = 10 ** Math.floor(Math.log10(value));
  return [1, 2, 2.5, 5, 10].map((m) => m * step).find((m) => m >= value) || value;
}
function chartView(chart) {
  return (stats.view[chart.id] ||= { hidden: new Set(), type: chart.types ? chart.types[0][0] : "ranked" });
}
function shownSeries(chart) {
  const view = chartView(chart);
  return chart.series.filter((s) => !view.hidden.has(s.key));
}
const rowTotal = (row, series) => series.reduce((sum, s) => sum + (row.values[s.key] || 0), 0);

function chartLegend(chart) {
  const view = chartView(chart);
  const totals = Object.fromEntries(chart.series.map((s) => [s.key, chart.rows.reduce((sum, r) => sum + (r.values[s.key] || 0), 0)]));
  return `<div class="legend" role="group" aria-label="Shown statuses">${chart.series.map((s) => `<button type="button"
    class="legend-item toggle${view.hidden.has(s.key) ? " off" : ""}" data-chart="${chart.id}" data-series="${s.key}"
    aria-pressed="${!view.hidden.has(s.key)}" title="Show / hide ${esc(s.label)}"><span class="swatch" style="background:${s.color}"></span>
    ${esc(s.label)} <span class="muted">${totals[s.key].toLocaleString()}</span></button>`).join("")}</div>`;
}

function chartTypes(chart) {
  const view = chartView(chart);
  return chart.types ? `<div class="seg chart-types" role="group" aria-label="Chart type">${chart.types.map(([key, label]) =>
    `<button type="button" class="${view.type === key ? "on" : ""}" data-chart="${chart.id}" data-type="${key}">${label}</button>`).join("")}</div>` : "";
}

function chartTable(chart, series) {
  return `<details class="table-view"><summary>Table</summary><table><thead><tr><th></th>${series.map((s) =>
    `<th>${esc(s.label)}</th>`).join("")}</tr></thead><tbody>${chart.rows.map((r) => `<tr><td>${esc(r.label)}</td>${series.map((s) =>
    `<td>${(r.values[s.key] || 0).toLocaleString()}</td>`).join("")}</tr>`).join("")}</tbody></table></details>`;
}

// Horizontal stacked bars, as counts or as shares of each row.
function hBars(chart, series, share) {
  const max = share ? 1 : niceMax(Math.max(1, ...chart.rows.map((r) => rowTotal(r, series))));
  return chart.rows.map((r) => {
    const total = rowTotal(r, series);
    const tip = [r.label, ...series.map((s) => `${s.label}: ${(r.values[s.key] || 0).toLocaleString()}${share && total
      ? ` (${Math.round(100 * (r.values[s.key] || 0) / total)}%)` : ""}`)].join("|");
    const width = share ? (total ? 100 : 0) : (total / max) * 100;
    return `<div class="bar-row" data-tip="${esc(tip)}"><div class="bar-label">${r.href ? `<a href="${r.href}">${esc(r.label)}</a>` : esc(r.label)}</div>
      <div class="bar-track"><div class="bar-stack" style="width:${width}%">${series.filter((s) => r.values[s.key]).map((s) =>
        `<span class="bar-seg" style="flex:${r.values[s.key]};background:${s.color}"></span>`).join("")}</div>
      <span class="bar-value">${total.toLocaleString()}</span></div></div>`;
  }).join("");
}

// Vertical stacked columns over time (bottom: the first series).
function columns(chart, series) {
  const max = niceMax(Math.max(1, ...chart.rows.map((r) => rowTotal(r, series))));
  const step = Math.ceil(chart.rows.length / 12);
  const ticks = [1, 0.75, 0.5, 0.25, 0].map((f) => Math.round(max * f));
  // axis labels and grid lines share one coordinate: a fraction of the plot height above the labels row
  const at = (i) => `calc((100% - 22px) * ${i * 0.25})`;
  return `<div class="col-chart"><div class="col-axis">${ticks.map((t, i) => `<span style="top:${at(i)}">${t.toLocaleString()}</span>`).join("")}</div>
    <div class="col-plot">${ticks.map((t, i) => `<span class="col-grid" style="top:${at(i)}"></span>`).join("")}
    ${chart.rows.map((r, i) => {
      const total = rowTotal(r, series);
      const tip = [r.label, ...series.map((s) => `${s.label}: ${(r.values[s.key] || 0).toLocaleString()}`)].join("|");
      return `<div class="col" data-tip="${esc(tip)}"><div class="col-stack" style="height:${(total / max) * 100}%">${series
        .filter((s) => r.values[s.key]).map((s) => `<span class="bar-seg" style="flex:${r.values[s.key]};background:${s.color}"></span>`).join("")}</div>
        <span class="col-label">${i % step === 0 ? esc(r.label) : ""}</span></div>`;
    }).join("")}</div></div>`;
}

// Lines over time (one per shown series), crosshair + tooltip on hover, labels at the line ends.
const LINE = { w: 760, h: 260, l: 44, r: 92, t: 14, b: 30 };
function lines(chart, series) {
  const rows = chart.rows, { w, h, l, r, t, b } = LINE;
  const max = niceMax(Math.max(1, ...rows.flatMap((row) => series.map((s) => row.values[s.key] || 0))));
  const x = (i) => l + (rows.length < 2 ? 0 : (i * (w - l - r)) / (rows.length - 1));
  const y = (v) => t + (h - t - b) * (1 - v / max);
  const grid = [0, 0.25, 0.5, 0.75, 1].map((f) => `<line class="grid-line" x1="${l}" x2="${w - r}" y1="${y(max * f)}" y2="${y(max * f)}"/>
    <text class="axis-text" x="${l - 8}" y="${y(max * f) + 4}" text-anchor="end">${Math.round(max * f).toLocaleString()}</text>`).join("");
  const step = Math.ceil(rows.length / 12);
  const xLabels = rows.map((row, i) => i % step === 0 ? `<text class="axis-text" x="${x(i)}" y="${h - 8}" text-anchor="middle">${esc(row.label)}</text>` : "").join("");
  const paths = series.map((s) => `<polyline class="line" style="stroke:${s.color}" points="${rows.map((row, i) =>
    `${x(i).toFixed(1)},${y(row.values[s.key] || 0).toFixed(1)}`).join(" ")}"/>`).join("");
  // end labels, pushed apart when they would overlap
  const ends = series.map((s) => ({ s, v: rows.length ? rows[rows.length - 1].values[s.key] || 0 : 0 }))
    .map((e) => ({ ...e, y: y(e.v) })).sort((a, b2) => a.y - b2.y);
  for (let i = 1; i < ends.length; i++) if (ends[i].y - ends[i - 1].y < 15) ends[i].y = ends[i - 1].y + 15;
  const endMarks = ends.map((e) => `<circle class="end-dot" cx="${x(rows.length - 1)}" cy="${y(e.v)}" r="4" style="fill:${e.s.color}"/>
    <text class="end-label" x="${x(rows.length - 1) + 10}" y="${e.y + 4}">${esc(e.s.label)} ${e.v.toLocaleString()}</text>`).join("");
  return `<div class="line-chart"><svg class="line-svg" viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(chart.title)}" data-chart="${chart.id}">
    ${grid}${xLabels}${paths}${endMarks}<line class="crosshair" x1="0" x2="0" y1="${t}" y2="${h - b}" visibility="hidden"/>
    <rect class="hit" x="${l}" y="${t}" width="${w - l - r}" height="${h - t - b}" data-tip=""/></svg></div>`;
}

// One series, ranked; photos for people.
function ranked(chart) {
  const max = Math.max(1, ...chart.rows.map((r) => r.value));
  return chart.rows.map((r) => `<div class="bar-row ranked-row" data-tip="${esc(`${r.label}|${r.value.toLocaleString()} ${chart.unit}`)}">
    <div class="bar-label">${chart.people ? avatar({ name: r.label, image: r.image }, "tiny") : ""}${r.href
      ? `<a href="${r.href}">${esc(r.label)}</a>` : esc(r.label)}</div>
    <div class="bar-track"><div class="bar-stack" style="width:${(r.value / max) * 100}%"><span class="bar-seg" style="flex:1;background:var(--accent)"></span></div>
    <span class="bar-value">${r.value.toLocaleString()}</span></div></div>`).join("");
}

function renderChart(chart) {
  const view = chartView(chart);
  if (!chart.series) {  // ranked, one series: the title names it, no legend
    return `<section class="chart" data-chart-id="${chart.id}"><div class="chart-head"><h3>${esc(chart.title)}</h3></div>${ranked(chart)}
      <details class="table-view"><summary>Table</summary><table><tbody>${chart.rows.map((r) =>
        `<tr><td>${esc(r.label)}</td><td>${r.value.toLocaleString()}</td></tr>`).join("")}</tbody></table></details></section>`;
  }
  const series = shownSeries(chart);
  const body = view.type === "share" ? hBars(chart, series, true) : view.type === "columns" ? columns(chart, series)
    : view.type === "lines" ? lines(chart, series) : hBars(chart, series, false);
  return `<section class="chart${chart.wide ? " wide" : ""}" data-chart-id="${chart.id}">
    <div class="chart-head"><h3>${esc(chart.title)}</h3>${chartTypes(chart)}</div>
    ${chartLegend(chart)}${body}${chartTable(chart, series)}</section>`;
}

function rerenderChart(id) {
  const chart = stats.charts.find((c) => c.id === id);
  const old = document.querySelector(`[data-chart-id="${id}"]`);
  if (chart && old) old.outerHTML = renderChart(chart);
}

function statTiles() {
  const own = state.songs.filter((s) => !s.guest);
  const tile = (key, label, n, color) => `<button type="button" class="stat-tile${stats.solo === key ? " on" : ""}${stats.solo && stats.solo !== key ? " dim" : ""}"
    ${key ? `data-solo="${key}" aria-pressed="${stats.solo === key}"` : "disabled"}>
    <span class="stat-label">${color ? `<span class="swatch" style="background:${color}"></span>` : ""}${esc(label)}</span>
    <span class="stat-value">${n.toLocaleString()}</span>
    <span class="stat-share">${key ? `${Math.round((100 * n) / Math.max(1, own.length))}% of his songs` : "Lil Wayne songs"}</span></button>`;
  return `<div class="stat-tiles">${tile("", "All songs", own.length, "")}${STATUS_SERIES.map((s) =>
    tile(s.key, s.label, own.filter((x) => x.status === s.key).length, s.color)).join("")}</div>
    <p class="muted stat-hint">Click a figure to show only that status in every chart, a legend entry to show or hide it in one chart.</p>`;
}

async function renderStats() {
  const data = await getJSON("/api/v1/stats");
  const images = new Map(state.people.map((p) => [p.id, p.image]));
  const top = (list) => list.map((p) => ({ label: p.name, href: `#/artist/${p.id}`, value: p.songs, image: images.get(p.id) }));
  stats.charts = [
    { id: "year", title: "Songs by year of first appearance", wide: true, series: STATUS_SERIES.slice(0, 2),
      rows: data.by_year.map((y) => ({ label: y.year, values: y })), types: [["columns", "Columns"], ["lines", "Lines"]] },
    { id: "era", title: "Lil Wayne songs by era", wide: true, series: STATUS_SERIES,
      rows: data.by_era.map((e) => ({ label: eraName(e), href: `#/era/${e.id}`, values: e })), types: [["bars", "Counts"], ["share", "Share"]] },
    { id: "producers", title: "Most credited producers", rows: top(data.producers), people: true, unit: "songs" },
    { id: "featured", title: "Most frequent featured artists", rows: top(data.featured), people: true, unit: "songs" },
    { id: "writers", title: "Most credited writers", rows: top(data.writers), people: true, unit: "songs" },
    { id: "versions", title: "Versions besides the main one",
      rows: Object.entries(data.versions).map(([type, n]) => ({ label: VERSION_TYPES[type] || type, value: n })), unit: "versions" },
  ];
  $("stats").innerHTML = `${statTiles()}<div class="stats-grid">${stats.charts.map(renderChart).join("")}</div>`;
  state.statsLoaded = true;
}

$("stats").addEventListener("click", (event) => {
  const legend = event.target.closest("[data-series]");
  const type = event.target.closest("[data-type]");
  const solo = event.target.closest("[data-solo]");
  if (legend) {
    const chart = stats.charts.find((c) => c.id === legend.dataset.chart);
    const view = chartView(chart);
    const key = legend.dataset.series;
    if (view.hidden.has(key)) view.hidden.delete(key);
    else if (chart.series.length - view.hidden.size > 1) view.hidden.add(key);  // one status always stays
    stats.solo = null;
    rerenderChart(chart.id);
  } else if (type) {
    chartView(stats.charts.find((c) => c.id === type.dataset.chart)).type = type.dataset.type;
    rerenderChart(type.dataset.chart);
  } else if (solo) {
    stats.solo = stats.solo === solo.dataset.solo ? null : solo.dataset.solo;
    for (const chart of stats.charts.filter((c) => c.series)) {
      const view = chartView(chart);
      view.hidden = new Set(stats.solo && chart.series.some((s) => s.key === stats.solo)
        ? chart.series.filter((s) => s.key !== stats.solo).map((s) => s.key) : []);
    }
    $("stats").innerHTML = `${statTiles()}<div class="stats-grid">${stats.charts.map(renderChart).join("")}</div>`;
  }
});
// Line charts: the crosshair snaps to the nearest year; the tooltip lists every shown series.
$("stats").addEventListener("mousemove", (event) => {
  const svg = event.target.closest(".line-svg");
  if (!svg) return;
  const chart = stats.charts.find((c) => c.id === svg.dataset.chart);
  const box = svg.getBoundingClientRect();
  const px = ((event.clientX - box.left) / box.width) * LINE.w;
  const n = chart.rows.length;
  const i = Math.max(0, Math.min(n - 1, Math.round(((px - LINE.l) / (LINE.w - LINE.l - LINE.r)) * (n - 1))));
  const row = chart.rows[i];
  const xPos = LINE.l + (n < 2 ? 0 : (i * (LINE.w - LINE.l - LINE.r)) / (n - 1));
  const cross = svg.querySelector(".crosshair");
  cross.setAttribute("x1", xPos); cross.setAttribute("x2", xPos); cross.setAttribute("visibility", "visible");
  svg.querySelector(".hit").dataset.tip = [row.label, ...shownSeries(chart).map((s) => `${s.label}: ${(row.values[s.key] || 0).toLocaleString()}`)].join("|");
});
$("stats").addEventListener("mouseleave", () => {
  for (const cross of document.querySelectorAll("#stats .crosshair")) cross.setAttribute("visibility", "hidden");
}, true);

/* ---------- CSV export of the songs shown ---------- */

function exportCsv() {
  const songs = filteredSongs().filter((s) => state.statuses.has(s.status)).sort(SORTS[$("sort").value] || SORTS.date);
  const cell = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const header = ["id", "title", "artist", "guest", "era", "status", "first date", "versions", "version types",
                  "releases", "producers", "also known as", "local file"];
  const rows = songs.map((s) => [s.id, s.title, s.artist, s.guest ? "yes" : "", (eraOf(s) || {}).name || "", s.status,
    s.date || "", s.versions, s.types.join("; "), s.releases, s.producers.join("; "), s.aliases.join("; "), s.file ? "yes" : ""]);
  const csv = "﻿" + [header, ...rows].map((r) => r.map(cell).join(",")).join("\r\n");
  const link = document.createElement("a");
  link.href = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  link.download = `lil-wayne-tracker-songs-${new Date().toISOString().slice(0, 10)}.csv`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}
$("export-csv").addEventListener("click", exportCsv);

/* ---------- review ---------- */

function candidateCard(c, button) {
  const tracks = c.tracks.map((t) => `${esc(t.release)} (${esc(t.date || "?")}${t.discography === "unofficial" ? ", bootleg" : ""})`
    + `${t.duration_ms ? ` ${duration(t.duration_ms)}` : ""}${t.type && t.type !== "album" ? ` [${esc(t.type)}]` : ""}`).join("; ");
  return `<div class="candidate">
    <div><a href="#/song/${c.id}"><strong>${esc(c.title)}</strong></a> <span class="muted">#${c.id} · ${esc(c.artist)}</span> ${badge(c.status)}</div>
    ${c.artists.length ? `<div class="track">Credited: ${esc(c.artists.join(", "))}</div>` : ""}
    <div class="track">${tracks || "no release"}</div>
    ${button}</div>`;
}

function reviewCard(item) {
  const key = esc(item.key);
  if (item.kind === "guest") {
    const facts = [
      item.lengths.length ? `MusicBrainz length ${item.lengths.map(duration).join(", ")}` : "",
      item.genius_albums.length ? `Genius album: ${esc(item.genius_albums.join(", "))}` : "",
      item.hq_projects.length ? `HQ project: ${esc(item.hq_projects.join(", "))}` : "",
      `sources: ${esc(item.sources)}`].filter(Boolean).join(" · ");
    return `<div class="review-card" data-key="${key}">
      <div class="review-q">Is <strong>${esc(item.title)}</strong> by <strong>${esc(item.main_artist)}</strong> one of these songs?</div>
      <div class="track">${facts}</div>
      ${serviceLinks(item.links)}
      ${item.candidates.map((c) => candidateCard(c,
        `<button class="act" data-action="guest_same" data-recording="${c.id}">Same song</button>`)).join("")}
      <div class="actions"><button class="act" data-action="guest_different">Different song — add as guest appearance</button>
        <button class="act ghost" data-action="skip">Skip</button></div></div>`;
  }
  if (item.kind === "spread") {
    const tracks = item.tracks.map((t) => `<div class="track">${esc(t.release)} (${esc(t.date || "?")}${t.discography === "unofficial"
      ? ", bootleg" : ""}) ${duration(t.duration_ms)}${t.track_title && t.track_title !== item.title ? ` “${esc(t.track_title)}”` : ""}
      ${item.early_releases.includes(t.release_id) ? `<span class="tag">earlier bootleg</span>` : ""}</div>`).join("");
    return `<div class="review-card" data-key="${key}">
      <div class="review-q">Is <a href="#/song/${item.recording.id}"><strong>${esc(item.title)}</strong></a> on these releases
        one song? The bootleg copy is years older than the official release.</div>
      ${tracks}
      <div class="actions"><button class="act ghost" data-action="skip">Same song</button>
        <button class="act" data-action="split_off">Different songs — split the earlier bootleg off</button></div></div>`;
  }
  return `<div class="review-card" data-key="${key}">
    <div class="review-q"><strong>${esc(item.title)}</strong> — a ${esc(VERSION_TYPES[item.version_type] || item.version_type)}
      version (“${esc(item.version_name)}”). Which song is it a version of?</div>
    ${item.recording ? candidateCard(item.recording, "") : ""}
    ${item.candidates.map((c) => candidateCard(c,
      `<button class="act" data-action="make_version" data-of="${c.id}">Version of this song</button>`)).join("")}
    <div class="actions"><button class="act" data-action="keep_separate">None — keep it as its own song</button>
      <button class="act ghost" data-action="skip">Skip</button></div></div>`;
}

function renderReview() {
  const kind = $("review-kind").value;
  const items = (state.review || []).filter((i) => !kind || i.kind === kind);
  if ($("review-count")) $("review-count").textContent = state.review ? `(${state.review.length})` : "";
  $("review-list").innerHTML = items.map(reviewCard).join("") || `<p class="muted">Nothing to review.</p>`;
}

async function loadReview() {
  state.review = await getJSON("/api/v1/review");
  renderReview();
}

$("review-kind").addEventListener("change", renderReview);
$("review-list").addEventListener("click", async (event) => {
  const button = event.target.closest("button.act");
  if (!button) return;
  const card = button.closest(".review-card");
  const decision = { key: card.dataset.key, action: button.dataset.action };
  if (button.dataset.recording) decision.recording = Number(button.dataset.recording);
  if (button.dataset.of) decision.of = Number(button.dataset.of);
  card.querySelectorAll("button").forEach((b) => { b.disabled = true; });
  const response = await fetch("/api/v1/review", { method: "POST", headers: { "Content-Type": "application/json" },
                                                body: JSON.stringify(decision) });
  if (response.ok) {
    state.review = state.review.filter((i) => i.key !== decision.key);
    card.outerHTML = `<p class="muted saved">Saved: ${esc(decision.action.replace(/_/g, " "))} — ${esc(decision.key.split(":")[1])}</p>`;
    if ($("review-count")) $("review-count").textContent = `(${state.review.length})`;
  } else {
    const error = await response.json().catch(() => ({}));
    card.querySelectorAll("button").forEach((b) => { b.disabled = false; });
    alert(`Not saved: ${error.error || response.status}`);
  }
});

/* ---------- routing (#/songs, #/song/12, #/release/8, #/artist/45 ...) ---------- */

const SECTION_NAMES = { about: "About Lil Wayne", songs: "Songs", eras: "Eras", timeline: "Timeline", releases: "Releases",
                        people: "People", stats: "Statistics", review: "Needs review" };

function setDrawer(open) {
  $("drawer").hidden = !open;
  $("drawer-backdrop").hidden = !open;
  $("menu-open").setAttribute("aria-expanded", String(open));
}

function showTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $("section-name").textContent = SECTION_NAMES[name] || "";
  setDrawer(false);
  for (const view of Object.keys(SECTION_NAMES)) $(`${view}-view`).hidden = view !== name;
  state.tab = name;
  if (name === "stats" && !state.statsLoaded) renderStats().catch((e) => { $("stats").textContent = e.message; });
  if (name === "about" && !state.aboutLoaded) renderAbout().catch((e) => { $("about").textContent = e.message; });
  if (name === "timeline" && !state.timeline) renderTimeline().catch((e) => { $("timeline").textContent = e.message; });
  if (name === "review" && !state.review) loadReview().catch((e) => { $("review-list").textContent = e.message; });
}

async function route() {
  const [, kind, id] = (location.hash.match(/^#\/(\w+)(?:\/(\d+))?/) || []);
  if (kind === "review") { location.hash = "#/songs"; return; }  // the "Needs review" page was removed (owner, Nov 15, 2026)
  if (kind in SECTION_NAMES) showTab(kind);
  if (kind === "eras") { $("era-grid").hidden = false; $("era-view-bar").hidden = false; $("era-detail").hidden = true; }
  try {
    if (kind === "era" && id) { showTab("eras"); $("panel").hidden = true; await openEra(id); }
    else if (kind === "song" && id) await openSong(id);
    else if (kind === "release" && id) await openRelease(id);
    else if (kind === "artist" && id) await openArtist(id);
    else $("panel").hidden = true;
  } catch (error) {
    $("panel-body").innerHTML = `<p class="muted">Not found (${esc(error.message)}).</p>`;
    showPanel();
  }
}

// Details (song, release, person) open in a centred window over the page.
function showPanel() {
  $("panel").hidden = false;
  $("panel").querySelector(".panel").scrollTop = 0;
}

function closePanel() {
  location.hash = `#/${state.tab || "songs"}`;
}
$("panel").addEventListener("click", (event) => { if (event.target === $("panel")) closePanel(); });

document.addEventListener("click", (event) => {
  if (event.target.closest("a[href^='#/']")) return; // normal links change the hash
  const release = event.target.closest("[data-release]");
  const row = event.target.closest("tr[data-id]");
  if (release) { event.preventDefault(); location.hash = `#/release/${release.dataset.release}`; }
  else if (row) location.hash = `#/song/${row.dataset.id}`;
});
$("close").addEventListener("click", closePanel);
document.addEventListener("keydown", (event) => { if (event.key === "Escape" && !$("panel").hidden) closePanel(); });
$("menu-open").addEventListener("click", () => setDrawer(true));
$("menu-close").addEventListener("click", () => setDrawer(false));
$("drawer-backdrop").addEventListener("click", () => setDrawer(false));
document.addEventListener("keydown", (event) => { if (event.key === "Escape") setDrawer(false); });
/* ---------- notifications: on this day, random song, open questions ---------- */

async function loadToday() {
  const now = new Date();
  const monthDay = `${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  const items = await getJSON(`/api/v1/on-this-day/${monthDay}`);
  state.today = items;
  state.todayKey = `${now.getFullYear()}-${monthDay}`;
  let read = false;
  try { read = localStorage.getItem(NOTIFY_READ_KEY) === state.todayKey; } catch (e) { /* storage unavailable */ }
  $("notify-count").hidden = !items.length || read;
  $("notify-count").textContent = items.length;
  const years = (date) => now.getFullYear() - Number(date.slice(0, 4));
  $("notify-body").innerHTML = (items.length ? `<p class="muted">On this day</p>` + items.map((r) =>
    `<a class="mini-card" href="#/release/${r.id}">${r.cover_file ? `<img class="cover small" src="covers/${esc(r.cover_file)}" alt="">`
      : `<img class="cover small" src="covers/artist.jpg" alt="" loading="lazy">`}
      <span><span class="title">${esc(r.title)}</span><br><span class="muted">${years(r.release_date)} years ago ·
      ${esc(fmtDate(r.release_date))}${r.discography === "unofficial" ? " · bootleg" : ""}</span></span></a>`).join("")
    : `<p class="muted">Nothing was released on ${esc(now.toLocaleDateString("en-US", { month: "long", day: "numeric" }))}.</p>`)
;
}

// Opening the popover marks today's notifications as read (the badge stays off until the next day).
function setNotify(open) {
  $("notify-panel").hidden = !open;
  $("notifications").setAttribute("aria-expanded", String(open));
  if (open) {
    $("notify-count").hidden = true;
    try { if (state.todayKey) localStorage.setItem(NOTIFY_READ_KEY, state.todayKey); } catch (e) { /* storage unavailable */ }
  }
}
$("notifications").addEventListener("click", (event) => { event.stopPropagation(); setNotify($("notify-panel").hidden); });
document.addEventListener("click", (event) => {
  if (!$("notify-panel").hidden && !$("notify-panel").contains(event.target)) setNotify(false);
});
$("random-song").addEventListener("click", () => {
  const pool = state.songs.filter((s) => !s.guest);
  location.hash = `#/song/${pool[Math.floor(Math.random() * pool.length)].id}`;
  setNotify(false);
});

/* ---------- settings (this browser only) ---------- */

const SETTINGS_KEY = "lwt-settings";
function loadSettings() {
  try { return JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {}; } catch (e) { return {}; }
}
const ACCENTS = ["purple", "gold", "red", "blue", "green", "cover"];
function applySettings(settings) {
  if (settings.theme) document.documentElement.dataset.theme = settings.theme;
  else delete document.documentElement.dataset.theme;
  document.documentElement.dataset.accent = ACCENTS.includes(settings.accent) ? settings.accent : "purple";
  if (window.updateCoverAccent) window.updateCoverAccent();  // player.js: colours of the playing cover
  state.dateStyle = settings.dates || "iso";
}
function fmtDate(date) {
  if (!date || state.dateStyle !== "long" || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return date || "";
  return new Date(`${date}T12:00:00`).toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" });
}
$("settings").addEventListener("click", () => {
  const s = loadSettings();
  $("set-theme").value = s.theme || "";
  $("set-view").value = s.view || state.view;
  $("set-kind").value = s.kind || "own";
  $("set-dates").value = s.dates || "iso";
  $("set-player").value = s.player || "spotify";
  $("set-accent").value = ACCENTS.includes(s.accent) ? s.accent : "purple";
  refreshChips($("settings-dialog"));
  showPane("appearance");
  $("settings-dialog").showModal();
});
function showPane(name) {
  for (const button of document.querySelectorAll(".settings-nav [data-pane]")) button.classList.toggle("on", button.dataset.pane === name);
  for (const pane of document.querySelectorAll(".settings-body .pane")) pane.hidden = pane.dataset.pane !== name;
}
document.querySelector(".settings-nav").addEventListener("click", (event) => {
  const button = event.target.closest("[data-pane]");
  if (button) showPane(button.dataset.pane);
});
// Theme and colours change at once (a preview); everything is saved on "Done".
for (const id of ["set-theme", "set-accent"]) {
  $(id).addEventListener("change", () => applySettings({ ...loadSettings(), theme: $("set-theme").value, accent: $("set-accent").value }));
}
$("settings-dialog").addEventListener("close", () => {
  const s = { theme: $("set-theme").value, view: $("set-view").value, kind: $("set-kind").value, dates: $("set-dates").value,
              player: $("set-player").value, accent: $("set-accent").value };
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)); } catch (e) { /* storage unavailable */ }
  applySettings(s);
  setView(s.view);
  $("kind").value = s.kind;
  refreshChips($("filter-menu"));
  resetAndRender();
  if (!$("timeline-view").hidden) renderTimeline();
});
applySettings(loadSettings());

/* ---------- timeline ---------- */

// The eras (start - end) a year belongs to, and the eras that begin / end in it.
function yearEras(year) {
  const inYear = (date) => (date || "").slice(0, 4) === year;
  const active = state.eras.filter((e) => (e.start_date || "0000") <= `${year}-12-31` && (e.end_date || "9999") >= `${year}-01-01`);
  const marks = [
    ...state.eras.filter((e) => inYear(e.start_date)).map((e) => ({ era: e, date: e.start_date, kind: "begins" })),
    ...state.eras.filter((e) => inYear(e.end_date)).map((e) => ({ era: e, date: e.end_date, kind: "ends" })),
  ].sort((a, b) => a.date.localeCompare(b.date) || (a.kind === "ends" ? -1 : 1));
  return { active, marks };
}

/* ---------- timeline: horizontal (eras as bands, covers on lanes, leaks per year) or a list ---------- */
const TIMELINE_VIEWS = { horizontal: "↔ Horizontal", list: "☰ List" };
const TL_LANES = [
  { key: "albums", label: "Albums", types: ["studio_album", "collaboration_album", "group_album"], size: 84, min: 56, max: 150, caption: true },
  { key: "mixtapes", label: "Mixtapes", types: ["mixtape"], size: 60, min: 40, max: 110, caption: false },
  { key: "singles", label: "Singles & EPs", types: ["single", "group_single", "ep"], size: 42, min: 30, max: 80, caption: false },
];
const TL_PAD = 8, TL_GAP = 6, TL_SCALES = [110, 160, 240, 340, 480, 680, 900];

// Cover size of a lane at a zoom (pixels per year): grows with the zoom, never wider than a year column.
function tlCoverSize(lane, scale) {
  return Math.max(Math.min(lane.min, scale - 2 * TL_PAD), Math.min(lane.max, scale - 2 * TL_PAD, Math.round(lane.size * Math.sqrt(scale / 240))));
}

// "2008-06-10" -> 2008.44; a year / month only sits in its middle (marked as approximate)
function fractionalYear(date) {
  const [y, m, d] = String(date || "").split("-").map(Number);
  if (!y) return null;
  if (!m) return y + 0.5;
  if (!d) return y + (m - 0.5) / 12;
  return y + (m - 1) / 12 + (d - 1) / 365;
}

async function renderTimeline() {
  state.timeline = state.timeline || await getJSON("/api/v1/timeline");
  try { state.timelineView = TIMELINE_VIEWS[state.timelineView] ? state.timelineView : localStorage.getItem("lwt-timeline-view") || "horizontal"; }
  catch (e) { state.timelineView = state.timelineView || "horizontal"; }
  if (!TIMELINE_VIEWS[state.timelineView]) state.timelineView = "horizontal";
  const bar = `<div class="view-bar"><span class="muted">${state.timelineView === "horizontal"
      ? "Scroll or drag sideways · click a cover or an era" : `${plural(state.timeline.length, "year")}`}</span>
    <div class="htl-tools">${state.timelineView === "horizontal" ? `<button type="button" class="menu-button plain" data-zoom="-1" title="Zoom out">−</button>
      <button type="button" class="menu-button plain" data-zoom="1" title="Zoom in">+</button>` : ""}
      <details class="menu" id="timeline-view-menu"><summary class="menu-button">${TIMELINE_VIEWS[state.timelineView]}</summary>
        <div class="menu-panel narrow">${Object.entries(TIMELINE_VIEWS).map(([key, label]) =>
          `<button data-timeline-view="${key}" class="${key === state.timelineView ? "on" : ""}">${label}</button>`).join("")}</div></details></div></div>`;
  $("timeline").innerHTML = bar + (state.timelineView === "horizontal" ? timelineHorizontal(state.timeline) : timelineList());
  if (state.timelineView === "horizontal") wireTimelineScroll();
}

function timelineHorizontal(data) {
  if (!state.timelineScale) {
    try { state.timelineScale = TL_SCALES.includes(Number(localStorage.getItem("lwt-timeline-scale"))) ? Number(localStorage.getItem("lwt-timeline-scale")) : 240; }
    catch (e) { state.timelineScale = 240; }
  }
  const scale = state.timelineScale;  // pixels per year
  const first = Math.min(...data.map((y) => Number(y.year)));
  const last = Math.max(new Date().getFullYear(), ...data.map((y) => Number(y.year)));
  const left = 24, x = (year) => left + (year - first) * scale;
  const width = x(last + 1) + 24;
  const ERA_HEAD = 46, YEAR_HEAD = 26, LANE_HEAD = 26, LANE_GAP = 14, CHART = 110;
  // Each year is a column; a lane's covers of that year fill a grid inside the column in date
  // order, so a cover never crosses a year line or another cover at any zoom.
  let top = ERA_HEAD + YEAR_HEAD;
  const labels = [];  // lane names stay at the left edge while the canvas scrolls
  const laneHtml = TL_LANES.map((lane) => {
    const size = tlCoverSize(lane, scale);
    const inner = scale - 2 * TL_PAD;
    const perRow = Math.max(1, Math.floor((inner + TL_GAP) / (size + TL_GAP)));
    const gridWidth = perRow * size + (perRow - 1) * TL_GAP;
    const rowHeight = size + (lane.caption ? 40 : TL_GAP);
    const laneTop = top;
    let rows = 1;
    const cells = data.map((y) => {
      const items = y.releases.filter((r) => lane.types.includes(r.type) && fractionalYear(r.date))
        .sort((a, b) => fractionalYear(a.date) - fractionalYear(b.date) || a.title.localeCompare(b.title));
      rows = Math.max(rows, Math.ceil(items.length / perRow));
      const x0 = x(Number(y.year)) + TL_PAD + (inner - gridWidth) / 2;
      return items.map((r, i) => {
        const approx = !/^\d{4}-\d{2}-\d{2}$/.test(r.date);
        const type = (RELEASE_TYPES[r.type] || r.type).replace(/s$/, "");
        const cx = x0 + (i % perRow) * (size + TL_GAP), cy = laneTop + LANE_HEAD + Math.floor(i / perRow) * rowHeight;
        return `<a class="htl-item${approx ? " approx" : ""}" href="#/release/${r.id}" style="left:${cx}px;top:${cy}px;width:${size}px"
          data-tip="${esc(`${r.title}|${type} · ${fmtDate(r.date)}${approx ? " (date not exact)" : ""}${r.editions > 1 ? `|${r.editions} editions` : ""}`)}">
          <img src="covers/${esc(r.cover_file || "artist.jpg")}" alt="" loading="lazy" style="width:${size}px;height:${size}px">
          ${lane.caption ? `<span class="htl-caption" style="width:${size + TL_GAP}px">${esc(r.title)}</span>` : ""}</a>`;
      }).join("");
    }).join("");
    top += LANE_HEAD + rows * rowHeight + LANE_GAP;
    labels.push(`<div class="htl-lane-label" style="top:${laneTop + 3}px">${esc(lane.label)}</div>`);
    return `<div class="htl-lane-line" style="top:${laneTop}px;width:${width}px"></div>${cells}`;
  }).join("");
  const chartTop = top + 8, height = chartTop + CHART + 34;
  // eras as bands over the whole height
  const eras = state.eras.map((era, i) => {
    const from = Math.max(first, fractionalYear(era.start_date) ?? first), to = Math.min(last + 1, fractionalYear(era.end_date) ?? last + 1);
    if (to <= first || from >= last + 1) return "";
    return `<a class="htl-era ${i % 2 ? "odd" : "even"}" href="#/era/${era.id}" style="left:${x(from)}px;width:${Math.max(2, x(to) - x(from))}px;height:${height}px"
      data-tip="${esc(`${eraName(era)}|${fmtDate(era.start_date) || "…"} – ${era.end_date ? fmtDate(era.end_date) : "today"}`)}">
      <span class="htl-era-label">${coverImg(era, "tiny")}<span>${esc(eraName(era))}</span></span></a>`;
  }).join("");
  const years = [];
  for (let y = first; y <= last + 1; y++) {
    const count = (data.find((d) => Number(d.year) === y) || { releases: [] }).releases.length;
    years.push(`<div class="htl-year" style="left:${x(y)}px;height:${height - ERA_HEAD}px;top:${ERA_HEAD}px"><span>${y <= last ? y : ""}${
      y <= last && count ? ` <small>${count}</small>` : ""}</span></div>`);
  }
  // leaks per year: bootlegs released + unreleased versions first known that year (one series: the title names it)
  const leaks = data.map((y) => ({ year: Number(y.year), bootlegs: y.bootlegs || 0, known: y.first_known || 0 }));
  const max = Math.max(1, ...leaks.map((l) => l.bootlegs + l.known));
  const barWidth = Math.min(28, scale * 0.35);
  const bars = leaks.filter((l) => l.bootlegs + l.known).map((l) => {
    const total = l.bootlegs + l.known, h = Math.max(2, (total / max) * (CHART - 22));
    return `<div class="htl-bar" style="left:${x(l.year + 0.5) - barWidth / 2}px;top:${chartTop + CHART - h}px;width:${barWidth}px;height:${h}px"
      data-tip="${esc(`${l.year}|${plural(l.bootlegs, "bootleg")}|${plural(l.known, "unreleased version")} first known`)}"></div>
      ${h > 14 || total >= max * 0.5 ? `<span class="htl-bar-value" style="left:${x(l.year + 0.5)}px;top:${chartTop + CHART - h - 18}px">${total}</span>` : ""}`;
  }).join("");
  const now = new Date(), today = now.getFullYear() + now.getMonth() / 12;
  labels.push(`<div class="htl-lane-label" style="top:${chartTop - 4}px">Leaks per year: bootlegs and unreleased versions first known</div>`);
  return `<div class="htl-frame"><div class="htl-labels">${labels.join("")}</div>
    <div class="htl-scroll" id="htl-scroll"><div class="htl-canvas" style="width:${width}px;height:${height}px">
    ${eras}${years.join("")}${laneHtml}
    <div class="htl-axis" style="top:${chartTop + CHART}px;width:${width}px"></div>${bars}
    <div class="htl-today" style="left:${x(today)}px;height:${height - ERA_HEAD}px;top:${ERA_HEAD}px" title="Today"></div>
  </div></div></div>`;
}

// Drag or wheel to scroll sideways; zoom keeps the middle of the view in place.
// One zoom step; the middle of the view stays in place (state.timelineCenter).
function zoomTimeline(step) {
  const now = state.timelineScale || 240;
  const index = TL_SCALES.reduce((best, s, i) => Math.abs(s - now) < Math.abs(TL_SCALES[best] - now) ? i : best, 0);
  const next = TL_SCALES[Math.max(0, Math.min(TL_SCALES.length - 1, index + step))];
  if (next === now) return;
  state.timelineScale = next;
  try { localStorage.setItem("lwt-timeline-scale", String(next)); } catch (e) { /* storage unavailable */ }
  renderTimeline();
}

function wireTimelineScroll() {
  const box = $("htl-scroll");
  if (!box) return;
  if (state.timelineCenter != null) box.scrollLeft = state.timelineCenter * box.scrollWidth - box.clientWidth / 2;
  let drag = null;
  box.addEventListener("pointerdown", (event) => { drag = { x: event.clientX, left: box.scrollLeft, moved: false }; });
  box.addEventListener("pointermove", (event) => {
    if (!drag) return;
    if (Math.abs(event.clientX - drag.x) > 5) drag.moved = true;
    if (drag.moved) box.scrollLeft = drag.left - (event.clientX - drag.x);
  });
  const stop = () => { setTimeout(() => { drag = null; }, 0); };
  box.addEventListener("pointerup", stop);
  box.addEventListener("pointerleave", () => { drag = null; });
  box.addEventListener("click", (event) => { if (drag && drag.moved) { event.preventDefault(); event.stopPropagation(); } }, true);
  box.addEventListener("wheel", (event) => {
    if (event.ctrlKey) {  // ctrl + wheel or a trackpad pinch zooms
      event.preventDefault();
      if (!box.dataset.zooming) { box.dataset.zooming = "1"; zoomTimeline(event.deltaY < 0 ? 1 : -1); }
      return;
    }
    if (Math.abs(event.deltaY) > Math.abs(event.deltaX) && !event.shiftKey) {
      box.scrollLeft += event.deltaY;
      event.preventDefault();
    }
  }, { passive: false });
  box.addEventListener("scroll", () => { state.timelineCenter = (box.scrollLeft + box.clientWidth / 2) / box.scrollWidth; }, { passive: true });
}
$("timeline").addEventListener("click", (event) => {
  const zoom = event.target.closest("[data-zoom]");
  const view = event.target.closest("[data-timeline-view]");
  if (zoom) {
    zoomTimeline(Number(zoom.dataset.zoom));
  } else if (view) {
    state.timelineView = view.dataset.timelineView;
    try { localStorage.setItem("lwt-timeline-view", state.timelineView); } catch (e) { /* storage unavailable */ }
    renderTimeline();
  }
});

function timelineList() {
  return `<div class="timeline">${state.timeline.map((y) => {
    const { active, marks } = yearEras(y.year);
    return `
    <section class="year"><div class="year-label">${esc(y.year)}
      ${active.map((e) => `<a class="year-era" href="#/era/${e.id}">${coverImg(e, "tiny")}${esc(eraName(e))}</a>`).join("")}</div>
      <div class="year-body">
      ${marks.map((m) => `<a class="era-mark ${m.kind}" href="#/era/${m.era.id}">${m.kind === "begins" ? "▶" : "■"}
        ${esc(eraName(m.era))} ${m.kind} · ${esc(fmtDate(m.date))}</a>`).join("")}
      <div class="mini-grid">${y.releases.map((r) => `<a class="mini-card" href="#/release/${r.id}">
        ${r.cover_file ? `<img class="cover small" src="covers/${esc(r.cover_file)}" alt="" loading="lazy">`
          : `<img class="cover small" src="covers/artist.jpg" alt="" loading="lazy">`}
        <span><span class="title">${esc(r.title)}</span><br><span class="muted">${esc((RELEASE_TYPES[r.type] || r.type).replace(/s$/, ""))}
          · ${esc(fmtDate(r.date))}</span>${r.editions > 1 ? ` <span class="editions-badge">${r.editions} editions</span>` : ""}</span></a>`).join("")}</div>
      ${y.bootlegs ? `<details class="table-view"><summary>${plural(y.bootlegs, "bootleg")} (leaks)</summary><div class="mini-grid">${
        y.bootleg_list.map((b) => `<a class="mini-card" href="#/release/${b.id}">${b.cover_file
          ? `<img class="cover small" src="covers/${esc(b.cover_file)}" alt="" loading="lazy">` : ""}<span>${esc(b.title)}</span></a>`).join("")}</div></details>` : ""}
      ${y.first_known ? `<p class="muted">${plural(y.first_known, "unreleased version")} first known this year</p>` : ""}
    </div></section>`;
  }).join("")}</div>`;
}
window.addEventListener("scroll", () => { $("to-top").hidden = window.scrollY < 500; }, { passive: true });
$("to-top").addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));
window.addEventListener("hashchange", route);
init().then(route).catch((error) => { $("totals").textContent = `Could not load the catalogue: ${error.message}`; });

/* ---------- covers full screen (click a big cover; click, × or Esc closes) ---------- */
const ZOOMABLE = ".panel-head img.cover.big, img.cover.hero, #pf-cover";
function setLightbox(src, alt = "") {
  $("lightbox").hidden = !src;
  if (src) { $("lightbox-img").src = src; $("lightbox-img").alt = alt; $("lightbox-close").focus(); }
}

// Our covers are small (250 px).  For a MusicBrainz release the Cover Art Archive has
// a 1200 px front: release first, then its release group; the local copy stays otherwise.
async function largeCover(src) {
  const match = src.match(/covers\/(release|era)_(\d+)\.jpg/);
  if (!match) return [];
  const data = await getJSON(`/api/v1/${match[1]}s/${match[2]}`).catch(() => null);
  if (!data) return [];
  const urls = [];
  if (data.musicbrainz_release_id) urls.push(`https://coverartarchive.org/release/${data.musicbrainz_release_id}/front-1200`);
  if (data.musicbrainz_release_group_id) urls.push(`https://coverartarchive.org/release-group/${data.musicbrainz_release_group_id}/front-1200`);
  return urls;
}
async function zoomCover(src, alt) {
  setLightbox(src, alt);
  for (const url of await largeCover(src)) {
    const ok = await new Promise((resolve) => {
      const probe = new Image();
      probe.onload = () => resolve(true);
      probe.onerror = () => resolve(false);
      probe.src = url;
    });
    if (ok) {
      if (!$("lightbox").hidden && $("lightbox-img").src === new URL(src, location.href).href) $("lightbox-img").src = url;
      return;
    }
  }
}
document.addEventListener("click", (event) => {
  const image = event.target.closest(ZOOMABLE);
  if (image && image.getAttribute("src")) {
    event.stopPropagation();
    const title = image.closest(".panel-head, .era-hero, .pf-box")?.querySelector("h2")?.textContent || "";
    zoomCover(image.src, title ? `Cover: ${title}` : "Cover");
  }
}, true);
$("lightbox").addEventListener("click", () => setLightbox(null));
document.addEventListener("keydown", (event) => {  // before the other Esc handlers (window / player)
  if (event.key === "Escape" && !$("lightbox").hidden) { event.stopImmediatePropagation(); setLightbox(null); }
}, true);

/* ---------- icons in the menu and the settings sections ---------- */
const NAV_ICONS = {
  songs: "M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z",
  eras: "M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6a7 7 0 1 1 2.05 4.95l-1.42 1.42A9 9 0 1 0 13 3zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z",
  timeline: "M3 5h2v14H3zm4 2h14v2H7zm0 4h10v2H7zm0 4h14v2H7z",
  releases: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 14.5a4.5 4.5 0 1 1 0-9 4.5 4.5 0 0 1 0 9zm0-5.5a1 1 0 1 0 0 2 1 1 0 0 0 0-2z",
  people: "M16 11a3 3 0 1 0-3-3 3 3 0 0 0 3 3zm-8 0a3 3 0 1 0-3-3 3 3 0 0 0 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5C15 14.17 10.33 13 8 13zm8 0c-.29 0-.62.02-.97.05A4.22 4.22 0 0 1 17 16.5V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z",
  stats: "M5 9.2h3V19H5zM10.6 5h2.8v14h-2.8zm5.6 8H19v6h-2.8z",
  review: "M14.4 6 14 4H5v17h2v-7h5.6l.4 2h7V6z",
  about: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-6h2zm0-8h-2V7h2z",
  appearance: "M12 3a9 9 0 0 0 0 18c.83 0 1.5-.67 1.5-1.5 0-.39-.15-.74-.39-1.01-.23-.26-.38-.61-.38-.99 0-.83.67-1.5 1.5-1.5H16a5 5 0 0 0 5-5c0-4.42-4.03-8-9-8zm-5.5 9a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm3-4a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm5 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm3 4a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3z",
  player: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-2 14.5v-9l6 4.5-6 4.5z",
};
for (const el of document.querySelectorAll(".drawer .tab[data-tab], .settings-nav [data-pane]")) {
  const path = NAV_ICONS[el.dataset.tab || el.dataset.pane];
  if (path) el.insertAdjacentHTML("afterbegin", `<svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg>`);
}
