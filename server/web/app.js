const API = "";

const els = {
  channelSelect: document.getElementById("channel-select"),
  channelName: document.getElementById("channel-name"),
  stationLogo: document.getElementById("station-logo"),
  healthDot: document.getElementById("health-dot"),
  cover: document.getElementById("cover"),
  coverFallback: document.getElementById("cover-fallback"),
  artworkGlow: document.getElementById("artwork-glow"),
  stateLabel: document.getElementById("state-label"),
  title: document.getElementById("title"),
  artist: document.getElementById("artist"),
  album: document.getElementById("album"),
  playlistSelect: document.getElementById("playlist-select"),
  playlistPos: document.getElementById("playlist-pos"),
  progressBar: document.getElementById("progress-bar"),
  elapsed: document.getElementById("elapsed"),
  duration: document.getElementById("duration"),
  volume: document.getElementById("volume"),
  volumeLabel: document.getElementById("volume-label"),
  btnPrev: document.getElementById("btn-prev"),
  btnPlay: document.getElementById("btn-play"),
  btnStop: document.getElementById("btn-stop"),
  btnNext: document.getElementById("btn-next"),
  btnSaveFile: document.getElementById("btn-savefile"),
  btnStream: document.getElementById("btn-stream"),
  streamLabel: document.getElementById("stream-label"),
  iconPlay: document.getElementById("icon-play"),
  iconPause: document.getElementById("icon-pause"),
  stream: document.getElementById("stream"),
  controlPanel: document.getElementById("control-panel"),
};

const state = {
  channels: {},
  playlists: [],
  activePlaylist: "",
  activeChannel: "",
  lastCoverName: "",
  playbackState: "stop",
  streamPlaying: false,
  streamWanted: false,
  streamReconnecting: false,
  volumeDragging: false,
  playlistChanging: false,
  currentFile: "",
  saveFileTimer: null,
  lastNowPlaying: null,
  authRequired: false,
  controlGranted: true,
  playlistPollId: null,
};

const mediaSession = {
  supported: typeof navigator !== "undefined" && "mediaSession" in navigator,
  lastMetadataKey: "",
};

function formatTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

function stripM3u(name) {
  return String(name || "").replace(/\.m3u$/i, "");
}

function passwordFromUrl() {
  return new URLSearchParams(window.location.search).get("password") || "";
}

/** Hängt ?password= aus der Seiten-URL an API-Pfade (für MPD-Steuerung). */
function apiPath(path) {
  const pwd = passwordFromUrl();
  if (!pwd) {
    return `${API}${path}`;
  }
  const url = new URL(path, window.location.href);
  url.searchParams.set("password", pwd);
  return `${API}${url.pathname}${url.search}`;
}

/** Kanal-Parameter für Metadaten-Proxy (backend_url aus channels.json). */
function withChannelQuery(path, channelId) {
  if (!channelId) {
    return path;
  }
  const url = new URL(path, window.location.href);
  url.searchParams.set("channel", channelId);
  return `${url.pathname}${url.search}`;
}

async function fetchJson(path) {
  const response = await fetch(apiPath(path), { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`${path} failed: ${response.status}`);
  }
  return response.json();
}

async function postText(path, body) {
  if (path.startsWith("/cmd/") && state.authRequired && !state.controlGranted) {
    throw new Error("control requires password");
  }

  const requestPath = path.startsWith("/cmd/")
    ? withChannelQuery(path, state.activeChannel)
    : path;

  const response = await fetch(apiPath(requestPath), {
    method: "POST",
    headers: { "Content-Type": "text/plain; charset=utf-8" },
    body,
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.error || `${path} failed: ${response.status}`);
  }
  return response.json();
}

function applyControlAccess() {
  const guest = state.authRequired && !state.controlGranted;
  document.body.classList.toggle("mode-guest", guest);
}

async function loadWebSession() {
  const session = await fetchJson("/web/session");
  state.authRequired = Boolean(session.auth_required);
  state.controlGranted = Boolean(session.control_granted);
  applyControlAccess();
}

