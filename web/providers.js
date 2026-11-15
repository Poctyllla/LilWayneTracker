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

// Spotify's embedded player (iFrame API): whole tracks when signed in, 30 s previews otherwise.
const spotifyProvider = {
  key: "spotify", server: "spotify", name: "Spotify", note: " (preview unless signed in)",
  capabilities: { seek: true, volume: false, position: true },
  active: false, controller: null, api: null, pendingPlay: false, state: { paused: true, position: 0, duration: 0 }, ended: false,
  allowed: () => true,
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
  async play(item, source) {
    const uri = `spotify:track:${(source && source.ref) || item.spotify}`;
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
    if (!this.active || !data) return;
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
  stop() { if (this.active && this.controller) this.controller.pause(); this.active = false; this.pendingPlay = false; },
  toggle() { if (this.controller) this.controller.togglePlay(); },
  seek(seconds) { if (this.controller) this.controller.seek(seconds); },
  setVolume() {},  // the embed has no volume API
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
