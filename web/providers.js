/* Lil Wayne Tracker -- audio providers (stage 5 of the architecture plan).
 *
 * Every source of sound is an adapter with one interface; player.js knows only
 * this interface (queue, repeat, shuffle, lyrics and the views stay there):
 *
 *   key            the field of a queue item / "play" object it plays (src/audio_providers.py)
 *   server         the provider name in GET /api/v1/versions/<id>/sources
 *   name, note     shown as "via <name>" (+ note)
 *   capabilities   {seek, volume, position}: what a page can do with it
 *   allowed()      may it play in this mode (local files: the local server only)
 *   play(item, source)  start the item with a source of that list ({ref} / {url});
 *                  without one (no list), the item's own field;  stop()  stop and forget it
 *   toggle()       play / pause;    seek(seconds);   setVolume(0..1)
 *
 * An adapter reports to the player through `host` (set by player.js):
 *   host.update({paused, position, duration})   host.ended()
 *   host.hint(text)   host.reveal() (open the full player: an embed needs a click)
 *   host.slot(name)   the element an embed lives in (#pf-spotify, #pf-apple)
 * Only the active adapter reports; a stopped one is silent.
 * A new official source = one adapter here + one provider in src/audio_providers.py.
 */

const providerHost = {  // replaced by player.js
  update() {}, ended() {}, hint() {}, reveal() {}, slot: (name) => document.getElementById(`pf-${name}`),
};

// The owner's own files, streamed by the local server (Range requests: seeking works).
const fileProvider = {
  key: "file_id", server: "local_file", name: "Local file", note: "",
  capabilities: { seek: true, volume: true, position: true },
  active: false,
  audio: document.getElementById("pl-audio"),
  allowed: () => API.features.stream,
  play(item, source) {
    this.active = true;
    this.audio.src = (source && source.url) || `/api/v1/stream/${item.file_id}`;
    this.audio.play().catch((error) => {  // AbortError: the source changed meanwhile (not a failure)
      if (error.name === "NotAllowedError") providerHost.hint("The browser blocked playback: press ▶.");
    });
  },
  stop() { this.active = false; this.audio.pause(); },
  toggle() { if (this.audio.paused) this.audio.play(); else this.audio.pause(); },
  seek(seconds) { this.audio.currentTime = seconds; },
  setVolume(level) { this.audio.volume = level; },
};
fileProvider.audio.addEventListener("play", () => { if (fileProvider.active) { providerHost.hint(""); providerHost.update({ paused: false }); } });
fileProvider.audio.addEventListener("pause", () => { if (fileProvider.active) providerHost.update({ paused: true }); });
fileProvider.audio.addEventListener("timeupdate", () => {
  if (fileProvider.active) providerHost.update({ position: fileProvider.audio.currentTime, duration: fileProvider.audio.duration || 0 });
});
fileProvider.audio.addEventListener("ended", () => { if (fileProvider.active) providerHost.ended(); });
fileProvider.audio.addEventListener("error", () => {
  if (fileProvider.active && fileProvider.audio.src) providerHost.hint("This file could not be played.");
});