function setHealth(ok) {
  els.healthDot.classList.toggle("ok", ok);
  els.healthDot.classList.toggle("bad", !ok);
  els.healthDot.title = ok ? "Verbunden" : "Verbindungsproblem";
}

function resolveArtworkUrl(data) {
  const fromApi = String(data.media_image_url || "").trim();
  if (fromApi.startsWith("http://") || fromApi.startsWith("https://")) {
    return fromApi;
  }

  const coverName = String(data.cover_name || "").trim();
  if (!coverName) {
    return null;
  }

  const coverPath = withChannelQuery(
    `/cover?name=${encodeURIComponent(coverName)}`,
    state.activeChannel,
  );
  return new URL(`${API}${coverPath}`, window.location.href).href;
}

function mediaMetadataKey(data) {
  return [
    data.title,
    data.artist,
    data.album,
    data.cover_name,
    data.media_image_url,
  ].join("\0");
}

function mediaSessionPlaybackState() {
  if (!state.streamPlaying) {
    return "none";
  }
  if (state.playbackState === "pause") {
    return "paused";
  }
  return "playing";
}

function clearMediaSession() {
  if (!mediaSession.supported) {
    return;
  }
  navigator.mediaSession.playbackState = "none";
  mediaSession.lastMetadataKey = "";
}

function syncMediaSession(data) {
  if (!mediaSession.supported) {
    return;
  }

  if (!state.streamPlaying) {
    clearMediaSession();
    return;
  }

  const title = String(data.title || "").trim() || "MPD Player";
  const artist = String(data.artist || "").trim();
  const album = String(data.album || "").trim();
  const artworkUrl = resolveArtworkUrl(data);
  const metadataKey = mediaMetadataKey(data);

  if (metadataKey !== mediaSession.lastMetadataKey) {
    mediaSession.lastMetadataKey = metadataKey;
    const artwork = artworkUrl
      ? [
          { src: artworkUrl, sizes: "512x512", type: "image/jpeg" },
          { src: artworkUrl, sizes: "256x256", type: "image/jpeg" },
        ]
      : [];

    try {
      navigator.mediaSession.metadata = new MediaMetadata({
        title,
        artist,
        album,
        artwork,
      });
    } catch (err) {
      console.warn("mediaSession.metadata failed", err);
    }
  }

  navigator.mediaSession.playbackState = mediaSessionPlaybackState();

  if ("setPositionState" in navigator.mediaSession) {
    const duration = Number(data.duration) || 0;
    const position = Number(data.elapsed) || 0;
    if (duration > 0) {
      try {
        navigator.mediaSession.setPositionState({
          duration,
          playbackRate: state.playbackState === "play" ? 1 : 0,
          position: Math.min(Math.max(0, position), duration),
        });
      } catch (_err) {
        // ignored when position state is invalid
      }
    }
  }
}

function initMediaSession() {
  if (!mediaSession.supported) {
    return;
  }

  navigator.mediaSession.setActionHandler("play", async () => {
    await startBrowserStream();
  });

  navigator.mediaSession.setActionHandler("pause", () => {
    pauseBrowserStream();
  });

  navigator.mediaSession.setActionHandler("previoustrack", () => {
    if (!state.controlGranted) {
      return;
    }
    postText("/cmd/player", "back").catch(console.warn);
  });

  navigator.mediaSession.setActionHandler("nexttrack", () => {
    if (!state.controlGranted) {
      return;
    }
    postText("/cmd/player", "next").catch(console.warn);
  });

  try {
    navigator.mediaSession.setActionHandler("stop", () => {
      els.stream.pause();
      setStreamUi(false);
    });
  } catch (_err) {
    // optional in some browsers
  }
}

function activeStreamUrl() {
  const channel = state.channels[state.activeChannel];
  return String(channel?.stream_url || els.stream.src || "").trim();
}

