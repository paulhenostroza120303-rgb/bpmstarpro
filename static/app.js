const socket = io();
let activeWavesurfers = [];

let pendingUpdateUrl = null;

function checkAppUpdate() {
    fetch("/api/check_update")
        .then(r => r.json())
        .then(data => {
            if (data.has_update && data.download_url) {
                pendingUpdateUrl = data.download_url;
                const banner = document.getElementById("update-banner");
                const desc = document.getElementById("update-banner-desc");
                if (banner && desc) {
                    desc.textContent = `Version ${data.latest_version} lista. ${data.notes || ''}`;
                    banner.classList.remove("hidden");
                }
            }
        })
        .catch(() => {});
}

document.addEventListener("DOMContentLoaded", () => {
    checkAppUpdate();
    const updateBtn = document.getElementById("update-action-btn");
    if (updateBtn) {
        updateBtn.addEventListener("click", () => {
            if (!pendingUpdateUrl) return;
            updateBtn.disabled = true;
            updateBtn.textContent = "Descargando...";
            fetch("/api/trigger_update", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ download_url: pendingUpdateUrl })
            })
            .then(r => r.json())
            .then(res => {
                if (!res.success) {
                    alert(res.error || "Error al iniciar la actualizacion");
                    updateBtn.disabled = false;
                    updateBtn.textContent = "Actualizar ahora";
                }
            })
            .catch(() => {
                alert("Error al conectar con el servidor de actualizacion");
                updateBtn.disabled = false;
                updateBtn.textContent = "Actualizar ahora";
            });
        });
    }
});

// ========================
//  DOWNLOAD HELPER
// ========================

async function forceDownload(url, filename) {
    try {
        if (window.pywebview && window.pywebview.api && window.pywebview.api.save_file) {
            const result = await window.pywebview.api.save_file(url, filename);
            if (result.error && result.error !== "Cancelado") {
                alert("Error: " + result.error);
            }
        } else {
            const response = await fetch(url);
            if (!response.ok) throw new Error("Error al descargar");
            const blob = await response.blob();
            const blobUrl = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = blobUrl;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            setTimeout(() => URL.revokeObjectURL(blobUrl), 5000);
        }
    } catch (e) {
        alert("Error al descargar: " + e.message);
    }
}

function destroyAllWavesurfers() {
    activeWavesurfers.forEach(ws => { try { ws.destroy(); } catch(e) {} });
    activeWavesurfers = [];
}

// ========================
//  TABS
// ========================

document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
        document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
        document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
        tab.classList.add("active");
        document.getElementById("tab-" + tab.dataset.tab).classList.add("active");
    });
});

// ========================
//  DESCARGAR
// ========================

const urlInput = document.getElementById("url-input");
const pasteBtn = document.getElementById("paste-btn");
const analyzeBtn = document.getElementById("analyze-btn");
const statusMsg = document.getElementById("status-msg");
const videoInfo = document.getElementById("video-info");
const thumb = document.getElementById("thumb");
const vTitle = document.getElementById("v-title");
const vUploader = document.getElementById("v-uploader");
const vDuration = document.getElementById("v-duration");
const downloadBtn = document.getElementById("download-btn");
const progressSection = document.getElementById("progress-section");
const progressBar = document.getElementById("progress-bar");
const progressPercent = document.getElementById("progress-percent");
const progressStatus = document.getElementById("progress-status");
const progressSpeed = document.getElementById("progress-speed");
const progressEta = document.getElementById("progress-eta");
const completeSection = document.getElementById("complete-section");
const completeFilename = document.getElementById("complete-filename");
const saveBtn = document.getElementById("save-btn");
const newDownloadBtn = document.getElementById("new-download-btn");

let currentVideo = null;
let selectedFormat = "mp3";
let selectedQuality = "best";

pasteBtn.addEventListener("click", async () => {
    try {
        const text = await navigator.clipboard.readText();
        urlInput.value = text;
        urlInput.focus();
    } catch {
        urlInput.focus();
    }
});

document.querySelectorAll(".format-btn[data-format]").forEach((btn) => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".format-btn[data-format]").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        selectedFormat = btn.dataset.format;
    });
});

