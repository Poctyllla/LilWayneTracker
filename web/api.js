/* Lil Wayne Tracker -- client of the Catalog API (/api/v1, src/api_routes.py).
 *
 * The page works in two modes, told by the API itself (GET /api/v1/ overview):
 *   local   the tracker server on 127.0.0.1: every feature
 *   static  the copy written by export_static.py: the same answers as files
 *           (api/v1/<path>.json), no streaming of local files, no lyrics,
 *           no review page (overview.features says so)
 * Loaded before app.js and player.js; they call getJSON("/api/v1/...") and
 * read API.features.
 */

const API = {
  mode: "local",
  features: { stream: false, lyrics: false, review: false },  // until the overview has answered
  overview: null,
};
let STATIC = false;

// Local server first, then the static copy; a host that answers with an HTML page is skipped.
async function loadOverview() {
  for (const url of ["/api/v1/", "api/v1/index.json"]) {
    try {
      const response = await fetch(url);
      if (response.ok) return await response.json();
    } catch (e) { /* not this mode */ }
  }
  throw new Error("the API is not reachable");
}

const API_READY = loadOverview().then((overview) => {
  API.overview = overview;
  API.mode = overview.mode === "static" ? "static" : "local";
  API.features = { stream: false, lyrics: false, review: false, ...overview.features };
  STATIC = API.mode === "static";
  return overview;
});

// "/api/v1/songs/5" -> the file of the static copy ("api/v1/songs/5.json"; the overview is "api/v1/index.json").
function apiUrl(path) {
  if (!STATIC) return path;
  const rest = path.replace(/^\/api\/v1\/?/, "").replace(/\/$/, "");
  return `api/v1/${rest || "index"}.json`;
}

async function getJSON(path) {
  await API_READY;
  const response = await fetch(apiUrl(path));
  if (!response.ok) throw new Error(`${path}: ${response.status}`);
  return response.json();
}