function reloadStreamElement() {
  const base = activeStreamUrl();
  if (!base) {
    return false;
  }
  const url = new URL(base, window.location.href);
  url.searchParams.set("t", String(Date.now()));
  els.stream.pause();
  els.stream.src = url.href;
  els.stream.load();
  return true;
}

function pauseBrowserStream({ clearWanted = true } = {}) {
  if (clearWanted) {
    state.streamWanted = false;
  }
  if (!els.stream.paused) {
    els.stream.pause();
  }
  if (state.streamPlaying) {
    setStreamUi(false);
  }
}

async function reconnectBrowserStream({ retries = 5, delayMs = 450 } = {}) {
  if (!state.streamWanted || !reloadStreamElement()) {
    return false;
  }

  state.streamReconnecting = true;
  try {
    for (let attempt = 0; attempt < retries; attempt += 1) {
      if (attempt > 0) {
        await new Promise((resolve) => setTimeout(resolve, delayMs));
        reloadStreamElement();
      }
      try {
        await els.stream.play();
        setStreamUi(true);
        return true;
      } catch (err) {
        console.warn("stream reconnect attempt failed", attempt + 1, err);
      }
    }
    setStreamUi(false);
    return false;
  } finally {
    state.streamReconnecting = false;
  }
}

async function startBrowserStream({ forceReload = false } = {}) {
  state.streamWanted = true;
  if (!activeStreamUrl()) {
    state.streamWanted = false;
    return;
  }
  if (forceReload) {
    await reconnectBrowserStream();
    return;
  }
  if (!els.stream.src) {
    reloadStreamElement();
  }
  try {
    await els.stream.play();
    setStreamUi(true);
  } catch (err) {
    console.warn(err);
    await reconnectBrowserStream();
  }
}

function setPlaybackUi(playbackState) {
  state.playbackState = playbackState || "stop";
  const playing = state.playbackState === "play";
  const paused = state.playbackState === "pause";

  els.iconPlay.classList.toggle("hidden", playing);
  els.iconPause.classList.toggle("hidden", !playing);

  if (playing) {
    els.stateLabel.textContent = "PLAYING";
  } else if (paused) {
    els.stateLabel.textContent = "PAUSED";
  } else {
    els.stateLabel.textContent = "STOPPED";
  }
}

function updateCover(coverName) {
  if (!coverName) {
    els.cover.classList.add("hidden");
    els.coverFallback.classList.remove("hidden");
    els.artworkGlow.style.background = "var(--accent-soft)";
    return;
  }

  const coverKey = `${state.activeChannel}\0${coverName}`;
  if (coverKey !== state.lastCoverName) {
    state.lastCoverName = coverKey;
    const coverPath = withChannelQuery(
      `/cover?name=${encodeURIComponent(coverName)}`,
      state.activeChannel,
    );
    els.cover.src = `${API}${coverPath}&t=${Date.now()}`;
  }

  els.cover.onload = () => {
    els.cover.classList.remove("hidden");
    els.coverFallback.classList.add("hidden");
  };
  els.cover.onerror = () => {
    els.cover.classList.add("hidden");
    els.coverFallback.classList.remove("hidden");
  };
}

function renderPlaylistOptions(playlists, active) {
  const previous = els.playlistSelect.value;
  els.playlistSelect.innerHTML = "";

  if (!playlists.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "Keine Playlists";
    els.playlistSelect.appendChild(option);
    els.playlistSelect.disabled = true;
    return;
  }

  els.playlistSelect.disabled = false;
  for (const name of playlists) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = stripM3u(name);
    els.playlistSelect.appendChild(option);
  }

  const target = active && playlists.includes(active)
    ? active
    : previous && playlists.includes(previous)
      ? previous
      : playlists[0];

  els.playlistSelect.value = target;
  state.activePlaylist = target;
}

function syncPlaylistSelect(active) {
  if (state.playlistChanging || !active) {
    return;
  }

  state.activePlaylist = active;
  if (state.playlists.includes(active) && els.playlistSelect.value !== active) {
    els.playlistSelect.value = active;
  }
}