document.querySelectorAll(".quality-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".quality-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        selectedQuality = btn.dataset.quality;
    });
});

analyzeBtn.addEventListener("click", () => analyze());
urlInput.addEventListener("keydown", (e) => { if (e.key === "Enter") analyze(); });

function analyze() {
    const url = urlInput.value.trim();
    if (!url) return;
    analyzeBtn.disabled = true;
    videoInfo.classList.add("hidden");
    completeSection.classList.add("hidden");
    progressSection.classList.add("hidden");
    showStatus("Analizando enlace...", "loading");
    socket.emit("analyze", { url });
}

downloadBtn.addEventListener("click", () => {
    if (!currentVideo) return;
    downloadBtn.disabled = true;
    videoInfo.classList.add("hidden");
    progressSection.classList.remove("hidden");
    completeSection.classList.add("hidden");
    socket.emit("start_download", {
        url: urlInput.value.trim(),
        format: selectedFormat,
        quality: selectedQuality,
        title: currentVideo.title,
    });
});

newDownloadBtn.addEventListener("click", () => {
    completeSection.classList.add("hidden");
    videoInfo.classList.add("hidden");
    progressSection.classList.add("hidden");
    urlInput.value = "";
    urlInput.focus();
    analyzeBtn.disabled = false;
    downloadBtn.disabled = false;
    currentVideo = null;
});

socket.on("analyzing", () => showStatus("Obteniendo informacion del video...", "loading"));
socket.on("video_info", (data) => {
    currentVideo = data;
    hideStatus();
    analyzeBtn.disabled = false;
    thumb.src = data.thumbnail;
    vTitle.textContent = data.title;
    vUploader.textContent = data.uploader;
    vDuration.textContent = data.duration;
    videoInfo.classList.remove("hidden");
});
socket.on("analyze_error", (data) => { analyzeBtn.disabled = false; showStatus(data.error, "error"); });
socket.on("download_started", () => updateProgress(0, "Iniciando...", "-", "--:--"));
socket.on("progress", (data) => updateProgress(data.percent, data.status, data.speed, data.eta));
socket.on("download_complete", (data) => {
    progressSection.classList.add("hidden");
    completeSection.classList.remove("hidden");
    const fname = data.title + "." + data.ext;
    completeFilename.textContent = fname;
    saveBtn.onclick = function(e) {
        e.preventDefault();
        forceDownload("/download_file/" + encodeURIComponent(data.filename), fname);
    };
    downloadBtn.disabled = false;
});
socket.on("download_error", (data) => {
    progressSection.classList.add("hidden");
    showStatus(data.error, "error");
    downloadBtn.disabled = false;
    videoInfo.classList.remove("hidden");
});

function showStatus(msg, type) {
    statusMsg.textContent = msg;
    statusMsg.className = "status-msg " + type;
}
function hideStatus() { statusMsg.className = "status-msg hidden"; }
function updateProgress(percent, status, speed, eta) {
    progressBar.style.width = percent + "%";
    progressPercent.textContent = Math.round(percent) + "%";
    progressStatus.textContent = status;
    progressSpeed.textContent = speed;
    progressEta.textContent = "ETA: " + eta;
}

// ========================
//  SEPARAR
// ========================

const uploadZone = document.getElementById("upload-zone");
const fileInput = document.getElementById("file-input");
const fileSelected = document.getElementById("file-selected");
const selectedFilename = document.getElementById("selected-filename");
const selectedFilesize = document.getElementById("selected-filesize");
const removeFileBtn = document.getElementById("remove-file-btn");
const separateBtn = document.getElementById("separate-btn");
const sepUploadSection = document.getElementById("sep-upload-section");
const sepProgressSection = document.getElementById("sep-progress-section");
const sepProgressBar = document.getElementById("sep-progress-bar");
const sepProgressPercent = document.getElementById("sep-progress-percent");
const sepProgressStatus = document.getElementById("sep-progress-status");
const sepProgressMessage = document.getElementById("sep-progress-message");
const sepCompleteSection = document.getElementById("sep-complete-section");
const stemsGrid = document.getElementById("stems-grid");
const newSeparationBtn = document.getElementById("new-separation-btn");

