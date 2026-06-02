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
};

const state = {
  channels: {},
  playlists: [],
  activePlaylist: "",
  activeChannel: "",
  lastCoverName: "",
  playbackState: "stop",
  streamPlaying: false,
  volumeDragging: false,
  playlistChanging: false,
  currentFile: "",
  saveFileTimer: null,
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

async function fetchJson(path) {
  const response = await fetch(`${API}${path}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`${path} failed: ${response.status}`);
  }
  return response.json();
}

async function postText(path, body) {
  const response = await fetch(`${API}${path}`, {
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

function setHealth(ok) {
  els.healthDot.classList.toggle("ok", ok);
  els.healthDot.classList.toggle("bad", !ok);
  els.healthDot.title = ok ? "Verbunden" : "Verbindungsproblem";
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

  if (coverName !== state.lastCoverName) {
    state.lastCoverName = coverName;
    els.cover.src = `${API}/cover?name=${encodeURIComponent(coverName)}&t=${Date.now()}`;
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
  const data = await fetchJson("/playlists");
  state.playlists = Array.isArray(data.playlists) ? data.playlists : [];
  renderPlaylistOptions(state.playlists, data.active || "");
}

async function pollNowPlaying() {
  try {
    const data = await fetchJson("/nowplaying");
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

els.channelSelect.addEventListener("change", () => {
  if (state.streamPlaying) {
    els.stream.pause();
    setStreamUi(false);
  }
  updateChannelUi(els.channelSelect.value);
});

els.playlistSelect.addEventListener("change", async () => {
  const name = els.playlistSelect.value;
  if (!name || name === state.activePlaylist) {
    return;
  }

  state.playlistChanging = true;
  try {
    await postText("/cmd/playlist", name);
    state.activePlaylist = name;
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
  postText("/cmd/player", "stop").catch(console.warn);
});

els.btnPlay.addEventListener("click", () => {
  const command = state.playbackState === "play" ? "stop" : "play";
  postText("/cmd/player", command).catch(console.warn);
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

  if (state.streamPlaying) {
    els.stream.pause();
    setStreamUi(false);
    return;
  }

  try {
    await els.stream.play();
    setStreamUi(true);
  } catch (err) {
    console.warn(err);
    setStreamUi(false);
  }
});

els.stream.addEventListener("pause", () => {
  if (!els.stream.ended) {
    setStreamUi(false);
  }
});

els.stream.addEventListener("play", () => {
  setStreamUi(true);
});

Promise.all([loadChannels(), loadPlaylists()])
  .then(() => {
    pollNowPlaying();
    pollHealth();
  })
  .catch((err) => {
    console.error(err);
    setHealth(false);
  });

setInterval(pollNowPlaying, 1000);
setInterval(pollHealth, 15000);
setInterval(() => {
  loadPlaylists().catch(console.warn);
}, 30000);