function updatePlaylistPos(pos, length) {
  if (pos && length) {
    els.playlistPos.textContent = `${pos} / ${length}`;
    return;
  }
  if (pos) {
    els.playlistPos.textContent = `#${pos}`;
    return;
  }
  els.playlistPos.textContent = "";
}

function updateNowPlaying(data) {
  setPlaybackUi(data.state);
  els.title.textContent = data.title || "—";
  els.artist.textContent = data.artist || "";
  els.album.textContent = data.album || "";

  const elapsed = Number(data.elapsed) || 0;
  const duration = Number(data.duration) || 0;
  els.elapsed.textContent = formatTime(elapsed);
  els.duration.textContent = formatTime(duration);

  const percent = duration > 0 ? Math.min(100, (elapsed / duration) * 100) : 0;
  els.progressBar.style.width = `${percent}%`;

  updateCover(data.cover_name || "");
  syncPlaylistSelect(data.playlist || "");
  updatePlaylistPos(data.pos, data.playlist_length);

  state.currentFile = data.file || "";
  els.btnSaveFile.disabled = !state.currentFile;

  if (typeof data.volume === "number" && !state.volumeDragging) {
    els.volume.value = String(data.volume);
    els.volumeLabel.textContent = String(data.volume);
  }

  state.lastNowPlaying = data;
  syncMediaSession(data);
}

function updateChannelUi(channelId) {
  const channel = state.channels[channelId];
  if (!channel) {
    return;
  }

  state.activeChannel = channelId;
  els.channelName.textContent = channel.name || `Kanal ${channelId}`;

  const logoUrl = `${API}/stationlogo?channel=${encodeURIComponent(channelId)}`;
  els.stationLogo.onload = () => {
    els.stationLogo.classList.remove("hidden");
  };
  els.stationLogo.onerror = () => {
    els.stationLogo.classList.add("hidden");
  };
  els.stationLogo.src = logoUrl;

  if (channel.stream_url) {
    els.stream.src = channel.stream_url;
  }
}

function setStreamUi(playing) {
  state.streamPlaying = playing;
  els.btnStream.classList.toggle("active", playing);
  els.streamLabel.textContent = playing ? "Stream läuft" : "Stream starten";

  if (!playing) {
    clearMediaSession();
  } else if (state.lastNowPlaying) {
    syncMediaSession(state.lastNowPlaying);
  }
}

async function loadChannels() {
  state.channels = await fetchJson("/channels");
  const ids = Object.keys(state.channels).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));

  els.channelSelect.innerHTML = "";
  for (const id of ids) {
    const channel = state.channels[id];
    const option = document.createElement("option");
    option.value = id;
    option.textContent = channel.name || `Kanal ${id}`;
    els.channelSelect.appendChild(option);
  }

  if (ids.length > 0) {
    els.channelSelect.value = ids[0];
    updateChannelUi(ids[0]);
  }
}

async function loadPlaylists() {
  const path = withChannelQuery("/playlists", state.activeChannel);
  const data = await fetchJson(path);
  state.playlists = Array.isArray(data.playlists) ? data.playlists : [];
  renderPlaylistOptions(state.playlists, data.active || "");
}

async function pollNowPlaying() {
  try {
    const path = withChannelQuery("/nowplaying", state.activeChannel);
    const data = await fetchJson(path);
    updateNowPlaying(data);
  } catch (err) {
    console.warn(err);
  }
}

async function pollHealth() {
  try {
    const data = await fetchJson("/health");
    setHealth(data.status === "ok");
  } catch (_err) {
    setHealth(false);
  }
}

els.channelSelect.addEventListener("change", async () => {
  const keepStream = state.streamWanted;
  pauseBrowserStream({ clearWanted: false });
  state.lastCoverName = "";
  state.lastNowPlaying = null;
  updateChannelUi(els.channelSelect.value);
  pollNowPlaying().catch(console.warn);
  if (state.controlGranted) {
    await loadPlaylists().catch(console.warn);
  }
  if (keepStream) {
    state.streamWanted = true;
    await reconnectBrowserStream();
  }
});