let uploadedFile = null;
let selectedSepFormat = "1";
let selectedCategory = "";
let selectedSubCategory = "";
let categoriesData = {};

fetch("/api/categories")
    .then(r => r.json())
    .then(data => {
        categoriesData = data;
        renderCategoryGrid();
    })
    .catch(() => {
        setTimeout(() => {
            fetch("/api/categories").then(r => r.json()).then(data => {
                categoriesData = data;
                renderCategoryGrid();
            });
        }, 500);
    });

function renderCategoryGrid() {
    const grid = document.getElementById("category-grid");
    grid.innerHTML = "";

    for (const [key, cat] of Object.entries(categoriesData)) {
        const btn = document.createElement("button");
        btn.className = "category-card";
        if (cat.sub) btn.classList.add("category-card-group");
        btn.dataset.category = key;
        btn.innerHTML =
            '<div class="category-icon">' + cat.icon + '</div>' +
            '<div class="category-name">' + cat.name + '</div>' +
            '<div class="category-desc">' + (cat.short || "") + '</div>';
        grid.appendChild(btn);

        btn.addEventListener("click", () => {
            document.querySelectorAll(".category-card").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            selectedCategory = key;
            renderSubOptions(key, cat.sub);
            checkPromptVisibility();
        });
    }

    // Auto-select first category (Vocal) by default
    const firstCatKey = Object.keys(categoriesData)[0];
    if (firstCatKey) {
        const firstCard = grid.querySelector('.category-card');
        if (firstCard) {
            firstCard.classList.add("active");
            selectedCategory = firstCatKey;
            renderSubOptions(firstCatKey, categoriesData[firstCatKey].sub);
        }
    }
}

function checkPromptVisibility() {
    const promptBox = document.getElementById("text-prompt-container");
    if (!promptBox) return;
    if (selectedCategory === "voice_ai" || selectedCategory === "generate") {
        promptBox.classList.remove("hidden");
    } else {
        promptBox.classList.add("hidden");
    }
}

function renderSubOptions(categoryKey, subModels) {
    const container = document.getElementById("category-sub-container");
    container.innerHTML = "";

    if (!subModels) {
        container.classList.add("hidden");
        selectedSubCategory = null;
        const cat = categoriesData[categoryKey];
        document.getElementById("model-desc").textContent = (cat ? cat.name : categoryKey);
        checkPromptVisibility();
        return;
    }

    container.classList.remove("hidden");
    const subKeys = Object.keys(subModels);
    subKeys.forEach((subKey, idx) => {
        const sub = subModels[subKey];
        const btn = document.createElement("button");
        btn.className = "sub-option" + (idx === 0 ? " active" : "");
        btn.dataset.subCategory = subKey;
        btn.textContent = sub.name;
        container.appendChild(btn);

        btn.addEventListener("click", () => {
            container.querySelectorAll(".sub-option").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            selectedSubCategory = subKey;
            document.getElementById("model-desc").textContent = sub.name;
            checkPromptVisibility();
        });
    });

    selectedSubCategory = subKeys[0];
    document.getElementById("model-desc").textContent = subModels[subKeys[0]].name;
    checkPromptVisibility();
}

uploadZone.addEventListener("click", () => fileInput.click());
uploadZone.addEventListener("dragover", (e) => { e.preventDefault(); uploadZone.classList.add("dragover"); });
uploadZone.addEventListener("dragleave", () => { uploadZone.classList.remove("dragover"); });
uploadZone.addEventListener("drop", (e) => {
    e.preventDefault();
    uploadZone.classList.remove("dragover");
    if (e.dataTransfer.files.length > 0) uploadFile(e.dataTransfer.files[0]);
});
fileInput.addEventListener("change", () => { if (fileInput.files.length > 0) uploadFile(fileInput.files[0]); });

removeFileBtn.addEventListener("click", () => {
    uploadedFile = null;
    fileSelected.classList.add("hidden");
    uploadZone.classList.remove("hidden");
    separateBtn.disabled = true;
    fileInput.value = "";
});

document.querySelectorAll(".sep-fmt").forEach((btn) => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".sep-fmt").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        selectedSepFormat = btn.dataset.fmt;
    });
});