// ---- Spotify sign-in: OAuth 2 with PKCE (no client secret), tokens in this browser only ----
// Used by the Web Playback SDK (Spotify Premium): whole songs and the volume slider.
const SPOTIFY_AUTH_KEY = "lwt-spotify-auth";
const SPOTIFY_SCOPES = "streaming user-read-email user-read-private user-read-playback-state user-modify-playback-state";
function randomText(length) {
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  return Array.from(bytes, (b) => chars[b % chars.length]).join("");
}
function base64url(buffer) {
  return btoa(String.fromCharCode(...new Uint8Array(buffer))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
const spotifyAuth = {
  clientId: () => (API.overview && API.overview.spotify_client_id) || null,
  redirectUri: () => `${location.origin}/`,
  load() { try { return JSON.parse(localStorage.getItem(SPOTIFY_AUTH_KEY) || "null"); } catch (e) { return null; } },
  save(data) {
    try { if (data) localStorage.setItem(SPOTIFY_AUTH_KEY, JSON.stringify(data)); else localStorage.removeItem(SPOTIFY_AUTH_KEY); }
    catch (e) { /* storage unavailable */ }
  },
  signedIn() { return Boolean(this.clientId() && this.load()); },
  async login() {
    const verifier = randomText(64), state = randomText(16);
    const challenge = base64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
    sessionStorage.setItem("lwt-spotify-pkce", JSON.stringify({ verifier, state, back: location.hash }));
    location.assign(`https://accounts.spotify.com/authorize?${new URLSearchParams({
      response_type: "code", client_id: this.clientId(), scope: SPOTIFY_SCOPES, redirect_uri: this.redirectUri(),
      code_challenge_method: "S256", code_challenge: challenge, state })}`);
  },
  // Back from Spotify (?code=... or ?error=...): exchange the code, clean the address.
  async finish() {
    const query = new URLSearchParams(location.search);
    if (!query.has("code") && !query.has("error")) return null;
    let pending = null;
    try { pending = JSON.parse(sessionStorage.getItem("lwt-spotify-pkce") || "null"); } catch (e) { /* none */ }
    sessionStorage.removeItem("lwt-spotify-pkce");
    history.replaceState(null, "", `${location.pathname}${(pending && pending.back) || ""}`);
    if (query.has("error")) return `Spotify sign-in was cancelled (${query.get("error")}).`;
    if (!pending || query.get("state") !== pending.state) return "Spotify sign-in did not match; please try again.";
    await this.token({ grant_type: "authorization_code", code: query.get("code"), redirect_uri: this.redirectUri(),
                       client_id: this.clientId(), code_verifier: pending.verifier });
    return "";
  },
  async token(body) {
    const response = await fetch("https://accounts.spotify.com/api/token", {
      method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: new URLSearchParams(body) });
    if (!response.ok) {
      if (body.grant_type === "refresh_token") this.save(null);
      throw new Error(`Spotify sign-in failed (${response.status})`);
    }
    const data = await response.json();
    const old = this.load() || {};
    this.save({ access: data.access_token, refresh: data.refresh_token || old.refresh,
                expires: Date.now() + (data.expires_in - 60) * 1000 });
  },
  async accessToken() {
    const auth = this.load();
    if (!auth) return null;
    if (Date.now() < auth.expires) return auth.access;
    await this.token({ grant_type: "refresh_token", refresh_token: auth.refresh, client_id: this.clientId() });
    return this.load().access;
  },
  logout() { this.save(null); },
};

// ---- Spotify Web Playback SDK: this browser becomes a Spotify Connect device ----
const spotifySdk = {
  player: null, deviceId: null, ready: null, failed: "",
  usable() { return !this.failed && spotifyAuth.signedIn(); },
  load() {
    if (this.ready) return this.ready;
    this.ready = new Promise((resolve, reject) => {
      const fail = (message) => { this.failed = message; reject(new Error(message)); };
      window.onSpotifyWebPlaybackSDKReady = () => {
        const player = new Spotify.Player({
          name: "Lil Wayne Tracker", volume: 0.8,
          getOAuthToken: (callback) => { spotifyAuth.accessToken().then(callback).catch(() => callback("")); },
        });
        player.addListener("ready", ({ device_id: deviceId }) => { this.deviceId = deviceId; resolve(player); });
        player.addListener("not_ready", () => { this.deviceId = null; });
        player.addListener("initialization_error", ({ message }) => fail(`Spotify player: ${message}`));
        player.addListener("authentication_error", () => { spotifyAuth.logout(); fail("Spotify sign-in expired; connect again in Settings."); });
        player.addListener("account_error", () => fail("The full Spotify player needs Spotify Premium; the embedded player is used."));
        player.addListener("player_state_changed", (state) => spotifyProvider.onSdkState(state));
        this.player = player;
        player.connect();
      };
      const script = document.createElement("script");
      script.src = "https://sdk.scdn.co/spotify-player.js";
      script.async = true;
      script.onerror = () => fail("The Spotify player could not be loaded.");
      document.head.appendChild(script);
    });
    return this.ready;
  },
  async api(method, path, body) {
    const token = await spotifyAuth.accessToken();
    return fetch(`https://api.spotify.com/v1${path}`, { method, headers: { Authorization: `Bearer ${token}`,
      "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
  },
};

// Spotify: the Web Playback SDK when signed in with Premium (whole songs, volume),
// otherwise the embedded player (iFrame API: whole tracks when signed in to Spotify
// in this browser, 30 s previews otherwise).
const spotifyProvider = {
  key: "spotify", server: "spotify", name: "Spotify",
  get note() { return spotifySdk.usable() ? "" : " (preview unless signed in)"; },
  get capabilities() { return { seek: true, volume: spotifySdk.usable(), position: true }; },
  active: false, mode: null, controller: null, api: null, pendingPlay: false,
  state: { paused: true, position: 0, duration: 0 }, ended: false, timer: null, trackId: null,
  allowed: () => true,
  async play(item, source) {
    const id = (source && source.ref) || item.spotify;
    if (spotifySdk.usable()) {
      try { await this.playSdk(id); return; } catch (error) { providerHost.hint(error.message); }
    }
    this.playEmbed(id);
  },
  async playSdk(id) {
    this.mode = "sdk";
    this.active = true;
    this.ended = false;
    this.trackId = id;
    this.state = { paused: true, position: 0, duration: 0 };
    const player = await spotifySdk.load();
    if (!this.active || this.trackId !== id) return;
    player.activateElement();  // browsers allow sound only after a click on the page
    let response = await spotifySdk.api("PUT", `/me/player/play?device_id=${spotifySdk.deviceId}`, { uris: [`spotify:track:${id}`] });
    if (response.status === 404) {  // the new device is not known to Spotify yet
      await new Promise((resolve) => setTimeout(resolve, 1200));
      response = await spotifySdk.api("PUT", `/me/player/play?device_id=${spotifySdk.deviceId}`, { uris: [`spotify:track:${id}`] });
    }
    if (!response.ok) {
      this.mode = null;
      if (response.status === 403) spotifySdk.failed = "The full Spotify player needs Spotify Premium; the embedded player is used.";
      throw new Error(spotifySdk.failed || `Spotify did not start the song (${response.status}).`);
    }
    clearInterval(this.timer);
    this.timer = setInterval(() => this.pollSdk(), 500);  // the SDK sends no position while playing
  },
  async pollSdk() {
    if (!this.active || this.mode !== "sdk" || !spotifySdk.player) return;
    const state = await spotifySdk.player.getCurrentState();
    if (state) this.onSdkState(state);
  },
  onSdkState(state) {
    if (!this.active || this.mode !== "sdk" || !state) return;
    const current = state.track_window && state.track_window.current_track;
    const last = this.state;
    this.state = { paused: state.paused, position: state.position / 1000, duration: state.duration / 1000 };
    if (!state.paused) providerHost.hint("");
    // the end: Spotify pauses at 0 after the last second, or moved on to another track
    const finished = state.paused && state.position === 0 && !last.paused && last.duration && last.position >= last.duration - 2.5;
    const movedOn = current && this.trackId && current.id !== this.trackId && !(current.linked_from && current.linked_from.id === this.trackId);
    if ((finished || movedOn) && !this.ended) {
      this.ended = true;
      if (movedOn) spotifySdk.player.pause();
      providerHost.ended();
      return;
    }
    providerHost.update(this.state);
  },
  playEmbed(id) {
    this.mode = "embed";
    this.playEmbedUri(`spotify:track:${id}`);
  },
  loadApi() {
    if (this.api) return this.api;
    this.api = new Promise((resolve, reject) => {
      window.onSpotifyIframeApiReady = resolve;
      const script = document.createElement("script");
      script.src = "https://open.spotify.com/embed/iframe-api/v1";
      script.async = true;
      script.onerror = () => { this.api = null; reject(new Error("Spotify player could not be loaded")); };
      document.head.appendChild(script);
    });
    return this.api;
  },
  async playEmbedUri(uri) {
    this.active = true;
    this.pendingPlay = true;
    this.ended = false;
    this.state = { paused: true, position: 0, duration: 0 };
    try {
      const api = await this.loadApi();
      if (!this.controller) {
        await new Promise((resolve) => api.createController(providerHost.slot("spotify"), { uri, width: "100%", height: 152 }, (controller) => {
          this.controller = controller;
          controller.addListener("ready", () => {
            if (this.pendingPlay && this.active) { this.pendingPlay = false; controller.play(); }
          });
          controller.addListener("playback_update", (event) => this.onUpdate(event.data));
          resolve();
        }));
      } else {
        this.controller.loadUri(uri);
      }
      // When the browser does not let the embed start by itself, ask for one click inside it.
      setTimeout(() => {
        if (this.active && this.state.paused && !this.state.position) {
          if (this.pendingPlay) { this.pendingPlay = false; this.controller.play(); }
          setTimeout(() => {
            if (this.active && this.state.paused && !this.state.position) {
              providerHost.hint("Press play in the Spotify player below.");
              providerHost.reveal();
            }
          }, 2000);
        }
      }, 1500);
    } catch (error) {
      providerHost.hint(error.message);
      providerHost.reveal();
    }
  },
  onUpdate(data) {
    if (!this.active || this.mode !== "embed" || !data) return;
    const wasPlaying = !this.state.paused, last = this.state.position, length = this.state.duration;
    this.state = { paused: data.isPaused, position: data.position / 1000, duration: data.duration / 1000 };
    if (!data.isPaused) providerHost.hint("");
    // The embed stops at the end of the track (or of the 30 s preview).
    const atEnd = data.duration && data.position >= data.duration - 400;
    const wrapped = wasPlaying && length && last >= length - 1.5 && data.position < 1000;  // reset to 0 at the end
    if (data.isPaused && (atEnd || wrapped) && !this.ended) {
      this.ended = true;
      providerHost.ended();
      return;
    }
    providerHost.update(this.state);
  },
  stop() {
    clearInterval(this.timer);
    if (this.active && this.mode === "sdk" && spotifySdk.player) spotifySdk.player.pause();
    if (this.active && this.mode === "embed" && this.controller) this.controller.pause();
    this.active = false;
    this.pendingPlay = false;
    this.mode = null;
  },
  toggle() {
    if (this.mode === "sdk" && spotifySdk.player) spotifySdk.player.togglePlay();
    else if (this.controller) this.controller.togglePlay();
  },
  seek(seconds) {
    if (this.mode === "sdk" && spotifySdk.player) spotifySdk.player.seek(Math.round(seconds * 1000));
    else if (this.controller) this.controller.seek(seconds);
  },
  setVolume(level) { if (spotifySdk.player) spotifySdk.player.setVolume(level); },  // the embed has no volume API
};

// Apple Music's embedded player: no script control (play / pause happen inside it).
const appleProvider = {
  key: "apple", server: "apple_music", name: "Apple Music", note: "",
  capabilities: { seek: false, volume: false, position: false },
  active: false,
  allowed: () => true,
  play(item, source) {
    this.active = true;
    const url = ((source && source.ref) || item.apple).replace("://music.apple.com/", "://embed.music.apple.com/");
    providerHost.slot("apple").innerHTML = `<iframe title="Apple Music player" src="${esc(url)}" height="175" loading="lazy"
      allow="autoplay *; encrypted-media *; clipboard-write" sandbox="allow-forms allow-popups allow-same-origin allow-scripts allow-storage-access-by-user-activation allow-top-navigation-by-user-activation"></iframe>`;
    providerHost.hint("Press play in the Apple Music player below.");
    providerHost.reveal();
  },
  stop() { this.active = false; providerHost.slot("apple").innerHTML = ""; },
  toggle() { providerHost.reveal(); },
  seek() {},
  setVolume() {},
};

// The order is the fallback order after the source chosen in Settings ("Play with").
const PROVIDERS = { spotify: spotifyProvider, apple: appleProvider, file: fileProvider };

// Settings -> Player: connect the Spotify account (local tracker with SPOTIFY_CLIENT_ID only).
function showSpotifyAccount(message) {
  const field = document.getElementById("spotify-account");
  if (!field) return;
  field.hidden = !spotifyAuth.clientId();
  const signedIn = spotifyAuth.signedIn();
  document.getElementById("spotify-connect").textContent = signedIn ? "Disconnect" : "Connect Spotify";
  document.getElementById("spotify-status").textContent = message
    || (signedIn ? (spotifySdk.failed || "Connected: whole songs and the volume slider (Spotify Premium).") : "Not connected.");
}
API_READY.then(() => spotifyAuth.finish())
  .then((message) => showSpotifyAccount(message || undefined))
  .catch((error) => showSpotifyAccount(error.message));
document.getElementById("spotify-connect").addEventListener("click", () => {
  if (spotifyAuth.signedIn()) {
    spotifyAuth.logout();
    if (spotifySdk.player) spotifySdk.player.disconnect();
    spotifySdk.player = null; spotifySdk.ready = null; spotifySdk.deviceId = null; spotifySdk.failed = "";
    showSpotifyAccount();
  } else {
    spotifyAuth.login().catch((error) => showSpotifyAccount(error.message));
  }
});