els.playlistSelect.addEventListener("change", async () => {
  const name = els.playlistSelect.value;
  if (!name || name === state.activePlaylist) {
    return;
  }

  const keepStream = state.streamWanted || state.streamPlaying;
  if (keepStream) {
    state.streamWanted = true;
  }
  state.playlistChanging = true;
  try {
    await postText("/cmd/playlist", name);
    state.activePlaylist = name;
    if (keepStream) {
      await reconnectBrowserStream({ retries: 8, delayMs: 500 });
    }
  } catch (err) {
    console.warn(err);
    els.playlistSelect.value = state.activePlaylist || "";
  } finally {
    state.playlistChanging = false;
  }
});

els.btnPrev.addEventListener("click", () => {
  postText("/cmd/player", "back").catch(console.warn);
});

els.btnNext.addEventListener("click", () => {
  postText("/cmd/player", "next").catch(console.warn);
});

function flashMarkDeleteButton() {
  els.btnSaveFile.classList.add("marked");
  els.btnSaveFile.title = "Markiert";
  clearTimeout(state.saveFileTimer);
  state.saveFileTimer = setTimeout(() => {
    els.btnSaveFile.classList.remove("marked");
    els.btnSaveFile.title = "Zum Löschen markieren";
  }, 1500);
}

els.btnSaveFile.addEventListener("click", async () => {
  if (!state.currentFile) {
    return;
  }

  try {
    const data = await postText("/cmd/savefile", "");
    flashMarkDeleteButton();
    console.info("Marked for delete:", data.file, "→", data.path);
  } catch (err) {
    console.warn(err);
    els.btnSaveFile.title = "Markieren fehlgeschlagen";
  }
});

els.btnStop.addEventListener("click", () => {
  pauseBrowserStream();
  postText("/cmd/player", "stop").catch(console.warn);
});

els.btnPlay.addEventListener("click", async () => {
  const command = state.playbackState === "play" ? "stop" : "play";
  if (command === "stop") {
    pauseBrowserStream();
  }
  try {
    await postText("/cmd/player", command);
    if (command === "play") {
      await startBrowserStream();
    }
  } catch (err) {
    console.warn(err);
  }
});

els.volume.addEventListener("pointerdown", () => {
  state.volumeDragging = true;
});

els.volume.addEventListener("pointerup", () => {
  state.volumeDragging = false;
});

els.volume.addEventListener("input", () => {
  els.volumeLabel.textContent = els.volume.value;
});

els.volume.addEventListener("change", () => {
  state.volumeDragging = false;
  postText("/cmd/volume", els.volume.value).catch(console.warn);
});

els.btnStream.addEventListener("click", async () => {
  if (!els.stream.src) {
    return;
  }

  if (state.streamPlaying || state.streamWanted) {
    pauseBrowserStream();
    return;
  }

  await startBrowserStream();
});

els.stream.addEventListener("pause", () => {
  if (els.stream.ended || state.streamReconnecting || state.playlistChanging) {
    return;
  }
  setStreamUi(false);
  if (state.streamWanted) {
    reconnectBrowserStream().catch(console.warn);
  }
});

els.stream.addEventListener("play", () => {
  setStreamUi(true);
});

initMediaSession();

async function bootstrap() {
  try {
    await loadWebSession();
  } catch (err) {
    console.warn(err);
    state.authRequired = false;
    state.controlGranted = true;
    applyControlAccess();
  }

  await loadChannels();

  if (state.controlGranted) {
    try {
      await loadPlaylists();
    } catch (err) {
      console.warn(err);
    }
    state.playlistPollId = setInterval(() => {
      loadPlaylists().catch(console.warn);
    }, 30000);
  }

  pollNowPlaying();
  pollHealth();
}

bootstrap().catch((err) => {
  console.error(err);
  setHealth(false);
});

setInterval(pollNowPlaying, 1000);
setInterval(pollHealth, 15000);