function uploadFile(file) {
    const allowed = [".mp3", ".flac", ".wav", ".m4a", ".ogg", ".aac", ".wma"];
    const ext = "." + file.name.split(".").pop().toLowerCase();
    if (!allowed.includes(ext)) { alert("Formato no soportado: " + ext); return; }
    selectedFilename.textContent = file.name;
    selectedFilesize.textContent = formatSize(file.size);
    const formData = new FormData();
    formData.append("file", file);
    separateBtn.disabled = true;
    separateBtn.querySelector("span").textContent = "Subiendo...";
    fetch("/upload_audio", { method: "POST", body: formData })
        .then((r) => r.json())
        .then((data) => {
            if (data.error) { alert(data.error); separateBtn.querySelector("span").textContent = "Separar"; return; }
            uploadedFile = data.filename;
            uploadZone.classList.add("hidden");
            fileSelected.classList.remove("hidden");
            separateBtn.disabled = false;
            separateBtn.querySelector("span").textContent = "Separar";
        })
        .catch(() => { alert("Error al subir el archivo"); separateBtn.querySelector("span").textContent = "Separar"; });
}

function formatSize(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

separateBtn.addEventListener("click", () => {
    if (!uploadedFile) return;
    if (!selectedCategory) return alert("Selecciona una categoria primero.");
    const promptInput = document.getElementById("text-prompt-input");
    const textPrompt = promptInput ? promptInput.value.trim() : "";

    sepUploadSection.classList.add("hidden");
    sepProgressSection.classList.remove("hidden");
    sepCompleteSection.classList.add("hidden");
    socket.emit("start_separation", {
        filename: uploadedFile,
        output_format: parseInt(selectedSepFormat),
        category: selectedCategory,
        sub_category: selectedSubCategory || undefined,
        text_prompt: textPrompt,
    });
});

socket.on("sep_started", () => updateSepProgress(0, "Iniciando...", "Preparando..."));
socket.on("sep_progress", (data) => updateSepProgress(data.percent, data.status, data.message));

socket.on("sep_complete", (data) => {
    destroyAllWavesurfers();
    sepProgressSection.classList.add("hidden");
    sepCompleteSection.classList.remove("hidden");
    document.getElementById("sep-complete-msg").textContent = data.stems.length + " archivos listos";
    renderStems(data.stems, data.original, data.folder);
});

socket.on("sep_error", (data) => {
    sepProgressSection.classList.add("hidden");
    sepUploadSection.classList.remove("hidden");
    alert(data.error);
});

newSeparationBtn.addEventListener("click", () => {
    destroyAllWavesurfers();
    sepCompleteSection.classList.add("hidden");
    sepProgressSection.classList.add("hidden");
    sepUploadSection.classList.remove("hidden");
    uploadedFile = null;
    fileSelected.classList.add("hidden");
    uploadZone.classList.remove("hidden");
    separateBtn.disabled = true;
    fileInput.value = "";
});

function updateSepProgress(percent, status, message) {
    sepProgressBar.style.width = percent + "%";
    sepProgressPercent.textContent = Math.round(percent) + "%";
    sepProgressStatus.textContent = status;
    sepProgressMessage.textContent = message;
}

// ========================
//  WAVEFORM PLAYER
// ========================

const stemIcons = {
    vocales: { emoji: "@", class: "vocals" },
    vocals: { emoji: "@", class: "vocals" },
    vocal: { emoji: "@", class: "vocals" },
    bajo: { emoji: "~", class: "bass" },
    bass: { emoji: "~", class: "bass" },
    bateria: { emoji: "o", class: "drums" },
    drums: { emoji: "o", class: "drums" },
    guitarra: { emoji: "#", class: "guitar" },
    guitar: { emoji: "#", class: "guitar" },
    piano: { emoji: "=", class: "piano" },
    teclados: { emoji: "=", class: "piano" },
    keys: { emoji: "=", class: "piano" },
    otro: { emoji: "-", class: "other" },
    other: { emoji: "-", class: "other" },
    instrumental: { emoji: "$", class: "instrumental" },
    sintetizador: { emoji: "%", class: "synth" },
    synth: { emoji: "%", class: "synth" },
    vientos: { emoji: "&", class: "wind" },
    wind: { emoji: "&", class: "wind" },
    percusion: { emoji: "+", class: "percussion" },
    percussion: { emoji: "+", class: "percussion" },
    fx: { emoji: "*", class: "fx" },
    efectos: { emoji: "*", class: "fx" },
    sinreverb: { emoji: "/", class: "reverb" },
    noreverb: { emoji: "/", class: "reverb" },
    sinruido: { emoji: "^", class: "denoise" },
    denoise: { emoji: "^", class: "denoise" },
    original: { emoji: "?", class: "original" },
    instrum: { emoji: "$", class: "instrumental" },
    instrumental: { emoji: "$", class: "instrumental" },
    kick: { emoji: "!", class: "drums" },
    snare: { emoji: "!", class: "drums" },
    hihat: { emoji: "!", class: "drums" },
    ride: { emoji: "!", class: "drums" },
    crash: { emoji: "}", class: "other" },
    toms: { emoji: "!", class: "drums" },
    vocaleslead: { emoji: "@", class: "vocals" },
    vocalesback: { emoji: "@", class: "vocals" },
    coros: { emoji: "@", class: "vocals" },
    publico: { emoji: "{", class: "other" },
    crowd: { emoji: "{", class: "other" },
    speech: { emoji: "<", class: "other" },
    music: { emoji: "-", class: "other" },
    effects: { emoji: "*", class: "fx" },
    discurso: { emoji: "<", class: "other" },
    musica: { emoji: "-", class: "other" },
    violin: { emoji: "V", class: "guitar" },
    viola: { emoji: "V", class: "guitar" },
    violonchelo: { emoji: "V", class: "guitar" },
    contrabajo: { emoji: "V", class: "guitar" },
    cuerdas: { emoji: "V", class: "guitar" },
    cuerdaspulsadas: { emoji: "V", class: "guitar" },
    arpa: { emoji: "A", class: "other" },
    mandolina: { emoji: "M", class: "guitar" },
    banjo: { emoji: "B", class: "guitar" },
    sitar: { emoji: "S", class: "other" },
    ukelele: { emoji: "U", class: "guitar" },
    dobro: { emoji: "D", class: "guitar" },
    saxofon: { emoji: "&", class: "wind" },
    flauta: { emoji: "&", class: "wind" },
    trompeta: { emoji: "&", class: "wind" },
    trombon: { emoji: "&", class: "wind" },
    oboe: { emoji: "&", class: "wind" },
    clarinete: { emoji: "&", class: "wind" },
    cornofrances: { emoji: "&", class: "wind" },
    armonica: { emoji: "&", class: "wind" },
    tuba: { emoji: "&", class: "wind" },
    fagot: { emoji: "&", class: "wind" },
    gaita: { emoji: "&", class: "wind" },
    silbato: { emoji: "&", class: "wind" },
    maderas: { emoji: "&", class: "wind" },
    latones: { emoji: "&", class: "wind" },
    piano_digital: { emoji: "=", class: "piano" },
    organo: { emoji: "=", class: "piano" },
    clavicordio: { emoji: "=", class: "piano" },
    acordeon: { emoji: "=", class: "piano" },
    vibrafono: { emoji: "+", class: "percussion" },
    rhodes: { emoji: "=", class: "piano" },
    campanasmetalicas: { emoji: "+", class: "percussion" },
    guitarraacustica: { emoji: "#", class: "guitar" },
    guitarraelectrica: { emoji: "#", class: "guitar" },
    lead: { emoji: "#", class: "guitar" },
    ritmica: { emoji: "#", class: "guitar" },
    pedalsteel: { emoji: "#", class: "guitar" },
    pandereta: { emoji: "+", class: "percussion" },
    marimba: { emoji: "+", class: "percussion" },
    glockenspiel: { emoji: "+", class: "percussion" },
    timpani: { emoji: "+", class: "percussion" },
    triangulo: { emoji: "+", class: "percussion" },
    congas: { emoji: "+", class: "percussion" },
    campanas: { emoji: "+", class: "percussion" },
    xilofono: { emoji: "+", class: "percussion" },
    celesta: { emoji: "+", class: "percussion" },
    cencerro: { emoji: "+", class: "percussion" },
    soprano: { emoji: "@", class: "vocals" },
    alto: { emoji: "@", class: "vocals" },
    tenor: { emoji: "@", class: "vocals" },
    coro: { emoji: "@", class: "vocals" },
    vozfemenina: { emoji: "@", class: "vocals" },
    vozmasculina: { emoji: "@", class: "vocals" },
    bpmstart: { emoji: "%", class: "synth" },
};

function createWaveformPlayer(container, audioUrl, label, isOriginal, downloadName) {
    const playerEl = document.createElement("div");
    playerEl.className = "waveform-player" + (isOriginal ? " waveform-original" : "");
    playerEl.innerHTML =
        '<div class="waveform-header">' +
            '<span class="waveform-label">' + (isOriginal ? "\uD83C\uDFB6 " + label : label) + '</span>' +
            '<span class="waveform-time">0:00</span>' +
        '</div>' +
        '<div class="waveform-canvas"></div>' +
        '<div class="waveform-controls">' +
            '<button class="wf-play-btn" title="Play/Pause">' +
                '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>' +
            '</button>' +
            '<input type="range" class="wf-volume" min="0" max="1" step="0.05" value="1" title="Volumen">' +
            (isOriginal ? '' :
                '<a href="#" class="wf-download" title="Descargar">' +
                    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
                        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>' +
                        '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>' +
                    '</svg>' +
                '</a>') +
        '</div>';
    container.appendChild(playerEl);

    const ws = WaveSurfer.create({
        container: playerEl.querySelector(".waveform-canvas"),
        waveColor: "rgba(255,255,255,0.2)",
        progressColor: "#ff4444",
        cursorColor: "#ff6666",
        barWidth: 2,
        barGap: 1,
        barRadius: 2,
        height: 56,
        responsive: true,
        normalize: true,
        backend: "WebAudio",
    });

    ws.load(audioUrl);
    activeWavesurfers.push(ws);

    const playBtn = playerEl.querySelector(".wf-play-btn");
    const timeEl = playerEl.querySelector(".waveform-time");
    const volumeEl = playerEl.querySelector(".wf-volume");

    ws.on("play", () => {
        playBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="3" width="4" height="18"/><rect x="15" y="3" width="4" height="18"/></svg>';
    });
    ws.on("pause", () => {
        playBtn.innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><polygon points="5,3 19,12 5,21"/></svg>';
    });
    ws.on("audioprocess", () => {
        timeEl.textContent = formatTime(ws.getCurrentTime());
    });
    ws.on("seeking", () => {
        timeEl.textContent = formatTime(ws.getCurrentTime());
    });
    ws.on("ready", () => {
        timeEl.textContent = formatTime(ws.getDuration());
    });

    playBtn.addEventListener("click", (e) => {
        e.preventDefault();
        activeWavesurfers.forEach(other => {
            if (other !== ws && other.isPlaying()) other.pause();
        });
        ws.playPause();
    });

    volumeEl.addEventListener("input", () => {
        ws.setVolume(parseFloat(volumeEl.value));
    });

    if (downloadName) {
        const dlBtn = playerEl.querySelector(".wf-download");
        if (dlBtn) {
            dlBtn.addEventListener("click", (e) => {
                e.preventDefault();
                forceDownload(audioUrl, downloadName);
            });
        }
    }

    return ws;
}

function formatTime(seconds) {
    if (!seconds || isNaN(seconds)) return "0:00";
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return m + ":" + (s < 10 ? "0" : "") + s;
}

function renderStems(stems, originalFile, folder) {
    stemsGrid.innerHTML = "";
    destroyAllWavesurfers();

    stems.forEach((stem) => {
        const key = stem.label.toLowerCase().replace(/\s+/g, "");
        const icon = stemIcons[key] || { emoji: "\uD83C\uDFB5", class: "other" };
        const stemUrl = "/download_stem/" + encodeURIComponent(stem.filename);
        const stemFileName = stem.label + "." + stem.ext;

        const card = document.createElement("div");
        card.className = "stem-card";
        card.innerHTML =
            '<div class="stem-card-top">' +
                '<div class="stem-icon ' + icon.class + '">' + icon.emoji + '</div>' +
                '<div class="stem-info">' +
                    '<div class="stem-label">' + stem.label + '</div>' +
                    '<div class="stem-ext">.' + stem.ext + '</div>' +
                '</div>' +
                '<a href="#" class="stem-download" title="Descargar">' +
                    '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
                        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>' +
                        '<polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>' +
                    '</svg>' +
                '</a>' +
            '</div>';
        stemsGrid.appendChild(card);

        createWaveformPlayer(card, stemUrl, stem.label, false, stemFileName);

        card.querySelector(".stem-download").addEventListener("click", function(e) {
            e.preventDefault();
            forceDownload(stemUrl, stemFileName);
        });
    });
}

// ========================
//  AJUSTES (SETTINGS)
// ========================

const settingsModal = document.getElementById("settings-modal");
const settingsBtn = document.getElementById("settings-btn");
const settingsCloseBtn = document.getElementById("settings-close-btn");
const settingsCodigo = document.getElementById("settings-codigo");
const settingsStatus = document.getElementById("settings-status");
const settingsSaveBtn = document.getElementById("settings-save-btn");
const settingsDeleteBtn = document.getElementById("settings-delete-btn");

function showSettingsStatus(msg, isError) {
    settingsStatus.textContent = msg;
    settingsStatus.className = "settings-status " + (isError ? "error" : "success");
}

function openSettings() {
    settingsModal.classList.remove("hidden");
    settingsStatus.className = "settings-status hidden";
    settingsStatus.textContent = "";
    fetch("/api/settings")
        .then((r) => r.json())
        .then((data) => {
            settingsCodigo.value = "";
            settingsCodigo.placeholder = data.has_key
                ? "Codigo guardado: " + data.key
                : "Pega tu codigo aqui...";
            settingsDeleteBtn.style.display = data.has_key ? "" : "none";
            settingsCodigo.focus();
        })
        .catch(() => {
            settingsCodigo.placeholder = "Pega tu codigo aqui...";
            settingsDeleteBtn.style.display = "none";
        });
}

function closeSettings() {
    settingsModal.classList.add("hidden");
}

settingsBtn.addEventListener("click", openSettings);
settingsCloseBtn.addEventListener("click", closeSettings);

settingsModal.addEventListener("click", (e) => {
    if (e.target === settingsModal) closeSettings();
});

document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !settingsModal.classList.contains("hidden")) {
        closeSettings();
    }
});

