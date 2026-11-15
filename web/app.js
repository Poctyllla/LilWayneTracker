"use strict";

const STATUS = { released: "Released", leaked: "Leaked", unreleased: "Unreleased" };
const VERSION_TYPES = {
  album: "Main", remix: "Remix", alternate: "Alternate", original: "Early version", extended: "Extended",
  demo: "Demo", snippet: "Snippet", live: "Live", acapella: "Acapella",
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
  compilation: "Compilations",
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

/* ---------- songs ---------- */

function filteredSongs() {
  const query = fold($("search").value.trim());
  const eras = chosen("era"), kind = $("kind").value, vtypes = chosen("vtype");
  const wanted = vtypes.has("wanted"), types = [...vtypes].filter((t) => t !== "wanted");
  return state.songs.filter((song) => {
    if (kind === "own" && song.guest) return false;
    if (kind === "guest" && !song.guest) return false;
    if (eras.size && !eras.has(String(song.era ?? "none"))) return false;
    if (vtypes.size && !((wanted && song.wanted) || types.some((t) => song.types.includes(t)))) return false;
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
      <td><div class="title">${favButton(song.id)}${esc(song.title)}</div>${songMeta(song)}${playButton(song.play, `data-play-song="${song.key || song.id}"`)}</td>
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
      <div class="card-body"><div class="title">${favButton(song.id)}${esc(song.title)}</div>
        ${songArtist(song) ? `<div class="artist">${esc(songArtist(song))}</div>` : ""}
        <div class="card-foot">${badge(song.status)}<span class="date">${esc((song.date || "").slice(0, 4))}</span>
          ${song.file ? `<span class="file">♪</span>` : ""}</div>
        <div class="card-album" title="Album / project">${song.album ? esc(song.album.title) : "No release"}</div>
        <div class="artist">${esc(era ? eraName(era) : "No era")}</div>
        ${playButton(song.play, `data-play-song="${song.key || song.id}"`)}</div></a>`;
  }).join("")}</div>`,
  compact: (songs) => `<table class="rows compact">${songs.map((song) => {
    const era = eraOf(song);
    return `<tr data-id="${song.id}"><td class="play-cell">${playButton(song.play, `data-play-song="${song.key || song.id}"`, "")}</td><td class="title">${favButton(song.id)}${esc(song.title)}</td>
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
  const saved = Object.fromEntries(FILTER_FIELDS.map((id) => [id, $(id).multiple ? [...chosen(id)] : $(id).value]));
  saved.statuses = [...state.statuses];
  try { localStorage.setItem(FILTERS_KEY, JSON.stringify(saved)); } catch (e) { /* storage unavailable */ }
}
function restoreFilters() {
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem(FILTERS_KEY)) || {}; } catch (e) { saved = {}; }
  for (const id of FILTER_FIELDS) {
    const field = $(id);
    if (field.multiple && (Array.isArray(saved[id]) || typeof saved[id] === "string")) {  // a single value from older visits too
      const offered = new Set([...field.options].map((o) => o.value));
      setChosen(id, [].concat(saved[id]).filter((v) => v && offered.has(v)));
      if (id === "discography" && !chosen(id).size) setChosen(id, ["official"]);
      continue;
    }
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

// With a version type chosen the list shows those versions themselves ("Hustler Musik (Live In Los
// Angeles)"): their own date, status and playable source; the link still opens the song.
function versionEntries(songs, types) {
  return songs.flatMap((song) => (song.variants || []).filter((v) => types.includes(v.type)).map((v) => ({
    ...song, key: `${song.id}-${v.id}`, title: `${song.title} (${v.name || VERSION_TYPES[v.type] || v.type})`,
    date: v.date || song.date, status: v.status || song.status, play: v.play, types: [v.type], variant: v,
    album: v.album, file: v.file,  // the release this version is on (none: known only from sources)
  })));
}

function renderSongs() {
  saveFilters();
  const vtypes = chosen("vtype"), wanted = vtypes.has("wanted"), types = [...vtypes].filter((t) => t !== "wanted");
  const filtered = filteredSongs();
  const base = types.length ? [...versionEntries(filtered, types), ...(wanted ? filtered.filter((s) => s.wanted) : [])] : filtered;
  for (const chip of document.querySelectorAll(".chip")) {
    chip.querySelector("span").textContent = base.filter((s) => s.status === chip.dataset.status).length;
  }
  const songs = base.filter((s) => state.statuses.has(s.status)).sort(SORTS[$("sort").value] || SORTS.date);
  const typeNames = types.map((t) => (VERSION_TYPES[t] || t).toLowerCase().replace(/ version$/, ""));
  $("shown").textContent = wanted && !types.length
    ? `Showing ${plural(songs.length, "wanted song")}: known from snippets only, no full version has leaked`
    : types.length ? `Showing ${songs.length} ${typeNames.join(" / ")} version${songs.length === 1 ? "" : "s"}${wanted ? " and wanted songs" : ""}`
      : `Showing ${plural(songs.length, "song")}`;
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
  const active = [chosen("era").size ? "x" : "", $("kind").value !== "own" ? "x" : "", chosen("vtype").size ? "x" : "",
                  $("sort").value !== "date" ? "x" : ""]
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
        ${qualityTag(version.quality)}
        ${playButton(version.play, `data-play-version="${version.id}"`, "Play")}
        ${(version.links || []).map((l) => `<a class="mini-listen" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer"
          title="${esc(l.url)}" style="--svc:${serviceOf(l.url).color}">▶ ${esc(serviceOf(l.url).name)}</a>`).join("")}</div>
      ${version.first_known_date ? `<div class="track">Known since ${esc(version.first_known_date)}</div>` : ""}
      ${appearsOn(version.tracks, song.title) || `<div class="track">Not on a release</div>`}
    </div>`).join("");
  const ga = song.genius_album;
  const appears = appearsOn(albumTracks, song.title)
    || (ga ? `<div class="genius-album"><a href="${esc(ga.url || ga.song_url)}" target="_blank" rel="noopener noreferrer"><strong>${esc(ga.name)}</strong></a>
        ${ga.artist ? `<span class="muted">· ${esc(ga.artist)}</span>` : ""} ${ga.release_date ? `<span class="muted">· ${esc(ga.release_date)}</span>` : ""}
        <div class="src">Album on Genius (not catalogued as a release here)</div></div>` : "")
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
        · ${listed.era ? `<a href="#/era/${listed.era}">${esc(eraName(song.era))}</a>` : esc(song.era ? eraName(song.era) : "no era")} ${badge(song.availability)}
        ${song.former ? `<span class="tag former" title="Known ${esc(fmtDate(song.former.known))}, released ${esc(fmtDate(song.former.released))}">Leaked before release</span>` : ""}</div>
      ${song.aliases.length ? `<p class="muted">Also known as: ${esc(song.aliases.join(", "))}</p>` : ""}
      <div class="head-play">${main ? playButton(main.play, `data-play-version="${main.id}"`) : ""}
        ${favButton(song.id, true)}<button type="button" class="act ghost" data-playlist-add="${song.id}">＋ Playlist</button>
        <button type="button" class="act ghost" data-share="#/song/${song.id}${main ? "/play" : ""}" title="Copy a link that opens (and plays) this song">↗ Share</button></div>
      ${song.listen.length ? `<div class="listen">${serviceLinks(song.listen.map((l) => l.url))}</div>` : ""}
      ${lyricsLink(song)}
    </div></div>
    <h3>Appears on</h3>${appears}
    ${others.length ? `<h3>Other versions</h3>${compareVersions(song)}${versions}` : ""}
    ${chartBlock(song)}
    ${(song.videos || []).length ? `<h3>Videos</h3><div class="grid video-grid small">${song.videos.map(videoCard).join("")}</div>` : ""}
    ${songHistory(song)}
    <h3>Credits</h3>${credits ? `<dl class="credits">${credits}</dl>` : `<p class="muted">No credits yet.</p>`}
    ${songConnections(song)}
    <h3>Sources</h3>${serviceLinks(urls) || `<p class="muted">No links.</p>`}${provenance(song)}`;
  showPanel();
}

// The quality of the owner's best file of a version (local tracker only).
const QUALITY = { lossless: "Lossless", high: "High quality", medium: "Medium quality", low: "Low quality" };
function qualityTag(quality) {
  return quality ? `<span class="tag quality q-${esc(quality)}" title="Quality of your file">${esc(QUALITY[quality] || quality)}</span>` : "";
}

// Song history: recorded, first known, leaked, each version's first appearance, releases.
const HISTORY_ICONS = { recorded: "●", first_known: "◆", leaked: "▲", version: "◇", bootleg: "▲", released: "★", title: "✎" };
function songHistory(song) {
  const events = song.history || [];
  if (!events.length) return "";
  return `<h3>History</h3><ol class="history">${events.map((e) => `<li class="h-${esc(e.kind)}">
    <span class="h-date">${esc(e.date ? fmtDate(e.date) : "undated")}</span>
    <span class="h-icon" aria-hidden="true">${HISTORY_ICONS[e.kind] || "•"}</span><span>${esc(e.text.charAt(0).toUpperCase() + e.text.slice(1))}</span></li>`).join("")}</ol>`;
}

// Samples, interpolations, covers and remixes (Genius song relationships).
function songConnections(song) {
  const groups = song.connections || [];
  if (!groups.length) return "";
  const item = (s) => s.song_id ? `<a href="#/song/${s.song_id}">${esc(s.title)}</a>${s.artist ? ` <span class="muted">${esc(s.artist)}</span>` : ""}`
    : `<a href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a>${s.artist ? ` <span class="muted">${esc(s.artist)}</span>` : ""}`;
  return `<h3>Samples &amp; connections</h3><dl class="credits connections">${groups.map((g) => {
    const first = g.songs.slice(0, 6), rest = g.songs.slice(6);
    return `<dt>${esc(g.label)}</dt><dd>${first.map(item).join(", ")}${rest.length
      ? ` <details class="more"><summary>${rest.length} more</summary>${rest.map(item).join(", ")}</details>` : ""}</dd>`;
  }).join("")}</dl><p class="src">From Genius</p>`;
}

// What each source confirms (recording_sources notes), folded.
function provenance(song) {
  const notes = new Map();
  for (const s of song.sources || []) {
    if (!s.notes) continue;
    const key = `${s.name}|${s.notes}`;
    if (!notes.has(key)) notes.set(key, { ...s, versions: new Set() });
    if (s.version) notes.get(key).versions.add(s.version);
  }
  if (!notes.size) return "";
  return `<details class="provenance"><summary>What the sources confirm (${notes.size})</summary><ul>${[...notes.values()].map((n) =>
    `<li><strong>${n.url ? `<a href="${esc(n.url)}" target="_blank" rel="noopener noreferrer">${esc(n.name)}</a>` : esc(n.name)}</strong>:
      ${esc(n.notes)}${n.versions.size ? ` <span class="muted">(${esc([...n.versions].join(", "))})</span>` : ""}</li>`).join("")}</ul></details>`;
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
    <div class="compare-scroll"><table><thead><tr><th>Version</th><th>Type</th><th>Status</th><th>First</th><th>Length</th><th>Releases</th>
    <th>Files</th></tr></thead><tbody>${rows}</tbody></table></div></details>`;
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
  const discos = chosen("discography").size ? chosen("discography") : new Set(["official"]);
  const types = chosen("release-type");
  const changed = (discos.size !== 1 || !discos.has("official")) + Boolean(types.size);
  $("release-filter-count").textContent = changed ? `(${changed})` : "";
  // a query matches any edition, the entry shows the album
  const groupsHit = new Set(state.releases.filter((r) => discos.has(r.discography) && (!types.size || types.has(r.release_type))
    && (!query || fold(`${r.title} ${r.artist_credit || ""}`).includes(query))).map((r) => r.group_id));
  const entries = releaseEntries(state.releases.filter((r) => discos.has(r.discography) && groupsHit.has(r.group_id)));
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
  setChosen("discography", ["official"]);
  setChosen("release-type", []);
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

// Editions side by side: each song's position on each edition; songs that are not on
// every edition are marked (bonus tracks, removed tracks), moved tracks show different numbers.
function compareEditions(release) {
  const cmp = release.comparison;
  if (!cmp || !cmp.songs.length) return "";
  const names = { [release.id]: "This" };
  for (const e of release.editions) {
    names[e.id] = e.edition ? capital(e.edition) : e.title !== release.title ? e.title : fmtDate(e.release_date) || "Edition";
  }
  const differs = cmp.songs.filter((s) => !s.on_all || new Set(Object.values(s.positions)).size > 1).length;
  return `<details class="compare"><summary>Compare editions <span class="muted">(${differs ? plural(differs, "difference") : "same tracks"})</span></summary>
    <div class="compare-scroll"><table class="rows compare-table"><thead><tr><th>Song</th>${cmp.releases.map((id) =>
      `<th title="${esc(names[id] || "")}">${esc(names[id] || "")}</th>`).join("")}</tr></thead><tbody>${cmp.songs.map((s) => {
      const moved = s.on_all && new Set(Object.values(s.positions)).size > 1;
      return `<tr class="${s.on_all ? (moved ? "moved" : "") : "partial"}"><td><a href="#/song/${s.id}">${esc(s.title)}</a></td>${cmp.releases.map((id) =>
        `<td class="pos">${s.positions[id] ? esc(s.positions[id]) : `<span class="muted">—</span>`}</td>`).join("")}</tr>`;
    }).join("")}</tbody></table></div>
    <p class="src">— not on that edition · highlighted: on some editions only · numbers differ: moved</p></details>`;
}

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
          ? ` (${e.tracks > release.tracks.length ? "+" : ""}${e.tracks - release.tracks.length})` : ""}</span></span></a>`).join("")}</div>
      ${compareEditions(release)}` : ""}
    ${chartBlock(release)}
    ${sources.length ? `<h3>Sources</h3>${serviceLinks(sources)}` : ""}`;
  showPanel();
}

/* ---------- wiring ---------- */

async function init() {
  const [summary, songs, releases, people] = await Promise.all([
    getJSON("/api/v1/summary"), getJSON("/api/v1/songs"), getJSON("/api/v1/releases"), getJSON("/api/v1/artists")]);
  state.songs = songs;
  state.summary = summary;
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
  // one option per version type (in the order of VERSION_TYPES) with the number of versions of that type
  const typeCounts = {};
  for (const variant of songs.flatMap((s) => s.variants || [])) typeCounts[variant.type] = (typeCounts[variant.type] || 0) + 1;
  const types = Object.keys(typeCounts).sort((a, b) => Object.keys(VERSION_TYPES).indexOf(a) - Object.keys(VERSION_TYPES).indexOf(b));
  $("vtype").insertAdjacentHTML("beforeend", types.map((t) =>
    `<option value="${esc(t)}">${esc(VERSION_TYPES[t] || t)} (${typeCounts[t]})</option>`).join("")
    + `<option value="wanted">Wanted: snippets only (${songs.filter((s) => s.wanted).length})</option>`);
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
// The values chosen in a filter (a Set; empty = everything). A <select multiple data-multi>
// holds several: its chips switch on and off, "All ..." (value "") clears the choice.
function chosen(id) {
  return new Set([...$(id).selectedOptions].map((option) => option.value).filter(Boolean));
}
function setChosen(id, values) {
  const select = $(id), wanted = new Set(values.map(String));
  for (const option of select.options) option.selected = select.multiple ? wanted.has(option.value) : option.value === [...wanted][0];
  if (select.multiple && ![...select.options].some((option) => option.selected && option.value)) {
    for (const option of select.options) if (option.value === "") option.selected = true;
  }
  refreshChips(select.parentElement);
}
function refreshChips(root = document) {
  for (const select of root.querySelectorAll("select[data-chips]")) {
    let group = select.nextElementSibling;
    if (!group || !group.classList.contains("seg")) {
      group = document.createElement("div");
      group.className = select.dataset.chips === "list" ? "seg seg-list" : "seg";  // "list": a checklist (eras)
      group.setAttribute("role", select.multiple ? "group" : "radiogroup");
      select.after(group);
      select.hidden = true;
      group.addEventListener("click", (event) => {
        const button = event.target.closest("button[data-value]");
        if (!button) return;
        if (select.multiple) {
          const option = [...select.options].find((o) => o.value === button.dataset.value);
          if (option.value === "") {
            for (const o of select.options) o.selected = o.value === "";
          } else {
            option.selected = !option.selected;
            const any = [...select.options].some((o) => o.selected && o.value);
            for (const o of select.options) if (o.value === "") o.selected = !any;
          }
          if (![...select.options].some((o) => o.selected)) option.selected = true;  // no "All": keep one choice
          select.dispatchEvent(new Event("change", { bubbles: true }));
          return;
        }
        if (button.dataset.value === select.value) return;
        select.value = button.dataset.value;
        select.dispatchEvent(new Event("change", { bubbles: true }));
      });
      select.addEventListener("change", () => refreshChips(select.parentElement));
    }
    const none = select.multiple && ![...select.options].some((o) => o.selected && o.value);
    group.innerHTML = [...select.options].map((option) => {
      const on = select.multiple ? option.selected || (option.value === "" && none) : option.value === select.value;
      const color = option.dataset.color;
      const swatch = color ? `<span class="swatch${color === "cover" ? " cover" : ""}"${color === "cover" ? "" : ` style="background:${esc(color)}"`}></span>` : "";
      return select.multiple
        ? `<button type="button" aria-pressed="${on}" class="${on ? "on" : ""}" data-value="${esc(option.value)}">${swatch}${esc(option.textContent)}</button>`
        : `<button type="button" role="radio" aria-checked="${on}" class="${on ? "on" : ""}" data-value="${esc(option.value)}">${swatch}${esc(option.textContent)}</button>`;
    }).join("");
  }
}

let timer;
const resetAndRender = () => { updateFilterCount(); renderSongs(); };
$("search").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(resetAndRender, 150); });
for (const id of ["era", "kind", "vtype", "sort"]) $(id).addEventListener("change", resetAndRender);
$("reset-filters").addEventListener("click", () => {
  setChosen("era", []); $("kind").value = "own"; setChosen("vtype", []); $("sort").value = "date";
  refreshChips($("filter-menu"));
  resetAndRender();
});
document.addEventListener("click", (event) => {  // close open menus on an outside click
  // a chip that was just redrawn is no longer in the page: that click was inside the menu
  if (!event.target.isConnected) return;
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

const PEOPLE_VIEWS = { grid: "▦ Grid", list: "☰ List", network: "◎ Network" };
function renderPeople() {
  saveFilters();
  try { state.peopleView = PEOPLE_VIEWS[state.peopleView] ? state.peopleView : localStorage.getItem("lwt-people-view") || "grid"; }
  catch (e) { state.peopleView = state.peopleView || "grid"; }
  if (!PEOPLE_VIEWS[state.peopleView]) state.peopleView = "grid";
  $("people-view-button").textContent = PEOPLE_VIEWS[state.peopleView];
  for (const b of document.querySelectorAll("#people-views button")) b.classList.toggle("on", b.dataset.view === state.peopleView);
  const query = fold($("people-search").value.trim());
  const roleSet = chosen("people-role"), sort = $("people-sort").value;
  $("people-filter-count").textContent = roleSet.size || sort !== "songs" ? `(${Boolean(roleSet.size) + (sort !== "songs")})` : "";
  const list = state.people.filter((p) => (!query || fold(p.name).includes(query))
    && (!roleSet.size || [...roleSet].some((role) => p.roles.some(ROLE_GROUPS[role]))))
    .sort(sort === "name" ? (a, b) => a.name.localeCompare(b.name)
      : sort === "first" ? (a, b) => (a.years[0] || "9999").localeCompare(b.years[0] || "9999") || b.songs - a.songs
        : (a, b) => b.songs - a.songs);
  $("people-total").textContent = `${list.length} ${list.length === 1 ? "person" : "people"} · the most credited people across the catalogue`;
  const roles = (p) => esc(p.roles.slice(0, 3).map(roleName).join(", "));
  if (state.peopleView === "network") { renderNetwork().catch((e) => { $("people-list").textContent = e.message; }); return; }
  $("people-list").innerHTML = !list.length ? `<p class="muted">Nothing found.</p>` : state.peopleView === "list"
    ? `<div class="people-rows">${list.map((p) => `<a class="person-row" href="#/artist/${p.id}">${avatar(p, "medium")}
        <span class="person-text"><span class="title">${esc(p.name)}</span><span class="muted">${roles(p)}</span></span>
        <span class="person-figures"><strong>${p.songs}</strong> ${p.songs === 1 ? "song" : "songs"}<br><span class="muted">${personYears(p)}</span></span></a>`).join("")}</div>`
    : `<div class="grid people-grid">${list.map((p) => `<a class="card person-card" href="#/artist/${p.id}">${avatar(p, "card-avatar")}
        <div class="card-body"><div class="title">${esc(p.name)}</div><div class="artist">${roles(p)}</div>
          <div class="artist">${plural(p.songs, "song")}${personYears(p) ? ` · ${personYears(p)}` : ""}</div></div></a>`).join("")}</div>`;
}
// Network: Lil Wayne in the middle, the artists he made songs with on the inner ring,
// producers on the outer ring; a circle's size = shared songs (in the chosen era).
async function renderNetwork() {
  state.network = state.network || await getJSON("/api/v1/network");
  const era = state.networkEra || "";
  const size = 720, mid = size / 2;
  const rings = { artist: { r: 205, label: "Artists" }, producer: { r: 320, label: "Producers" } };
  let nodes = "", lines = "", labels = "";
  for (const g of state.network.groups) {
    const ring = rings[g.group];
    const people = g.people.map((p) => ({ ...p, n: era ? p.eras[era] || 0 : p.songs })).filter((p) => p.n > 0)
      .sort((a, b) => b.n - a.n).slice(0, g.group === "artist" ? 36 : 44);
    const max = Math.max(1, ...people.map((p) => p.n));
    people.forEach((p, i) => {
      const angle = (i / people.length) * Math.PI * 2 - Math.PI / 2 + (g.group === "producer" ? Math.PI / people.length : 0);
      const x = mid + ring.r * Math.cos(angle), y = mid + ring.r * Math.sin(angle);
      const radius = 5 + 17 * Math.sqrt(p.n / max);
      lines += `<line x1="${mid}" y1="${mid}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" class="net-line net-${g.group}" style="stroke-opacity:${(0.12 + 0.5 * p.n / max).toFixed(2)}"/>`;
      nodes += `<a href="#/artist/${p.id}" class="net-node net-${g.group}"><title>${esc(p.name)}: ${plural(p.n, "song")}</title>
        <circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="${radius.toFixed(1)}"/></a>`;
      if (i < (g.group === "artist" ? 14 : 12)) {
        const lx = mid + (ring.r + radius + 8) * Math.cos(angle), ly = mid + (ring.r + radius + 8) * Math.sin(angle);
        const anchor = Math.cos(angle) > 0.3 ? "start" : Math.cos(angle) < -0.3 ? "end" : "middle";
        labels += `<text x="${lx.toFixed(1)}" y="${(ly + 4).toFixed(1)}" text-anchor="${anchor}" class="net-label">${esc(p.name)}</text>`;
      }
    });
  }
  const eraOptions = state.eras.map((e) => `<option value="${e.id}" ${String(e.id) === era ? "selected" : ""}>${esc(eraName(e))}</option>`).join("");
  $("people-total").textContent = "Lil Wayne's collaborators: artists on the inner ring, producers on the outer ring · circle size = shared songs";
  $("people-list").innerHTML = `<div class="net-bar"><select id="network-era" aria-label="Era"><option value="">All eras</option>${eraOptions}</select>
      <span class="net-key"><span class="net-dot net-artist"></span>Artists <span class="net-dot net-producer"></span>Producers</span></div>
    <div class="net-wrap"><svg viewBox="-60 -20 ${size + 120} ${size + 40}" class="network" role="img" aria-label="Collaboration network">
      ${lines}${nodes}<circle cx="${mid}" cy="${mid}" r="34" class="net-center"/>
      <clipPath id="net-clip"><circle cx="${mid}" cy="${mid}" r="32"/></clipPath>
      <image href="covers/artist.jpg" x="${mid - 32}" y="${mid - 32}" width="64" height="64" clip-path="url(#net-clip)" preserveAspectRatio="xMidYMid slice"/>
      ${labels}</svg></div>`;
  $("network-era").addEventListener("change", (event) => { state.networkEra = event.target.value; renderNetwork(); });
}

$("people-role").addEventListener("change", renderPeople);
$("people-sort").addEventListener("change", renderPeople);
$("reset-people-filters").addEventListener("click", () => {
  setChosen("people-role", []); $("people-sort").value = "songs";
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

// The trackers' era header: versions of each type, songs with a file, wanted songs.
function eraTrackerCounts(era) {
  const songs = state.songs.filter((s) => s.era === era.id && !s.guest);
  const byType = {};
  for (const v of songs.flatMap((s) => s.variants || [])) byType[v.type] = (byType[v.type] || 0) + 1;
  const parts = Object.keys(VERSION_TYPES).filter((t) => byType[t]).map((t) =>
    `<span class="era-count"><strong>${byType[t]}</strong> ${esc((VERSION_TYPES[t] || t).toLowerCase())}</span>`);
  const files = songs.filter((s) => s.file).length, wanted = songs.filter((s) => s.wanted).length;
  if (files) parts.push(`<span class="era-count"><strong>${files}</strong> with a file</span>`);
  if (wanted) parts.push(`<span class="era-count"><strong>${wanted}</strong> wanted</span>`);
  return parts.length ? `<div class="era-tracker-counts">${parts.join("")}</div>` : "";
}

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
      ${eraTrackerCounts(era)}
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

/* ---------- library: favourites, playlists, radio (stored in this browser) ---------- */
const FAV_KEY = "lwt-favorites", LISTS_KEY = "lwt-playlists";
function readStore(key, fallback) { try { return JSON.parse(localStorage.getItem(key)) || fallback; } catch (e) { return fallback; } }
function writeStore(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* storage unavailable */ } }
const favorites = () => new Set(readStore(FAV_KEY, []));
const playlists = () => readStore(LISTS_KEY, []);
function favButton(id, big) {
  const on = favorites().has(id);
  return `<button type="button" class="fav-btn${on ? " on" : ""}${big ? " big" : ""}" data-fav="${id}" aria-pressed="${on}"
    title="${on ? "Remove from favourites" : "Add to favourites"}">${on ? "♥" : "♡"}</button>`;
}
function toggleFavorite(id) {
  const fav = favorites();
  if (fav.has(id)) fav.delete(id); else fav.add(id);
  writeStore(FAV_KEY, [...fav]);
  for (const b of document.querySelectorAll(`[data-fav="${id}"]`)) {
    const on = fav.has(id);
    b.classList.toggle("on", on); b.textContent = on ? "♥" : "♡"; b.setAttribute("aria-pressed", on);
  }
  if (!$("library-view").hidden) renderLibrary();
}
function addToPlaylist(songId, listId) {
  const lists = playlists();
  let list = lists.find((l) => l.id === listId);
  if (!list) {
    const name = prompt("Name of the new playlist", "My playlist");
    if (!name) return;
    list = { id: `pl${Date.now()}`, name: name.slice(0, 80), songs: [] };
    lists.push(list);
  }
  if (!list.songs.includes(songId)) list.songs.push(songId);
  writeStore(LISTS_KEY, lists);
  if (window.flash) flash(`Added to “${list.name}”`);
}
function songsById(ids) {
  const byId = new Map(state.songs.map((s) => [s.id, s]));
  return ids.map((id) => byId.get(id)).filter(Boolean);
}
function libraryRows(songs, listId) {
  return songs.map((s, i) => `<div class="lib-row"><span class="lib-num">${i + 1}</span>${songCover(s, "tiny")}
    <a href="#/song/${s.id}" class="lib-title">${esc(s.title)}<span class="muted">${esc([songArtist(s), s.album ? s.album.title : ""].filter(Boolean).join(" · "))}</span></a>
    ${playButton(s.play, `data-play-song="${s.id}"`, "")}
    ${listId ? `<button type="button" class="icon-mini" data-move="${listId}|${s.id}|-1" title="Up">↑</button><button type="button" class="icon-mini" data-move="${listId}|${s.id}|1" title="Down">↓</button>` : ""}
    <button type="button" class="icon-mini" data-remove="${listId || "fav"}|${s.id}" title="Remove">✕</button></div>`).join("");
}
function renderLibrary() {
  if (!state.songs) return;
  const fav = songsById([...favorites()]);
  const lists = playlists();
  const eraOptions = state.eras.map((e) => `<option value="${e.id}">${esc(eraName(e))}</option>`).join("");
  $("library").innerHTML = `
    <section class="home-card lib-radio"><h3>Radio</h3>
      <p class="muted">An endless shuffle: new songs are added while it plays (only songs that can be played).</p>
      <div class="lib-actions">
        <button type="button" class="act" data-radio="all">🔀 All songs</button>
        <button type="button" class="act" data-radio="released">Released</button>
        <button type="button" class="act" data-radio="leaked">Leaks</button>
        <button type="button" class="act" data-radio="fav">♥ Favourites</button>
        <span class="lib-era"><select id="radio-era" aria-label="Era">${eraOptions}</select><button type="button" class="act" data-radio="era">Era radio</button></span>
      </div></section>
    <section class="home-card"><div class="lib-head"><h3>Favourites <span class="muted">${fav.length}</span></h3>
      ${fav.length ? `<button type="button" class="act ghost" data-play-list="fav">▶ Play</button><button type="button" class="act ghost" data-radio="fav">🔀 Shuffle</button>` : ""}</div>
      ${fav.length ? libraryRows(fav) : `<p class="muted">Tap ♡ on a song to keep it here.</p>`}</section>
    <section class="home-card"><div class="lib-head"><h3>Playlists <span class="muted">${lists.length}</span></h3>
      <button type="button" class="act ghost" id="new-playlist">＋ New playlist</button></div>
      ${lists.length ? lists.map((l) => {
        const songs = songsById(l.songs);
        return `<details class="lib-list" ${state.openList === l.id ? "open" : ""} data-list="${l.id}"><summary><strong>${esc(l.name)}</strong> <span class="muted">${plural(songs.length, "song")}</span></summary>
          <div class="lib-actions"><button type="button" class="act ghost" data-play-list="${l.id}">▶ Play</button>
            <button type="button" class="act ghost" data-radio="list:${l.id}">🔀 Shuffle</button>
            <button type="button" class="act ghost" data-rename="${l.id}">Rename</button>
            <button type="button" class="act ghost" data-delete-list="${l.id}">Delete</button></div>
          ${songs.length ? libraryRows(songs, l.id) : `<p class="muted">Add songs with “＋ Playlist” in a song window.</p>`}</details>`;
      }).join("") : `<p class="muted">No playlists yet.</p>`}</section>
    <p class="src">Favourites and playlists are kept in this browser only.</p>`;
}
document.addEventListener("click", (event) => {
  const fav = event.target.closest("[data-fav]");
  if (fav) { event.preventDefault(); event.stopPropagation(); toggleFavorite(Number(fav.dataset.fav)); return; }
  const add = event.target.closest("[data-playlist-add]");
  if (add) {
    event.preventDefault();
    const lists = playlists();
    document.querySelector(".playlist-pop")?.remove();
    add.insertAdjacentHTML("afterend", `<div class="playlist-pop">${lists.map((l) => `<button type="button" data-pl-pick="${l.id}" data-song="${add.dataset.playlistAdd}">${esc(l.name)}</button>`).join("")}
      <button type="button" data-pl-pick="" data-song="${add.dataset.playlistAdd}">＋ New playlist…</button></div>`);
    return;
  }
  const pick = event.target.closest("[data-pl-pick]");
  if (pick) { addToPlaylist(Number(pick.dataset.song), pick.dataset.plPick); pick.parentElement.remove(); return; }
  if (!event.target.closest(".playlist-pop")) document.querySelector(".playlist-pop")?.remove();
}, true);
$("library").addEventListener("click", (event) => {
  const t = event.target.closest("button");
  if (!t) return;
  const lists = playlists();
  if (t.dataset.radio) {
    const r = t.dataset.radio;
    let pool = state.songs, label = "all songs";
    if (r === "released") { pool = state.songs.filter((s) => s.status === "released"); label = "released songs"; }
    if (r === "leaked") { pool = state.songs.filter((s) => s.status === "leaked"); label = "leaks"; }
    if (r === "fav") { pool = songsById([...favorites()]); label = "favourites"; }
    if (r === "era") { const e = $("radio-era").value; pool = state.songs.filter((s) => String(s.era) === e); label = eraName(state.eraById.get(Number(e))); }
    if (r.startsWith("list:")) { const l = lists.find((x) => x.id === r.slice(5)); pool = l ? songsById(l.songs) : []; label = l ? l.name : ""; }
    window.startRadio(pool, label);
  } else if (t.dataset.playList) {
    const ids = t.dataset.playList === "fav" ? [...favorites()] : (lists.find((l) => l.id === t.dataset.playList) || { songs: [] }).songs;
    window.playSongs(songsById(ids));
  } else if (t.id === "new-playlist") {
    const name = prompt("Name of the new playlist", "My playlist");
    if (name) { lists.push({ id: `pl${Date.now()}`, name: name.slice(0, 80), songs: [] }); writeStore(LISTS_KEY, lists); renderLibrary(); }
  } else if (t.dataset.rename) {
    const l = lists.find((x) => x.id === t.dataset.rename);
    const name = l && prompt("New name", l.name);
    if (name) { l.name = name.slice(0, 80); writeStore(LISTS_KEY, lists); state.openList = l.id; renderLibrary(); }
  } else if (t.dataset.deleteList) {
    const l = lists.find((x) => x.id === t.dataset.deleteList);
    if (l && confirm(`Delete the playlist “${l.name}”?`)) { writeStore(LISTS_KEY, lists.filter((x) => x !== l)); renderLibrary(); }
  } else if (t.dataset.remove) {
    const [where, id] = t.dataset.remove.split("|");
    if (where === "fav") toggleFavorite(Number(id));
    else { const l = lists.find((x) => x.id === where); l.songs = l.songs.filter((s) => s !== Number(id)); writeStore(LISTS_KEY, lists); state.openList = where; renderLibrary(); }
  } else if (t.dataset.move) {
    const [where, id, step] = t.dataset.move.split("|");
    const l = lists.find((x) => x.id === where), i = l.songs.indexOf(Number(id)), j = i + Number(step);
    if (i >= 0 && j >= 0 && j < l.songs.length) { [l.songs[i], l.songs[j]] = [l.songs[j], l.songs[i]]; writeStore(LISTS_KEY, lists); state.openList = where; renderLibrary(); }
  }
});
$("shuffle-shown").addEventListener("click", () => {
  const songs = (state.shown || []).filter((s) => !s.key);  // whole songs (a version filter shows versions)
  window.startRadio(songs.length ? songs : state.shown || [], "songs shown");
});

// Share: copy the page link of a song (with /play: the song starts when the link is opened).
document.addEventListener("click", async (event) => {
  const share = event.target.closest("[data-share]");
  if (!share) return;
  const url = `${location.origin}${location.pathname}${share.dataset.share}`;
  try { await navigator.clipboard.writeText(url); if (window.flash) flash("Link copied"); }
  catch (e) { prompt("Copy this link", url); }
});

/* ---------- charts and certifications (Wikipedia, charts_source.py) ---------- */
const CHART_NAMES = { US: "Billboard Hot 100", "US R&B/HH": "Hot R&B/Hip-Hop Songs", "US Rap": "Hot Rap Songs", CAN: "Canadian Hot 100",
                      UK: "UK Singles Chart", AUS: "ARIA", NZ: "New Zealand", IRL: "Ireland", FRA: "France", SWI: "Switzerland" };
function chartBlock(item) {
  const charts = item.charts || [], certs = item.certifications || [];
  if (!charts.length && !certs.length) return "";
  return `<h3>Charts</h3><div class="chart-badges">${charts.map((c) =>
    `<span class="chart-badge${c.peak === 1 ? " top1" : c.peak <= 10 ? " top10" : ""}" title="${esc(CHART_NAMES[c.chart] || c.chart)}">${esc(c.chart)} <strong>#${c.peak}</strong></span>`).join("")}</div>
    ${certs.length ? `<div class="chart-badges">${certs.map((c) => `<span class="cert-badge cert-${esc(c.level.replace(/^\d+×/, "").toLowerCase())}">${esc(c.body)} ${esc(c.level)}${c.note ? ` <span class="muted">(${esc(c.note)})</span>` : ""}</span>`).join("")}</div>` : ""}
    <p class="src">Peak positions and certifications from Wikipedia</p>`;
}
function riaaUnits(certs) {  // Diamond = 10 x Platinum
  let units = 0;
  for (const c of certs || []) {
    if (c.body !== "RIAA" || c.note) continue;
    const m = /^(\d+)×/.exec(c.level), n = m ? Number(m[1]) : 1;
    units = Math.max(units, /Diamond/i.test(c.level) ? 10 * n : /Platinum/i.test(c.level) ? n : 0);
  }
  return units;
}
async function renderCharts() {
  const data = state.chartData = state.chartData || await getJSON("/api/v1/charts");
  const shown = ["US", "US R&B/HH", "US Rap", "UK", "CAN"];
  const songs = data.songs.filter((s) => Object.keys(s.peaks).length || s.certifications.length)
    .sort((a, b) => (a.peaks.US || 999) - (b.peaks.US || 999) || (a.peaks["US R&B/HH"] || 999) - (b.peaks["US R&B/HH"] || 999));
  const albums = data.albums.sort((a, b) => (a.peaks.US || 999) - (b.peaks.US || 999));
  const ones = songs.filter((s) => s.peaks.US === 1).length, top10 = songs.filter((s) => s.peaks.US <= 10).length;
  const diamond = songs.filter((s) => (s.certifications || []).some((c) => c.body === "RIAA" && /Diamond/i.test(c.level) && !c.note)).length;
  const albumOnes = albums.filter((a) => a.peaks.US === 1).length;
  const riaaRow = (certs) => (certs || []).filter((c) => c.body === "RIAA" && !c.note).map((c) => c.level).join(", ");
  const cell = (peak) => peak ? `<td class="peak${peak === 1 ? " top1" : peak <= 10 ? " top10" : ""}">${peak}</td>` : `<td class="muted">—</td>`;
  $("charts").innerHTML = `
    <div class="home-stats">
      <div class="home-stat"><strong>${ones}</strong><span>US Hot 100 #1 songs</span></div>
      <div class="home-stat"><strong>${top10}</strong><span>US top-10 songs</span></div>
      <div class="home-stat"><strong>${songs.filter((s) => s.peaks.US).length}</strong><span>songs on the Hot 100</span></div>
      <div class="home-stat"><strong>${diamond}</strong><span>RIAA Diamond songs</span></div>
      <div class="home-stat"><strong>${albumOnes}</strong><span>US #1 albums</span></div>
    </div>
    <h3 class="home-h">Albums</h3>
    <div class="check-scroll"><table class="check-table chart-table"><thead><tr><th>Album</th>${shown.map((c) => `<th title="${esc(CHART_NAMES[c] || c)}">${esc(c)}</th>`).join("")}<th>RIAA</th></tr></thead>
      <tbody>${albums.map((a) => `<tr><td><a href="#/release/${a.id}">${esc(a.title)}</a> <span class="muted">${esc((a.release_date || "").slice(0, 4))}</span></td>
        ${shown.map((c) => cell(a.peaks[c])).join("")}<td>${esc(riaaRow(a.certifications))}</td></tr>`).join("")}</tbody></table></div>
    <h3 class="home-h">Songs</h3>
    <div class="check-scroll"><table class="check-table chart-table"><thead><tr><th>Song</th>${shown.map((c) => `<th title="${esc(CHART_NAMES[c] || c)}">${esc(c)}</th>`).join("")}<th>RIAA</th></tr></thead>
      <tbody>${songs.map((s) => `<tr><td><a href="#/song/${s.id}">${esc(s.title)}</a>${s.guest ? ` <span class="muted">${esc(s.artist)}</span>` : ""}</td>
        ${shown.map((c) => cell(s.peaks[c])).join("")}<td>${esc(riaaRow(s.certifications))}</td></tr>`).join("")}</tbody></table></div>
    <p class="src">Peak positions and certifications from the Wikipedia discography pages of Lil Wayne (singles, albums).</p>`;
}

/* ---------- home: the start page ---------- */
function homeSongPick() {
  const pool = state.songs.filter((s) => !s.guest && s.status === "released" && playable(s.play));
  return pool[Math.floor(Math.random() * pool.length)];
}
function homeSongCard(song) {
  if (!song) return `<p class="muted">No playable song.</p>`;
  const era = eraOf(song);
  return `<div class="home-song"><a href="#/song/${song.id}">${songCover(song, "home-cover")}</a>
    <div><a class="title" href="#/song/${song.id}">${esc(song.title)}</a>
      <div class="muted">${esc([songArtist(song), song.album ? song.album.title : "", era ? eraName(era) : ""].filter(Boolean).join(" · "))}</div>
      <div class="home-song-actions">${playButton(song.play, `data-play-song="${song.id}"`)}
        <button type="button" class="act ghost" id="home-reroll" title="Another song">🎲 Another</button></div></div></div>`;
}
async function renderHome() {
  const s = state.summary;
  if (!s) return;
  const st = s.status || {}, c = s.counts || {};
  const tile = (n, label, href, cls = "") => `<a class="home-stat ${cls}" href="${href}"><strong>${Number(n || 0).toLocaleString("en-US")}</strong><span>${esc(label)}</span></a>`;
  const official = (state.releases || []).filter((r) => r.discography === "official" && /^\d{4}-\d{2}-\d{2}$/.test(r.release_date || ""))
    .sort((a, b) => b.release_date.localeCompare(a.release_date));
  const latest = official[0];
  const links = [["songs", "Songs"], ["releases", "Releases"], ["eras", "Eras"], ["timeline", "Timeline"], ["videos", "Videos"],
                 ["people", "People"], ["stats", "Statistics"], ["completeness", "Completeness"]];
  $("home").innerHTML = `
    <div class="home-hero">
      <svg class="home-logo" viewBox="0 0 64 64" aria-hidden="true"><defs><linearGradient id="home-red" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0" stop-color="#f0574a"/><stop offset="1" stop-color="#b3141c"/></linearGradient></defs>
        <rect width="64" height="64" rx="16" fill="url(#home-red)"/>
        <polyline points="13,20 22,45 32,27 42,45 51,20" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>         <circle cx="13" cy="20" r="3.6" fill="#fff"/><circle cx="51" cy="20" r="3.6" fill="#fff"/>         <path d="M32 7 C32 7 26.5 13.5 26.5 17 a5.5 5.5 0 0 0 11 0 C37.5 13.5 32 7 32 7 Z" fill="#fff"/></svg>
      <div><h2>Lil Wayne Tracker</h2>
        <p>Every song, version, leak and release of Lil Wayne, with the sources behind it.</p>
        <button type="button" class="home-search" id="home-search">Search songs, releases, people … <kbd>/</kbd></button></div>
    </div>
    <div class="home-stats">
      ${tile(c.songs, "songs", "#/songs")}${tile(st.released, "released", "#/songs", "released")}
      ${tile(st.leaked, "leaked", "#/songs", "leaked")}${tile(st.unreleased, "unreleased", "#/songs", "unreleased")}
      ${tile(c.releases, "releases", "#/releases")}${tile(c.guest_songs, "guest appearances", "#/songs")}${tile(c.versions, "versions", "#/songs")}
    </div>
    <div class="home-grid">
      <section class="home-card"><h3>On this day</h3><div id="home-today"><p class="muted">Loading…</p></div></section>
      <section class="home-card"><h3>Song of the moment</h3><div id="home-song">${homeSongCard(homeSongPick())}</div></section>
      <section class="home-card"><h3>Latest release</h3>${latest ? `<a class="home-release" href="#/release/${latest.id}">${releaseCover(latest, "home-cover")}
        <span><span class="title">${esc(latest.title)}</span><span class="muted">${esc((RELEASE_TYPES[latest.release_type] || latest.release_type).replace(/s$/, ""))} · ${esc(fmtDate(latest.release_date))}</span></span></a>` : ""}</section>
      <section class="home-card"><h3>Video</h3><div id="home-video"><p class="muted">Loading…</p></div></section>
    </div>
    <h3 class="home-h">Eras</h3>
    <div class="home-eras">${state.eras.map((e) => `<a class="home-era" href="#/era/${e.id}">${coverImg(e, "home-era-cover")}<span>${esc(eraName(e))}</span></a>`).join("")}</div>
    <h3 class="home-h">Explore</h3>
    <div class="home-links">${links.map(([key, label]) => `<a class="home-link" href="#/${key}"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="${NAV_ICONS[key] || ""}"/></svg>${esc(label)}</a>`).join("")}</div>`;
  $("home-search").addEventListener("click", (event) => { event.stopPropagation(); setGlobalSearch(true); });
  // on this day
  const now = new Date(), monthDay = `${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
  getJSON(`/api/v1/on-this-day/${monthDay}`).then((items) => {
    $("home-today").innerHTML = items.length ? items.slice(0, 5).map((r) => `<a class="home-row" href="#/release/${r.id}">${releaseCover(r, "tiny")}
      <span><span class="title">${esc(r.title)}</span><span class="muted">${esc(r.release_date.slice(0, 4))} · ${now.getFullYear() - Number(r.release_date.slice(0, 4))} years ago${r.discography === "official" ? "" : " · bootleg"}</span></span></a>`).join("")
      : `<p class="muted">Nothing came out on ${esc(now.toLocaleDateString("en-US", { month: "long", day: "numeric" }))} in earlier years.</p>`;
  }).catch(() => { $("home-today").innerHTML = `<p class="muted">—</p>`; });
  // a video: an official performance or music video
  (state.videos ? Promise.resolve(state.videos) : getJSON("/api/v1/videos")).then((videos) => {
    state.videos = state.videos || videos;
    const pool = videos.filter((v) => v.official && ["performance", "music_video"].includes(v.kind));
    const video = pool[Math.floor(Math.random() * pool.length)];
    $("home-video").innerHTML = video ? videoCard(video) : `<p class="muted">No videos yet.</p>`;
  }).catch(() => { $("home-video").innerHTML = `<p class="muted">—</p>`; });
}
document.addEventListener("click", (event) => {
  if (event.target.closest("#home-reroll")) $("home-song").innerHTML = homeSongCard(homeSongPick());
});

/* ---------- videos: YouTube (official embeds, privacy-enhanced mode) ---------- */
const VIDEO_KINDS = { music_video: "Music videos", performance: "Performances", freestyle: "Freestyles", interview: "Interviews" };
function videoThumb(video) {
  return `https://i.ytimg.com/vi/${encodeURIComponent(video.youtube_id)}/hqdefault.jpg`;
}
function videoCard(video) {
  const song = video.songs[0];
  return `<button type="button" class="card video-card" data-video="${esc(video.youtube_id)}">
    <span class="video-thumb"><img src="${esc(videoThumb(video))}" alt="" loading="lazy"><span class="video-play" aria-hidden="true">▶</span></span>
    <span class="card-body"><span class="title">${esc(video.title)}</span>
      <span class="artist">${esc(video.channel || "")}${video.official ? "" : " · unofficial upload"}${noEmbed().has(video.youtube_id) ? " · ↗ YouTube" : ""}</span>
      <span class="artist">${esc(VIDEO_KINDS[video.kind] ? VIDEO_KINDS[video.kind].replace(/s$/, "") : video.kind)}${video.event ? ` · ${esc(video.event)}` : ""}${song ? ` · ${esc(song.title)}` : ""}${!video.event && (video.date || video.song_date) ? ` · ${esc((video.date || video.song_date).slice(0, 4))}` : ""}</span></span></button>`;
}
async function renderVideos() {
  if (!state.videos) {
    state.videos = await getJSON("/api/v1/videos");
    const kinds = [...new Set(state.videos.map((v) => v.kind))];
    $("video-kind").insertAdjacentHTML("beforeend", kinds.map((k) => `<option value="${esc(k)}">${esc(VIDEO_KINDS[k] || k)}</option>`).join(""));
    $("video-era").insertAdjacentHTML("beforeend", state.eras.map((e) => `<option value="${e.id}">${esc(eraName(e))}</option>`).join(""));
    refreshChips($("video-filter-menu"));
  }
  const query = fold($("video-search").value.trim()), kinds = chosen("video-kind"), eras = chosen("video-era");
  const official = $("video-official").checked;
  $("video-filter-count").textContent = kinds.size + eras.size ? `(${Boolean(kinds.size) + Boolean(eras.size)})` : "";
  const list = state.videos.filter((v) => (!official || v.official) && (!kinds.size || kinds.has(v.kind))
    && (!eras.size || v.songs.some((s) => eras.has(String(s.era))))
    && (!query || fold([v.title, v.channel, v.event || "", ...v.songs.map((s) => s.title)].join(" ")).includes(query)))
    .sort((a, b) => (a.date || a.song_date || "9999").localeCompare(b.date || b.song_date || "9999"));
  $("video-total").textContent = `${plural(list.length, "video")} · YouTube, played in its privacy-enhanced embed${official ? " · official channels only" : ""}`;
  $("video-grid").innerHTML = list.map(videoCard).join("") || `<p class="muted">No videos.</p>`;
}
// Videos whose owner does not allow playback on other sites (remembered in this browser).
const NO_EMBED_KEY = "lwt-no-embed";
function noEmbed() { try { return new Set(JSON.parse(localStorage.getItem(NO_EMBED_KEY) || "[]")); } catch (e) { return new Set(); } }
function markNoEmbed(id) {
  const ids = noEmbed(); ids.add(id);
  try { localStorage.setItem(NO_EMBED_KEY, JSON.stringify([...ids])); } catch (e) { /* storage unavailable */ }
}
const youtubeUrl = (id) => `https://www.youtube.com/watch?v=${encodeURIComponent(id)}`;
// The official YouTube IFrame Player API: it reports why a video cannot play here.
let youtubeApi = null;
function loadYoutubeApi() {
  youtubeApi = youtubeApi || new Promise((resolve, reject) => {
    window.onYouTubeIframeAPIReady = () => resolve(window.YT);
    const script = document.createElement("script");
    script.src = "https://www.youtube.com/iframe_api";
    script.onerror = () => { youtubeApi = null; reject(new Error("YouTube could not be loaded")); };
    document.head.appendChild(script);
  });
  return youtubeApi;
}
let youtubePlayer = null;
function videoBlocked(video, reason) {
  markNoEmbed(video.youtube_id);
  $("video-frame").innerHTML = `<div class="video-blocked"><img src="${esc(videoThumb(video))}" alt="">
    <div><p>${esc(reason)}</p><a class="act" href="${esc(youtubeUrl(video.youtube_id))}" target="_blank" rel="noopener noreferrer">▶ Watch on YouTube</a></div></div>`;
}
async function openVideo(youtubeId) {
  const video = (state.videos || []).find((v) => v.youtube_id === youtubeId)
    || (state.song && (state.song.videos || []).find((v) => v.youtube_id === youtubeId));
  if (!video) return;
  if (window.stopAllPlayback) window.stopAllPlayback();  // the music player pauses for the video
  $("video-caption").innerHTML = `<strong>${esc(video.title)}</strong><br><span class="muted">${esc(video.channel || "")}${video.official ? "" : " · unofficial upload"}</span>
    ${video.songs.map((s) => `<a href="#/song/${s.id}">${esc(s.title)}</a>`).join(" · ")}
    · <a href="${esc(youtubeUrl(video.youtube_id))}" target="_blank" rel="noopener noreferrer">Watch on YouTube ↗</a>`;
  $("video-dialog").showModal();
  if (noEmbed().has(video.youtube_id)) {
    videoBlocked(video, "The owner of this video allows it to be played on YouTube only.");
    return;
  }
  $("video-frame").innerHTML = `<div id="video-player"></div>`;
  try {
    const YT = await loadYoutubeApi();
    if (!$("video-dialog").open) return;
    youtubePlayer = new YT.Player("video-player", {
      host: "https://www.youtube-nocookie.com", videoId: video.youtube_id, width: "100%", height: "100%",
      playerVars: { autoplay: 1, rel: 0, playsinline: 1, origin: location.origin },
      events: {
        onError: (event) => {  // 101 / 150: not allowed on other sites; 100: removed; 153: no referrer
          const reason = event.data === 100 ? "This video is no longer on YouTube."
            : [101, 150, 153].includes(event.data) ? "The owner of this video allows it to be played on YouTube only."
              : "YouTube could not play this video here.";
          if (event.data !== 100) videoBlocked(video, reason);
          else $("video-frame").innerHTML = `<div class="video-blocked"><p>${esc(reason)}</p></div>`;
        },
      },
    });
  } catch (error) {
    videoBlocked(video, error.message);
  }
}
function closeVideo() {
  if (youtubePlayer && youtubePlayer.destroy) { try { youtubePlayer.destroy(); } catch (e) { /* gone */ } }
  youtubePlayer = null;
  $("video-frame").innerHTML = "";
  if ($("video-dialog").open) $("video-dialog").close();
}
for (const id of ["video-search", "video-official"]) $(id).addEventListener("input", () => renderVideos());
for (const id of ["video-kind", "video-era"]) $(id).addEventListener("change", () => renderVideos());
$("reset-video-filters").addEventListener("click", () => { setChosen("video-kind", []); setChosen("video-era", []); renderVideos(); });
document.addEventListener("click", (event) => {
  const card = event.target.closest("[data-video]");
  if (card) { event.preventDefault(); openVideo(card.dataset.video); }
});
$("video-close").addEventListener("click", closeVideo);
$("video-dialog").addEventListener("close", () => {
  if (youtubePlayer && youtubePlayer.destroy) { try { youtubePlayer.destroy(); } catch (e) { /* gone */ } }
  youtubePlayer = null;
  $("video-frame").innerHTML = "";
});
$("video-caption").addEventListener("click", (event) => { if (event.target.closest("a")) closeVideo(); });

/* ---------- completeness: how complete and checked the catalogue is ---------- */
const CHECK_LABELS = { date: "Date", cover: "Cover", musicbrainz: "MusicBrainz", listen: "Listen link",
                       era: "Era", source: "Source", producer: "Producer" };

function checkCell(check) {
  if (!check || !check.total) return `<td class="muted">—</td>`;
  const share = check.done / check.total;
  const level = share >= 0.95 ? "good" : share >= 0.6 ? "mid" : "low";
  return `<td class="cmp-cell ${level}" title="${check.done} of ${check.total}">
    <div class="check-bar"><span style="width:${(share * 100).toFixed(1)}%"></span></div>
    <span class="check-num">${Math.round(share * 100)}%</span> <span class="muted">${check.total - check.done ? `${check.total - check.done} missing` : "all"}</span></td>`;
}

function checkTable(rows, label) {
  const keys = [...new Set(rows.flatMap((r) => Object.keys(r.checks)))];
  return `<div class="check-scroll"><table class="check-table"><thead><tr><th></th>${keys.map((k) => `<th>${esc(CHECK_LABELS[k] || k)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr><th scope="row">${label(r)} <span class="muted">${r.total}</span></th>${keys.map((k) => checkCell(r.checks[k])).join("")}</tr>`).join("")}</tbody></table></div>`;
}

async function renderCompleteness() {
  const data = await getJSON("/api/v1/completeness");
  const releaseLabel = (r) => `${esc(r.discography === "official" ? "Official" : "Unofficial")} · ${esc(RELEASE_TYPES[r.type] || r.type)}`;
  const songLabel = (r) => (r.kind === "guest" ? "Guest appearances" : "Lil Wayne's songs");
  $("completeness").innerHTML = `
    <p class="muted">How complete the catalogue is: what share of releases and songs has a date, a cover, a source ...
      Missing data is the to-do list; a song without an era has no known date in any source.</p>
    <h3>Releases</h3>${checkTable(data.releases, releaseLabel)}
    <h3>Songs</h3>${checkTable(data.songs, songLabel)}
    <h3>Open questions</h3><ul class="open-questions">${data.open.map((o) =>
      `<li><strong>${o.count}</strong> ${esc(o.label)}</li>`).join("")}</ul>`;
}

/* ---------- global search: songs (titles, alternate titles, versions), releases, people ---------- */
function globalSearch(text) {
  const key = (s) => fold(s).replace(/[^\p{L}\p{N}]+/gu, "");  // "Mrs. Officer" = "mrs off…"
  const query = key(text.trim());
  if (query.length < 2) return `<p class="muted">Type at least two letters.</p>`;
  const rank = (name) => { const f = key(name); return f === query ? 0 : f.startsWith(query) ? 1 : f.includes(query) ? 2 : 9; };
  const best = (items, names) => items.map((item) => ({ item, score: Math.min(...names(item).map(rank)), length: key(names(item)[0]).length }))
    .filter((x) => x.score < 9).sort((a, b) => a.score - b.score || a.length - b.length).slice(0, 8).map((x) => x.item);
  const songs = best(state.songs || [], (s) => [s.title, ...(s.aliases || []), ...(s.variants || []).map((v) => `${s.title} ${v.name || ""}`)]);
  const releases = best(state.releases || [], (r) => [r.title]);
  const people = best(state.people || [], (p) => [p.name]);
  const group = (title, rows) => rows.length ? `<div class="gs-group"><div class="gs-title">${title}</div>${rows.join("")}</div>` : "";
  const html = group("Songs", songs.map((s) => `<a class="gs-row" href="#/song/${s.id}">${songCover(s, "tiny")}<span>${esc(s.title)}
      <span class="muted">${esc([songArtist(s), s.album ? s.album.title : "", (s.date || "").slice(0, 4)].filter(Boolean).join(" · "))}</span></span>${badge(s.status)}</a>`))
    + group("Releases", releases.map((r) => `<a class="gs-row" href="#/release/${r.id}">${releaseCover(r, "tiny")}<span>${esc(r.title)}
      <span class="muted">${esc((RELEASE_TYPES[r.release_type] || r.release_type || "").replace(/s$/, ""))} · ${esc((r.release_date || "").slice(0, 4))}</span></span></a>`))
    + group("People", people.map((p) => `<a class="gs-row" href="#/artist/${p.id}"><span>${esc(p.name)}</span></a>`));
  return html || `<p class="muted">Nothing found.</p>`;
}
function setGlobalSearch(open) {
  $("global-search").hidden = !open;
  $("global-search-open").setAttribute("aria-expanded", String(open));
  if (open) { $("global-search-input").focus(); $("global-search-input").select(); }
}
$("global-search-open").addEventListener("click", (event) => { event.stopPropagation(); setGlobalSearch($("global-search").hidden); });
$("global-search-input").addEventListener("input", () => { $("global-search-results").innerHTML = globalSearch($("global-search-input").value); });
$("global-search-input").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { const first = $("global-search-results").querySelector("a"); if (first) { location.hash = first.getAttribute("href"); setGlobalSearch(false); } }
  if (event.key === "Escape") setGlobalSearch(false);
});
$("global-search-results").addEventListener("click", (event) => { if (event.target.closest("a")) setGlobalSearch(false); });
document.addEventListener("click", (event) => {
  if (!$("global-search").hidden && !event.target.closest("#global-search, #global-search-open")) setGlobalSearch(false);
});
document.addEventListener("keydown", (event) => {
  if (event.key === "/" && !event.target.closest("input, textarea, select, [contenteditable]")) { event.preventDefault(); setGlobalSearch(true); }
});

/* ---------- routing (#/songs, #/song/12, #/release/8, #/artist/45 ...) ---------- */

const SECTION_NAMES = { home: "Home", about: "About Lil Wayne", songs: "Songs", eras: "Eras", timeline: "Timeline", releases: "Releases",
                        people: "People", library: "Library", videos: "Videos", charts: "Charts", stats: "Statistics", completeness: "Completeness" };

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
  if (name === "completeness") renderCompleteness().catch((e) => { $("completeness").textContent = e.message; });
  if (name === "videos") renderVideos().catch((e) => { $("video-grid").textContent = e.message; });
  if (name === "home") renderHome().catch((e) => { $("home").textContent = e.message; });
  if (name === "library") renderLibrary();
  if (name === "charts") renderCharts().catch((e) => { $("charts").textContent = e.message; });
  if (name === "about" && !state.aboutLoaded) renderAbout().catch((e) => { $("about").textContent = e.message; });
  if (name === "timeline" && !state.timeline) renderTimeline().catch((e) => { $("timeline").textContent = e.message; });
}

async function route() {
  let [, kind, id, action] = (location.hash.match(/^#\/(\w+)(?:\/(\d+))?(?:\/(play))?/) || []);
  if (!kind) kind = "home";  // the start page
  if (kind === "review") { location.hash = "#/songs"; return; }  // the "Needs review" page was removed (owner, Nov 15, 2026)
  if (kind in SECTION_NAMES) showTab(kind);
  if (kind === "eras") { $("era-grid").hidden = false; $("era-view-bar").hidden = false; $("era-detail").hidden = true; }
  try {
    if (kind === "era" && id) { showTab("eras"); $("panel").hidden = true; await openEra(id); }
    else if (kind === "song" && id) {
      await openSong(id);
      if (action === "play") {  // a shared link: start the song (the browser may still ask for a click)
        const button = document.querySelector("#panel-body .head-play .play-btn:not([data-add])");
        if (button) button.click();
        history.replaceState(null, "", `#/song/${id}`);
      }
    }
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
// Lyrics style (Settings -> Lyrics): CSS variables used by .pf-lyrics.
const LYRIC_FONTS = { serif: 'Georgia, "Times New Roman", serif', rounded: '"Nunito", "Segoe UI Rounded", "Arial Rounded MT Bold", system-ui, sans-serif',
                      mono: 'ui-monospace, "Cascadia Mono", Consolas, monospace', condensed: '"Arial Narrow", "Roboto Condensed", sans-serif-condensed, sans-serif' };
function applyLyricStyle(settings) {
  const root = document.documentElement.style;
  root.setProperty("--lyr-size", `${Number(settings.lyrSize) || 20}px`);
  root.setProperty("--lyr-font", LYRIC_FONTS[settings.lyrFont] || "inherit");
  root.setProperty("--lyr-weight", settings.lyrWeight || "700");
  root.setProperty("--lyr-now", settings.lyrColor === "accent" ? "var(--accent)"
    : settings.lyrColor === "custom" && /^#[0-9a-f]{6}$/i.test(settings.lyrCustom || "") ? settings.lyrCustom : "#fff");
  root.setProperty("--lyr-dim", settings.lyrDim || "0.42");
  root.setProperty("--lyr-align", settings.lyrAlign === "center" ? "center" : "left");
}
const LYRIC_FIELDS = { lyrSize: "set-lyr-size", lyrFont: "set-lyr-font", lyrWeight: "set-lyr-weight", lyrColor: "set-lyr-color",
                       lyrCustom: "set-lyr-custom", lyrDim: "set-lyr-dim", lyrAlign: "set-lyr-align" };
const LYRIC_DEFAULTS = { lyrSize: "20", lyrFont: "", lyrWeight: "700", lyrColor: "white", lyrCustom: "#ffd166", lyrDim: "0.42", lyrAlign: "left" };
function lyricSettingsFromForm() {
  return Object.fromEntries(Object.entries(LYRIC_FIELDS).map(([key, id]) => [key, $(id).value]));
}

function applySettings(settings) {
  applyLyricStyle(settings);
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
  for (const [key, id] of Object.entries(LYRIC_FIELDS)) $(id).value = s[key] || LYRIC_DEFAULTS[key];
  $("set-lyr-custom").hidden = $("set-lyr-color").value !== "custom";
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
// The lyric style changes at once too (the preview in the pane and the open player).
for (const id of Object.values(LYRIC_FIELDS)) {
  for (const type of ["input", "change"]) {  // the option chips report "change"
    $(id).addEventListener(type, () => {
      $("set-lyr-custom").hidden = $("set-lyr-color").value !== "custom";
      applyLyricStyle(lyricSettingsFromForm());
    });
  }
}
for (const id of ["set-theme", "set-accent"]) {
  $(id).addEventListener("change", () => applySettings({ ...loadSettings(), theme: $("set-theme").value, accent: $("set-accent").value }));
}
$("settings-dialog").addEventListener("close", () => {
  const s = { theme: $("set-theme").value, view: $("set-view").value, kind: $("set-kind").value, dates: $("set-dates").value,
              player: $("set-player").value, accent: $("set-accent").value, ...lyricSettingsFromForm() };
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
  const ERA_HEAD = 46, YEAR_HEAD = 26, LANE_HEAD = 26, LANE_GAP = 12, CHART = 80;
  // Each year is a column; a lane's covers of that year fill a grid inside the column in date
  // order, so a cover never crosses a year line or another cover at any zoom.  The whole
  // timeline fits the window height: covers shrink first, then a busy year shows "+N".
  const inner = scale - 2 * TL_PAD;
  const laneItems = TL_LANES.map((lane) => data.map((y) => y.releases.filter((r) => lane.types.includes(r.type) && fractionalYear(r.date))
    .sort((a, b) => fractionalYear(a.date) - fractionalYear(b.date) || a.title.localeCompare(b.title))));
  const layout = (lane, items, factor) => {
    const size = Math.max(22, Math.round(tlCoverSize(lane, scale) * factor));
    const perRow = Math.max(1, Math.floor((inner + TL_GAP) / (size + TL_GAP)));
    const rowHeight = size + (lane.caption && size >= 48 ? 40 : TL_GAP);
    const rows = Math.max(1, ...items.map((list) => Math.ceil(list.length / perRow)));
    return { size, perRow, rowHeight, rows, caption: lane.caption && size >= 48 };
  };
  const available = Math.max(440, window.innerHeight - 170);
  const fixed = ERA_HEAD + YEAR_HEAD + TL_LANES.length * (LANE_HEAD + LANE_GAP) + 8 + CHART + 34;
  let factor = 1, layouts = [];
  for (; factor >= 0.5; factor -= 0.05) {
    layouts = TL_LANES.map((lane, i) => layout(lane, laneItems[i], factor));
    if (fixed + layouts.reduce((sum, l) => sum + l.rows * l.rowHeight, 0) <= available) break;
  }
  // still too tall at the smallest covers: each lane gets rows in proportion to its need
  const need = layouts.reduce((sum, l) => sum + l.rows * l.rowHeight, 0);
  if (fixed + need > available) {
    for (const l of layouts) l.maxRows = Math.max(1, Math.floor(((available - fixed) * (l.rows * l.rowHeight / need)) / l.rowHeight));
  }
  let top = ERA_HEAD + YEAR_HEAD;
  const labels = [];  // lane names stay at the left edge while the canvas scrolls
  const laneHtml = TL_LANES.map((lane, laneIndex) => {
    const { size, perRow, rowHeight, caption } = layouts[laneIndex];
    const maxRows = layouts[laneIndex].maxRows || layouts[laneIndex].rows;
    const gridWidth = perRow * size + (perRow - 1) * TL_GAP;
    const laneTop = top;
    let rows = 1;
    const cells = data.map((y, yearIndex) => {
      let items = laneItems[laneIndex][yearIndex];
      const capacity = maxRows * perRow;
      const hidden = items.length > capacity ? items.slice(capacity - 1) : [];
      if (hidden.length) items = items.slice(0, capacity - 1);
      rows = Math.max(rows, Math.ceil((items.length + (hidden.length ? 1 : 0)) / perRow));
      const x0 = x(Number(y.year)) + TL_PAD + (inner - gridWidth) / 2;
      const more = hidden.length ? (() => {
        const i = items.length;
        const cx = x0 + (i % perRow) * (size + TL_GAP), cy = laneTop + LANE_HEAD + Math.floor(i / perRow) * rowHeight;
        return `<button type="button" class="htl-more" data-year="${esc(y.year)}" style="left:${cx}px;top:${cy}px;width:${size}px;height:${size}px"
          data-tip="${esc(`${y.year}: ${hidden.length} more|${hidden.slice(0, 12).map((r) => r.title).join(", ")}${hidden.length > 12 ? " …" : ""}|Click: the year in the list view`)}">+${hidden.length}</button>`;
      })() : "";
      return more + items.map((r, i) => {
        const approx = !/^\d{4}-\d{2}-\d{2}$/.test(r.date);
        const type = (RELEASE_TYPES[r.type] || r.type).replace(/s$/, "");
        const cx = x0 + (i % perRow) * (size + TL_GAP), cy = laneTop + LANE_HEAD + Math.floor(i / perRow) * rowHeight;
        return `<a class="htl-item${approx ? " approx" : ""}" href="#/release/${r.id}" style="left:${cx}px;top:${cy}px;width:${size}px"
          data-tip="${esc(`${r.title}|${type} · ${fmtDate(r.date)}${approx ? " (date not exact)" : ""}${r.editions > 1 ? `|${r.editions} editions` : ""}`)}">
          <img src="covers/${esc(r.cover_file || "artist.jpg")}" alt="" loading="lazy" style="width:${size}px;height:${size}px">
          ${caption ? `<span class="htl-caption" style="width:${size + TL_GAP}px">${esc(r.title)}</span>` : ""}</a>`;
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
  const more = event.target.closest(".htl-more");
  if (more) {
    state.timelineView = "list";
    try { localStorage.setItem("lwt-timeline-view", "list"); } catch (e) { /* storage unavailable */ }
    renderTimeline().then(() => { const section = document.getElementById(`tl-year-${more.dataset.year}`); if (section) section.scrollIntoView(); });
    return;
  }
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
    <section class="year" id="tl-year-${esc(y.year)}"><div class="year-label">${esc(y.year)}
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
  home: "M10 20v-6h4v6h5v-8h3L12 3 2 12h3v8z",
  library: "M12 21.35 10.55 20C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54z",
  songs: "M12 3v10.55A4 4 0 1 0 14 17V7h4V3h-6z",
  eras: "M13 3a9 9 0 0 0-9 9H1l3.89 3.89.07.14L9 12H6a7 7 0 1 1 2.05 4.95l-1.42 1.42A9 9 0 1 0 13 3zm-1 5v5l4.28 2.54.72-1.21-3.5-2.08V8H12z",
  timeline: "M3 5h2v14H3zm4 2h14v2H7zm0 4h10v2H7zm0 4h14v2H7z",
  releases: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm0 14.5a4.5 4.5 0 1 1 0-9 4.5 4.5 0 0 1 0 9zm0-5.5a1 1 0 1 0 0 2 1 1 0 0 0 0-2z",
  people: "M16 11a3 3 0 1 0-3-3 3 3 0 0 0 3 3zm-8 0a3 3 0 1 0-3-3 3 3 0 0 0 3 3zm0 2c-2.33 0-7 1.17-7 3.5V19h14v-2.5C15 14.17 10.33 13 8 13zm8 0c-.29 0-.62.02-.97.05A4.22 4.22 0 0 1 17 16.5V19h6v-2.5c0-2.33-4.67-3.5-7-3.5z",
  stats: "M5 9.2h3V19H5zM10.6 5h2.8v14h-2.8zm5.6 8H19v6h-2.8z",
  completeness: "M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z",
  videos: "M21 6H3a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h18a1 1 0 0 0 1-1V7a1 1 0 0 0-1-1zm-11 9V9l5 3z",
  charts: "M3 13h4v8H3zm7-6h4v14h-4zm7-4h4v18h-4z",
  about: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm1 15h-2v-6h2zm0-8h-2V7h2z",
  appearance: "M12 3a9 9 0 0 0 0 18c.83 0 1.5-.67 1.5-1.5 0-.39-.15-.74-.39-1.01-.23-.26-.38-.61-.38-.99 0-.83.67-1.5 1.5-1.5H16a5 5 0 0 0 5-5c0-4.42-4.03-8-9-8zm-5.5 9a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm3-4a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm5 0a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3zm3 4a1.5 1.5 0 1 1 0-3 1.5 1.5 0 0 1 0 3z",
  player: "M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-2 14.5v-9l6 4.5-6 4.5z",
  lyrics: "M4 5h16v2H4zm0 4h10v2H4zm0 4h16v2H4zm0 4h10v2H4z",
};
for (const el of document.querySelectorAll(".drawer .tab[data-tab], .settings-nav [data-pane]")) {
  const path = NAV_ICONS[el.dataset.tab || el.dataset.pane];
  if (path) el.insertAdjacentHTML("afterbegin", `<svg class="nav-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg>`);
}