settingsSaveBtn.addEventListener("click", () => {
    const codigo = settingsCodigo.value.trim();
    if (!codigo) {
        showSettingsStatus("Ingresa tu codigo de BPMStartPRO.", true);
        return;
    }
    settingsSaveBtn.disabled = true;
    fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ codigo: codigo }),
    })
        .then((r) => r.json())
        .then((data) => {
            if (data.success) {
                showSettingsStatus("Codigo guardado correctamente.");
                settingsCodigo.value = "";
                settingsCodigo.placeholder = "Codigo guardado: " + "*".repeat(codigo.length);
                settingsDeleteBtn.style.display = "";
            } else {
                showSettingsStatus(data.error || "Error al guardar el codigo.", true);
            }
        })
        .catch(() => showSettingsStatus("Error de conexion al guardar.", true))
        .finally(() => { settingsSaveBtn.disabled = false; });
});

settingsDeleteBtn.addEventListener("click", () => {
    if (!confirm("Quieres quitar tu codigo de BPMStartPRO de este equipo?")) return;
    settingsDeleteBtn.disabled = true;
    fetch("/api/settings/delete", { method: "POST" })
        .then((r) => r.json())
        .then((data) => {
            if (data.success) {
                showSettingsStatus("Codigo eliminado.");
                settingsCodigo.value = "";
                settingsCodigo.placeholder = "Pega tu codigo aqui...";
                settingsDeleteBtn.style.display = "none";
            } else {
                showSettingsStatus("Error al eliminar el codigo.", true);
            }
        })
        .catch(() => showSettingsStatus("Error de conexion al eliminar.", true))
        .finally(() => { settingsDeleteBtn.disabled = false; });
});
