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

// Las tres opciones de calidad significan cosas distintas segun el formato:
// resolucion para video, bitrate para audio. Se renombran al cambiar de formato
// para que el usuario vea exactamente que va a descargar.
const QUALITY_LABELS = {
    mp4: {
        best:   { icon: "2K",    label: "2K",    sub: "1440p" },
        medium: { icon: "1080p", label: "1080p", sub: "Full HD" },
        low:    { icon: "720p",  label: "720p",  sub: "HD" },
    },
    mp3: {
        best:   { icon: "320K",  label: "Alta",  sub: "320 kbps" },
        medium: { icon: "192K",  label: "Media", sub: "192 kbps" },
        low:    { icon: "128K",  label: "Baja",  sub: "128 kbps" },
    },
};

function updateQualityLabels() {
    const set = QUALITY_LABELS[selectedFormat] || QUALITY_LABELS.mp3;
    document.querySelectorAll(".quality-btn").forEach((btn) => {
        const info = set[btn.dataset.quality];
        if (!info) return;
        const icon = btn.querySelector(".q-icon");
        const label = btn.querySelector(".q-label");
        const sub = btn.querySelector(".q-sub");
        if (icon) icon.textContent = info.icon;
        if (label) label.textContent = info.label;
        if (sub) sub.textContent = info.sub;
    });
}

document.querySelectorAll(".format-btn[data-format]").forEach((btn) => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".format-btn[data-format]").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        selectedFormat = btn.dataset.format;
        updateQualityLabels();
    });
});

updateQualityLabels();

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
    updateQualityLabels();
    document.querySelectorAll(".format-btn[data-format]").forEach((b) => {
        b.classList.toggle("active", b.dataset.format === selectedFormat);
    });
    document.querySelectorAll(".quality-btn").forEach((b) => {
        b.classList.toggle("active", b.dataset.quality === selectedQuality);
    });
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
    // Si YouTube no permitio la resolucion pedida, avisar en vez de entregar
    // un video de baja calidad en silencio.
    if (data.quality_warning) {
        showStatus(data.quality_warning, "warning");
    }
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
            checkMidiFormatVisibility();
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
            checkMidiFormatVisibility();
        }
    }
}

function checkMidiFormatVisibility() {
    const midiBtn = document.getElementById("midi-fmt-btn");
    if (!midiBtn) return;
    if (selectedCategory === "midi") {
        midiBtn.classList.remove("hidden");
        // Auto select MIDI format button
        document.querySelectorAll(".sep-fmt").forEach((b) => b.classList.remove("active"));
        midiBtn.classList.add("active");
        selectedSepFormat = "midi";
    } else {
        midiBtn.classList.add("hidden");
        if (selectedSepFormat === "midi") {
            const defaultWavBtn = document.querySelector('.sep-fmt[data-fmt="1"]');
            if (defaultWavBtn) {
                document.querySelectorAll(".sep-fmt").forEach((b) => b.classList.remove("active"));
                defaultWavBtn.classList.add("active");
                selectedSepFormat = "1";
            }
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
    const outFmt = (selectedSepFormat === "midi" || isNaN(parseInt(selectedSepFormat))) ? 1 : parseInt(selectedSepFormat);
    socket.emit("start_separation", {
        filename: uploadedFile,
        output_format: outFmt,
        category: selectedCategory,
        sub_category: selectedSubCategory || undefined,
        text_prompt: textPrompt,
    });
});

// Separation state tracking
let currentSepId = null;
let currentTaskHash = null;
let soundEnabled = localStorage.getItem("bpm_sep_sound") === "true";
let countdownInterval = null;
let countdownRemainingSeconds = 0;

function playCompletionSound() {
    if (!soundEnabled) return;
    try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return;
        const ctx = new AudioCtx();
        const now = ctx.currentTime;
        const notes = [
            { f: 587.33, t: 0, d: 0.25 },     // D5
            { f: 880.00, t: 0.15, d: 0.35 },    // A5
            { f: 1174.66, t: 0.32, d: 0.6 }     // D6
        ];
        notes.forEach(n => {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.type = "sine";
            osc.frequency.setValueAtTime(n.f, now + n.t);
            gain.gain.setValueAtTime(0.001, now + n.t);
            gain.gain.exponentialRampToValueAtTime(0.25, now + n.t + 0.03);
            gain.gain.exponentialRampToValueAtTime(0.0001, now + n.t + n.d);
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.start(now + n.t);
            osc.stop(now + n.t + n.d + 0.05);
        });
    } catch (e) {
        console.warn("Chime error:", e);
    }
}

// Sound toggle button initialization
const soundBtn = document.getElementById("btn-sound-toggle");
if (soundBtn) {
    if (soundEnabled) soundBtn.classList.add("active");
    soundBtn.addEventListener("click", () => {
        soundEnabled = !soundEnabled;
        localStorage.setItem("bpm_sep_sound", soundEnabled ? "true" : "false");
        soundBtn.classList.toggle("active", soundEnabled);
        if (soundEnabled) {
            playCompletionSound();
        }
    });
}

// Cancel separation button handler
const cancelSepBtn = document.getElementById("btn-cancel-sep");
if (cancelSepBtn) {
    cancelSepBtn.addEventListener("click", () => {
        if (confirm("¿Estás seguro de cancelar este trabajo de separación?")) {
            socket.emit("cancel_separation", {
                sep_id: currentSepId,
                task_hash: currentTaskHash
            });
            stopCountdown();
            sepProgressSection.classList.add("hidden");
            sepUploadSection.classList.remove("hidden");
        }
    });
}

function startCountdown(seconds) {
    stopCountdown();
    countdownRemainingSeconds = Math.max(0, parseInt(seconds, 10) || 0);
    renderCountdown();
    countdownInterval = setInterval(() => {
        if (countdownRemainingSeconds > 0) {
            countdownRemainingSeconds--;
            renderCountdown();
        } else {
            stopCountdown();
        }
    }, 1000);
}

function stopCountdown() {
    if (countdownInterval) {
        clearInterval(countdownInterval);
        countdownInterval = null;
    }
}

function renderCountdown() {
    const timerEl = document.getElementById("bpm-countdown");
    if (!timerEl) return;
    const m = Math.floor(countdownRemainingSeconds / 60);
    const s = countdownRemainingSeconds % 60;
    timerEl.textContent = `${m}:${s < 10 ? '0' : ''}${s}`;
}

function setStage(stageName) {
    const card = document.querySelector(".bpm-stage-card");
    if (!card) return;
    card.classList.remove("stage-preparar", "stage-fila", "stage-separar", "stage-unir");
    card.classList.add(`stage-${stageName}`);

    const steps = ["preparar", "fila", "separar", "unir"];
    const curIdx = steps.indexOf(stageName);

    steps.forEach((name, idx) => {
        const stepEl = document.getElementById(`step-${name}`);
        const panelEl = document.getElementById(`phase-${name}`);
        if (stepEl) {
            stepEl.classList.remove("active", "completed");
            if (idx < curIdx) stepEl.classList.add("completed");
            else if (idx === curIdx) stepEl.classList.add("active");
        }
        if (panelEl) {
            if (idx === curIdx) panelEl.classList.remove("hidden");
            else panelEl.classList.add("hidden");
        }
    });
}

socket.on("sep_started", (data) => {
    currentSepId = data ? data.sep_id : null;
    currentTaskHash = null;
    updateSepProgress(0, "Iniciando...", "Preparando audio...", { stage: "preparar", sep_id: currentSepId });
});

socket.on("sep_progress", (data) => {
    updateSepProgress(data.percent, data.status, data.message, data);
});

socket.on("sep_cancelled", (data) => {
    stopCountdown();
    sepProgressSection.classList.add("hidden");
    sepUploadSection.classList.remove("hidden");
});

socket.on("sep_complete", (data) => {
    stopCountdown();
    destroyAllWavesurfers();
    sepProgressSection.classList.add("hidden");
    sepCompleteSection.classList.remove("hidden");
    document.getElementById("sep-complete-msg").textContent = data.stems.length + " archivos listos";
    renderStems(data.stems, data.original, data.folder);
    playCompletionSound();
});

socket.on("sep_error", (data) => {
    stopCountdown();
    sepProgressSection.classList.add("hidden");
    sepUploadSection.classList.remove("hidden");
    alert(data.error);
});

newSeparationBtn.addEventListener("click", () => {
    stopCountdown();
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

function updateSepProgress(percent, status, message, extra = {}) {
    if (sepProgressBar) sepProgressBar.style.width = percent + "%";
    if (sepProgressPercent) sepProgressPercent.textContent = Math.round(percent) + "%";
    if (sepProgressStatus) sepProgressStatus.textContent = status;
    if (sepProgressMessage) sepProgressMessage.textContent = message;

    if (extra.task_hash) currentTaskHash = extra.task_hash;
    if (extra.sep_id) currentSepId = extra.sep_id;

    // Detect stage
    let stage = extra.stage;
    if (!stage) {
        const s = (status || "").toLowerCase();
        if (s.includes("sub") || s.includes("prep")) stage = "preparar";
        else if (s.includes("cola") || s.includes("wait")) stage = "fila";
        else if (s.includes("sep") || s.includes("proc") || s.includes("dist")) stage = "separar";
        else if (s.includes("un") || s.includes("desc") || s.includes("fus")) stage = "unir";
        else stage = percent < 25 ? "preparar" : percent < 50 ? "fila" : percent < 80 ? "separar" : "unir";
    }

    setStage(stage);

    if (stage === "fila") {
        if (extra.wait_seconds !== undefined && extra.wait_seconds !== null) {
            startCountdown(extra.wait_seconds);
        } else if (extra.wait_time) {
            const parts = extra.wait_time.split(":");
            if (parts.length === 2) {
                const totalSec = parseInt(parts[0], 10) * 60 + parseInt(parts[1], 10);
                startCountdown(totalSec);
            }
        }
        const orderEl = document.getElementById("bpm-queue-order");
        if (orderEl) {
            const order = extra.queue_order !== undefined ? extra.queue_order : 0;
            const total = extra.queue_total !== undefined ? extra.queue_total : 1;
            orderEl.textContent = `Número ${order} de ${total} en la cola`;
        }
    } else {
        stopCountdown();
    }

    if (stage === "preparar") {
        const det = document.getElementById("sep-preparar-detail");
        if (det) det.textContent = message || "Subiendo archivo a servidores de BPMStart Pro...";
    } else if (stage === "separar") {
        const det = document.getElementById("sep-separar-detail");
        if (det) det.textContent = message || "Separando tu audio ahora mismo con BPMStart Pro...";
    } else if (stage === "unir") {
        const det = document.getElementById("sep-unir-detail");
        if (det) det.textContent = message || "Uniendo y ensamblando pistas separadas...";
    }
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
        // Si el servidor responde con un error que no es JSON (una traza HTML,
        // por ejemplo), no hay que tratarlo como fallo de conexion: eso hacia
        // que un problema al escribir el archivo se mostrara como
        // "Error de conexion" y despistara por completo.
        .then(async (r) => {
            const texto = await r.text();
            try {
                return JSON.parse(texto);
            } catch {
                return {
                    success: false,
                    error: r.ok
                        ? "Respuesta inesperada del programa al guardar."
                        : "El programa no pudo guardar el codigo (error " + r.status + "). " +
                          "Revisa que el antivirus no lo este bloqueando.",
                };
            }
        })
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
        .catch(() => showSettingsStatus("No se pudo contactar con el programa. Cierralo y vuelve a abrirlo.", true))
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

// ========================
//  MIDI PRO CONTROLLER
// ========================

let midiSourceType = "yt";
let midiUploadedFile = null;
let midiSelectedMode = "multitrack_7";
let midiCurrentResults = null;
let midiActiveTrackFilter = "all";

const midiSourceYtBtn = document.getElementById("midi-source-yt-btn");
const midiSourceFileBtn = document.getElementById("midi-source-file-btn");
const midiYtArea = document.getElementById("midi-yt-area");
const midiFileArea = document.getElementById("midi-file-area");
const midiUrlInput = document.getElementById("midi-url-input");
const midiPasteBtn = document.getElementById("midi-paste-btn");
const midiUploadZone = document.getElementById("midi-upload-zone");
const midiFileInput = document.getElementById("midi-file-input");
const midiFileSelected = document.getElementById("midi-file-selected");
const midiSelectedFilename = document.getElementById("midi-selected-filename");
const midiSelectedFilesize = document.getElementById("midi-selected-filesize");
const midiRemoveFileBtn = document.getElementById("midi-remove-file-btn");

const midiModeCards = document.querySelectorAll(".midi-mode-card");
const midiCustomBpmInput = document.getElementById("midi-custom-bpm");
const midiBpmAutoBtn = document.getElementById("midi-bpm-auto-btn");
const midiBpmHalfBtn = document.getElementById("midi-bpm-half-btn");
const midiBpmDoubleBtn = document.getElementById("midi-bpm-double-btn");
const bpmModeBadge = document.getElementById("bpm-mode-badge");
const startMidiBtn = document.getElementById("start-midi-btn");

const midiInputSection = document.getElementById("midi-input-section");
const midiProgressSection = document.getElementById("midi-progress-section");
const midiProgressStatus = document.getElementById("midi-progress-status");
const midiProgressPercent = document.getElementById("midi-progress-percent");
const midiProgressBar = document.getElementById("midi-progress-bar");
const midiProgressMessage = document.getElementById("midi-progress-message");

const midiCompleteSection = document.getElementById("midi-complete-section");
const midiSongTitle = document.getElementById("midi-song-title");
const midiBpmDisplay = document.getElementById("midi-bpm-display");
const midiNotesDisplay = document.getElementById("midi-notes-display");
const midiDawDraggable = document.getElementById("midi-daw-draggable");
const midiDownloadMasterBtn = document.getElementById("midi-download-master-btn");
const midiDownloadZipBtn = document.getElementById("midi-download-zip-btn");
const midiOpenFolderBtn = document.getElementById("midi-open-folder-btn");
const newMidiBtn = document.getElementById("new-midi-btn");

const prTrackSelector = document.getElementById("pr-track-selector");
const pianoRollCanvas = document.getElementById("piano-roll-canvas");
const prNoteStats = document.getElementById("pr-note-stats");
const midiTracksGrid = document.getElementById("midi-tracks-grid");

// Switch source type
if (midiSourceYtBtn && midiSourceFileBtn) {
    midiSourceYtBtn.addEventListener("click", () => {
        midiSourceType = "yt";
        midiSourceYtBtn.classList.add("active");
        midiSourceFileBtn.classList.remove("active");
        midiYtArea.classList.remove("hidden");
        midiFileArea.classList.add("hidden");
    });

    midiSourceFileBtn.addEventListener("click", () => {
        midiSourceType = "file";
        midiSourceFileBtn.classList.add("active");
        midiSourceYtBtn.classList.remove("active");
        midiFileArea.classList.remove("hidden");
        midiYtArea.classList.add("hidden");
    });
}

// Paste button
if (midiPasteBtn) {
    midiPasteBtn.addEventListener("click", async () => {
        try {
            const text = await navigator.clipboard.readText();
            midiUrlInput.value = text.trim();
            midiUrlInput.focus();
        } catch (e) {
            midiUrlInput.focus();
        }
    });
}

// File drop/upload
if (midiUploadZone && midiFileInput) {
    midiUploadZone.addEventListener("click", () => midiFileInput.click());
    midiUploadZone.addEventListener("dragover", (e) => { e.preventDefault(); midiUploadZone.classList.add("dragover"); });
    midiUploadZone.addEventListener("dragleave", () => midiUploadZone.classList.remove("dragover"));
    midiUploadZone.addEventListener("drop", (e) => {
        e.preventDefault();
        midiUploadZone.classList.remove("dragover");
        if (e.dataTransfer.files.length) handleMidiFileUpload(e.dataTransfer.files[0]);
    });
    midiFileInput.addEventListener("change", (e) => {
        if (e.target.files.length) handleMidiFileUpload(e.target.files[0]);
    });
}

function handleMidiFileUpload(file) {
    const formData = new FormData();
    formData.append("file", file);

    midiUploadZone.classList.add("hidden");
    midiFileSelected.classList.remove("hidden");
    midiSelectedFilename.textContent = "Subiendo " + file.name + "...";
    midiSelectedFilesize.textContent = (file.size / (1024 * 1024)).toFixed(1) + " MB";

    fetch("/upload_audio", { method: "POST", body: formData })
        .then((r) => r.json())
        .then((data) => {
            if (data.error) {
                alert(data.error);
                removeMidiFile();
                return;
            }
            midiUploadedFile = data.filename;
            midiSelectedFilename.textContent = file.name;
        })
        .catch(() => {
            alert("Error al subir archivo.");
            removeMidiFile();
        });
}

function removeMidiFile() {
    midiUploadedFile = null;
    midiFileInput.value = "";
    midiFileSelected.classList.add("hidden");
    midiUploadZone.classList.remove("hidden");
}

if (midiRemoveFileBtn) {
    midiRemoveFileBtn.addEventListener("click", removeMidiFile);
}

// Mode selector
midiModeCards.forEach((card) => {
    card.addEventListener("click", () => {
        midiModeCards.forEach((c) => c.classList.remove("active"));
        card.classList.add("active");
        midiSelectedMode = card.dataset.mode;
    });
});

// BPM modifiers
if (midiCustomBpmInput) {
    midiCustomBpmInput.addEventListener("input", () => {
        if (midiCustomBpmInput.value) {
            bpmModeBadge.textContent = "Manual: " + midiCustomBpmInput.value + " BPM";
            bpmModeBadge.className = "badge badge-notes";
        } else {
            bpmModeBadge.textContent = "Auto (Detectar con IA)";
            bpmModeBadge.className = "badge badge-auto";
        }
    });
}

if (midiBpmAutoBtn) {
    midiBpmAutoBtn.addEventListener("click", () => {
        midiCustomBpmInput.value = "";
        bpmModeBadge.textContent = "Auto (Detectar con IA)";
        bpmModeBadge.className = "badge badge-auto";
    });
}

if (midiBpmHalfBtn) {
    midiBpmHalfBtn.addEventListener("click", () => {
        const cur = parseFloat(midiCustomBpmInput.value) || 120.0;
        midiCustomBpmInput.value = (cur / 2).toFixed(2);
        bpmModeBadge.textContent = "Manual: " + midiCustomBpmInput.value + " BPM";
        bpmModeBadge.className = "badge badge-notes";
    });
}

if (midiBpmDoubleBtn) {
    midiBpmDoubleBtn.addEventListener("click", () => {
        const cur = parseFloat(midiCustomBpmInput.value) || 120.0;
        midiCustomBpmInput.value = (cur * 2).toFixed(2);
        bpmModeBadge.textContent = "Manual: " + midiCustomBpmInput.value + " BPM";
        bpmModeBadge.className = "badge badge-notes";
    });
}

// Start button
if (startMidiBtn) {
    startMidiBtn.addEventListener("click", () => {
        const ytUrl = (midiUrlInput.value || "").trim();
        const customBpm = midiCustomBpmInput.value ? parseFloat(midiCustomBpmInput.value) : null;

        if (midiSourceType === "yt" && !ytUrl) {
            alert("Por favor ingresa un enlace de YouTube.");
            midiUrlInput.focus();
            return;
        }

        if (midiSourceType === "file" && !midiUploadedFile) {
            alert("Por favor selecciona o sube un archivo de audio.");
            return;
        }

        midiInputSection.classList.add("hidden");
        midiProgressSection.classList.remove("hidden");
        midiCompleteSection.classList.add("hidden");

        updateMidiProgress(5, "Iniciando...", "Preparando tarea de transcripcion MIDI...");

        socket.emit("start_midi_pipeline", {
            url: midiSourceType === "yt" ? ytUrl : null,
            filename: midiSourceType === "file" ? midiUploadedFile : null,
            mode: midiSelectedMode,
            custom_bpm: customBpm
        });
    });
}

function updateMidiProgress(percent, status, message) {
    midiProgressBar.style.width = percent + "%";
    midiProgressPercent.textContent = Math.round(percent) + "%";
    midiProgressStatus.textContent = status;
    midiProgressMessage.textContent = message;

    const step1 = document.getElementById("p-step-1");
    const step2 = document.getElementById("p-step-2");
    const step3 = document.getElementById("p-step-3");
    const step4 = document.getElementById("p-step-4");
    const step5 = document.getElementById("p-step-5");

    [step1, step2, step3, step4, step5].forEach(s => s && (s.className = "p-step"));

    if (percent >= 8 && percent < 18) { if (step1) step1.className = "p-step active"; }
    else if (percent >= 18 && percent < 25) { if (step1) step1.className = "p-step done"; if (step2) step2.className = "p-step active"; }
    else if (percent >= 25 && percent < 75) { if (step1) step1.className = "p-step done"; if (step2) step2.className = "p-step done"; if (step3) step3.className = "p-step active"; }
    else if (percent >= 75 && percent < 92) { if (step1) step1.className = "p-step done"; if (step2) step2.className = "p-step done"; if (step3) step3.className = "p-step done"; if (step4) step4.className = "p-step active"; }
    else if (percent >= 92) { if (step1) step1.className = "p-step done"; if (step2) step2.className = "p-step done"; if (step3) step3.className = "p-step done"; if (step4) step4.className = "p-step done"; if (step5) step5.className = "p-step active"; }
}

socket.on("midi_progress", (data) => {
    updateMidiProgress(data.percent || 0, data.status || "Procesando", data.message || "");
});

socket.on("midi_error", (data) => {
    midiProgressSection.classList.add("hidden");
    midiInputSection.classList.remove("hidden");
    alert("Error en proceso MIDI: " + (data.error || "Ocurrio un error"));
});

socket.on("midi_complete", (data) => {
    midiCurrentResults = data;
    destroyAllWavesurfers();

    midiProgressSection.classList.add("hidden");
    midiCompleteSection.classList.remove("hidden");

    midiSongTitle.textContent = data.song_title;
    midiBpmDisplay.textContent = "BPM: " + parseFloat(data.bpm).toFixed(2);
    midiNotesDisplay.textContent = (data.total_notes || 0) + " notas detectadas";

    midiDownloadMasterBtn.href = data.master_midi_url;
    midiDownloadMasterBtn.onclick = (e) => {
        e.preventDefault();
        forceDownload(data.master_midi_url, data.master_midi_name);
    };

    midiDownloadZipBtn.href = data.zip_url;
    midiDownloadZipBtn.onclick = (e) => {
        e.preventDefault();
        forceDownload(data.zip_url, data.zip_name);
    };

    midiDawDraggable.href = data.master_midi_url;
    midiDawDraggable.setAttribute("download", data.master_midi_name);
    midiDawDraggable.onclick = (e) => {
        e.preventDefault();
        forceDownload(data.master_midi_url, data.master_midi_name);
    };

    midiDawDraggable.ondragstart = (e) => {
        const fullUrl = window.location.origin + data.master_midi_url;
        e.dataTransfer.setData("DownloadURL", `audio/midi:${data.master_midi_name}:${fullUrl}`);
        e.dataTransfer.setData("text/uri-list", fullUrl);
        e.dataTransfer.setData("text/plain", fullUrl);
    };

    if (midiOpenFolderBtn) {
        midiOpenFolderBtn.onclick = () => {
            if (window.pywebview && window.pywebview.api && window.pywebview.api.open_folder) {
                window.pywebview.api.open_folder(data.master_midi_url);
            } else {
                fetch("/api/open_folder", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ path: data.folder + "/" + data.master_midi_name })
                }).catch(() => {});
            }
        };
    }

    renderPianoRollControls(data.tracks);
    drawPianoRoll(data.tracks, "all");
    renderMidiTracksList(data.tracks, data.song_title);
});

if (newMidiBtn) {
    newMidiBtn.addEventListener("click", () => {
        destroyAllWavesurfers();
        midiCompleteSection.classList.add("hidden");
        midiProgressSection.classList.add("hidden");
        midiInputSection.classList.remove("hidden");
    });
}

function renderPianoRollControls(tracks) {
    prTrackSelector.innerHTML = "";
    
    const allPill = document.createElement("button");
    allPill.className = "pr-track-pill active";
    allPill.textContent = "🌟 Todos";
    allPill.onclick = () => {
        document.querySelectorAll(".pr-track-pill").forEach(p => p.classList.remove("active"));
        allPill.classList.add("active");
        midiActiveTrackFilter = "all";
        drawPianoRoll(tracks, "all");
    };
    prTrackSelector.appendChild(allPill);

    tracks.forEach((track) => {
        const pill = document.createElement("button");
        pill.className = "pr-track-pill";
        pill.textContent = `${track.icon} ${track.name}`;
        pill.style.borderColor = track.color;
        pill.onclick = () => {
            document.querySelectorAll(".pr-track-pill").forEach(p => p.classList.remove("active"));
            pill.classList.add("active");
            midiActiveTrackFilter = track.name;
            drawPianoRoll(tracks, track.name);
        };
        prTrackSelector.appendChild(pill);
    });
}

function drawPianoRoll(tracks, filter) {
    if (!pianoRollCanvas) return;
    const ctx = pianoRollCanvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const width = pianoRollCanvas.parentElement.clientWidth || 800;
    const height = 220;

    pianoRollCanvas.width = width * dpr;
    pianoRollCanvas.height = height * dpr;
    pianoRollCanvas.style.width = width + "px";
    pianoRollCanvas.style.height = height + "px";
    ctx.scale(dpr, dpr);

    ctx.fillStyle = "#0d0d14";
    ctx.fillRect(0, 0, width, height);

    let filteredNotes = [];
    tracks.forEach(track => {
        if (filter === "all" || filter === track.name) {
            (track.notes || []).forEach(n => {
                filteredNotes.push({ ...n, color: track.color, trackName: track.name });
            });
        }
    });

    prNoteStats.textContent = `${filteredNotes.length} notas renderizadas`;

    if (filteredNotes.length === 0) {
        ctx.fillStyle = "#666";
        ctx.font = "12px sans-serif";
        ctx.textAlign = "center";
        ctx.fillText("No hay notas disponibles para esta pista", width / 2, height / 2);
        return;
    }

    let maxTime = 10;
    let minPitch = 127;
    let maxPitch = 0;

    filteredNotes.forEach(n => {
        const end = (n.start_time || 0) + (n.duration || 0.1);
        if (end > maxTime) maxTime = end;
        if (n.pitch < minPitch) minPitch = n.pitch;
        if (n.pitch > maxPitch) maxPitch = n.pitch;
    });

    minPitch = Math.max(12, minPitch - 2);
    maxPitch = Math.min(115, maxPitch + 2);
    const pitchRange = Math.max(12, maxPitch - minPitch);

    ctx.strokeStyle = "rgba(255, 255, 255, 0.04)";
    ctx.lineWidth = 1;
    for (let p = minPitch; p <= maxPitch; p++) {
        const y = height - ((p - minPitch) / pitchRange) * height;
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(width, y);
        ctx.stroke();
    }

    ctx.strokeStyle = "rgba(255, 255, 255, 0.06)";
    for (let t = 0; t <= maxTime; t += 2) {
        const x = (t / maxTime) * width;
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, height);
        ctx.stroke();
    }

    filteredNotes.forEach(n => {
        const x = (n.start_time / maxTime) * width;
        const noteWidth = Math.max(3, (n.duration / maxTime) * width);
        const y = height - ((n.pitch - minPitch + 1) / pitchRange) * height;
        const noteHeight = Math.max(3, height / pitchRange - 1);

        ctx.fillStyle = n.color || "#8b5cf6";
        ctx.globalAlpha = Math.min(1, Math.max(0.4, (n.velocity || 90) / 127));
        ctx.fillRect(x, y, noteWidth, noteHeight);

        ctx.fillStyle = "#ffffff";
        ctx.globalAlpha = 0.5;
        ctx.fillRect(x, y, noteWidth, 1);
    });

    ctx.globalAlpha = 1.0;
}

function renderMidiTracksList(tracks, songTitle) {
    midiTracksGrid.innerHTML = "";

    tracks.forEach((track) => {
        const card = document.createElement("div");
        card.className = "midi-track-card";
        card.innerHTML = `
            <div class="midi-track-left">
                <div class="midi-track-icon" style="color:${track.color}; border:1px solid ${track.color}40">${track.icon}</div>
                <div class="midi-track-info">
                    <div class="midi-track-title">${track.name}</div>
                    <div class="midi-track-sub">
                        <span class="badge" style="background:${track.color}20; color:${track.color}; border:1px solid ${track.color}40">
                            ${track.is_drum ? "Canal 10 (GM Drums)" : "Canal " + track.channel}
                        </span>
                        <span>${track.note_count} notas</span>
                    </div>
                </div>
            </div>
            <div class="midi-track-actions">
                <a href="${track.midi_url}" class="btn-midi-action" download="${track.midi_filename}" title="Descargar MIDI individual">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                        <polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>
                    </svg>
                    <span>MIDI (.mid)</span>
                </a>
                <a href="${track.audio_url}" class="btn-midi-action" download="${songTitle} - ${track.name}.wav" title="Descargar audio WAV">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>
                    </svg>
                    <span>Audio (.wav)</span>
                </a>
            </div>
        `;

        midiTracksGrid.appendChild(card);

        const midiDl = card.querySelector(`a[download="${track.midi_filename}"]`);
        if (midiDl) {
            midiDl.onclick = (e) => {
                e.preventDefault();
                forceDownload(track.midi_url, track.midi_filename);
            };
        }

        const wavDl = card.querySelector(`a[download="${songTitle} - ${track.name}.wav"]`);
        if (wavDl) {
            wavDl.onclick = (e) => {
                e.preventDefault();
                forceDownload(track.audio_url, `${songTitle} - ${track.name}.wav`);
            };
        }
    });
}

// ======================== TONALIDAD (cambio de tono / velocidad) ========================
let pitchUploadedFile = null;
let pitchSemitones = 0;
let pitchSpeed = 1.0;
let pitchFormat = "mp3";

const pitchUploadZone = document.getElementById("pitch-upload-zone");
const pitchFileInput = document.getElementById("pitch-file-input");
const pitchFileSelected = document.getElementById("pitch-file-selected");
const pitchSelectedFilename = document.getElementById("pitch-selected-filename");
const pitchSelectedFilesize = document.getElementById("pitch-selected-filesize");
const pitchRemoveFileBtn = document.getElementById("pitch-remove-file-btn");
const pitchValueEl = document.getElementById("pitch-value");
const pitchDescEl = document.getElementById("pitch-desc");
const pitchSlider = document.getElementById("pitch-slider");
const pitchResetBtn = document.getElementById("pitch-reset-btn");
const speedValueEl = document.getElementById("speed-value");
const speedDescEl = document.getElementById("speed-desc");
const speedSlider = document.getElementById("speed-slider");
const pitchExportBtn = document.getElementById("pitch-export-btn");
const pitchInputSection = document.getElementById("pitch-input-section");
const pitchProgressSection = document.getElementById("pitch-progress-section");
const pitchCompleteSection = document.getElementById("pitch-complete-section");
const pitchProgressBar = document.getElementById("pitch-progress-bar");
const pitchProgressPercent = document.getElementById("pitch-progress-percent");
const pitchProgressMessage = document.getElementById("pitch-progress-message");
const pitchCompleteMsg = document.getElementById("pitch-complete-msg");
const pitchSaveBtn = document.getElementById("pitch-save-btn");
const pitchNewBtn = document.getElementById("pitch-new-btn");

// Mismos textos que la version de Android, para que ambas coincidan.
const PITCH_TEXTS = {
    "0": "Tonalidad original de la cancion (sin cambios)",
    "1": "Subir 1/2 Tono (+1 Semitono)",
    "2": "Subir 1 Tono (+2 Semitonos)",
    "3": "Subir 1 1/2 Tonos (+3 Semitonos)",
    "4": "Subir 2 Tonos (+4 Semitonos)",
    "5": "Subir 2 1/2 Tonos (+5 Semitonos)",
    "6": "Subir 3 Tonos / Tritono (+6 Semitonos)",
    "12": "Subir 1 Octava Completa (+12 Semitonos)",
    "-1": "Bajar 1/2 Tono (-1 Semitono)",
    "-2": "Bajar 1 Tono (-2 Semitonos)",
    "-3": "Bajar 1 1/2 Tonos (-3 Semitonos)",
    "-4": "Bajar 2 Tonos (-4 Semitonos)",
    "-5": "Bajar 2 1/2 Tonos (-5 Semitonos)",
    "-6": "Bajar 3 Tonos / Tritono (-6 Semitonos)",
    "-12": "Bajar 1 Octava Completa (-12 Semitonos)",
};

function buildPitchDescription(st) {
    const r = Math.round(st * 10) / 10;
    if (PITCH_TEXTS[String(r)]) return PITCH_TEXTS[String(r)];
    const tonos = (Math.abs(r) / 2).toFixed(2);
    if (r > 0) return "Subir " + r.toFixed(1) + " semitonos (+" + tonos + " tonos)";
    return "Bajar " + Math.abs(r).toFixed(1) + " semitonos (-" + tonos + " tonos)";
}

function updatePitchUI() {
    const r = Math.round(pitchSemitones * 10) / 10;
    pitchValueEl.textContent = r === 0 ? "ORIGINAL (0.0)" : (r > 0 ? "+" : "") + r.toFixed(1) + " ST";
    pitchValueEl.classList.toggle("changed", r !== 0);
    pitchDescEl.textContent = buildPitchDescription(r);
    pitchSlider.value = r;

    speedValueEl.textContent = pitchSpeed.toFixed(2) + "x";
    speedValueEl.classList.toggle("changed", Math.abs(pitchSpeed - 1) > 0.001);
    if (Math.abs(pitchSpeed - 1) < 0.005) {
        speedDescEl.textContent = "Velocidad original";
    } else {
        const etiqueta = pitchSpeed > 1 ? "Mas rapido" : "Mas lento";
        speedDescEl.textContent = etiqueta + " (" + Math.round(pitchSpeed * 100) + "%)";
    }
    speedSlider.value = pitchSpeed;

    const cambiado = r !== 0 || Math.abs(pitchSpeed - 1) > 0.005;
    pitchExportBtn.disabled = !pitchUploadedFile || !cambiado;

    // Vista previa: habilitarla al haber archivo y, si ya esta sonando,
    // regenerar el fragmento con el nuevo tono sin cortar la escucha.
    if (typeof onPitchParamsChanged === "function") onPitchParamsChanged();
}

function setPitchSemitones(v) {
    pitchSemitones = Math.round(Math.max(-12, Math.min(12, v)) * 10) / 10;
    updatePitchUI();
}

document.querySelectorAll(".pitch-step").forEach((btn) => {
    btn.addEventListener("click", () => setPitchSemitones(pitchSemitones + parseFloat(btn.dataset.delta)));
});

if (pitchResetBtn) {
    pitchResetBtn.addEventListener("click", () => {
        pitchSemitones = 0;
        pitchSpeed = 1.0;
        updatePitchUI();
    });
}

if (pitchSlider) {
    pitchSlider.addEventListener("input", () => setPitchSemitones(parseFloat(pitchSlider.value)));
}
if (speedSlider) {
    speedSlider.addEventListener("input", () => {
        pitchSpeed = Math.max(0.5, Math.min(2, parseFloat(speedSlider.value)));
        updatePitchUI();
    });
}

document.querySelectorAll(".pitch-fmt").forEach((btn) => {
    btn.addEventListener("click", () => {
        document.querySelectorAll(".pitch-fmt").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        pitchFormat = btn.dataset.fmt;
    });
});

if (pitchUploadZone && pitchFileInput) {
    pitchUploadZone.addEventListener("click", () => pitchFileInput.click());
    pitchUploadZone.addEventListener("dragover", (e) => {
        e.preventDefault();
        pitchUploadZone.classList.add("dragover");
    });
    pitchUploadZone.addEventListener("dragleave", () => pitchUploadZone.classList.remove("dragover"));
    pitchUploadZone.addEventListener("drop", (e) => {
        e.preventDefault();
        pitchUploadZone.classList.remove("dragover");
        if (e.dataTransfer.files.length) uploadPitchFile(e.dataTransfer.files[0]);
    });
    pitchFileInput.addEventListener("change", (e) => {
        if (e.target.files.length) uploadPitchFile(e.target.files[0]);
    });
}

async function uploadPitchFile(file) {
    pitchUploadZone.classList.add("hidden");
    pitchFileSelected.classList.remove("hidden");
    pitchSelectedFilename.textContent = "Subiendo " + file.name + "...";
    pitchSelectedFilesize.textContent = (file.size / (1024 * 1024)).toFixed(1) + " MB";

    const fd = new FormData();
    fd.append("file", file);
    try {
        const resp = await fetch("/upload_audio", { method: "POST", body: fd });
        const data = await resp.json();
        if (data.error) {
            showStatus(data.error, "error");
            removePitchFile();
            return;
        }
        pitchUploadedFile = data.filename;
        pitchSelectedFilename.textContent = file.name;
        updatePitchUI();
    } catch (err) {
        showStatus("Error al subir el archivo", "error");
        removePitchFile();
    }
}

function removePitchFile() {
    // Cortar la vista previa: seguia sonando el fragmento de un archivo que ya
    // no esta seleccionado.
    if (typeof previewActivo !== "undefined" && previewActivo) {
        previewActivo = false;
        clearTimeout(previewTimer);
        pitchAudio.pause();
        setListenUI(false, false);
    }
    pitchUploadedFile = null;
    pitchUploadZone.classList.remove("hidden");
    pitchFileSelected.classList.add("hidden");
    pitchFileInput.value = "";
    updatePitchUI();
}

if (pitchRemoveFileBtn) pitchRemoveFileBtn.addEventListener("click", removePitchFile);

if (pitchExportBtn) {
    pitchExportBtn.addEventListener("click", () => {
        if (!pitchUploadedFile) return;
        pitchInputSection.classList.add("hidden");
        pitchProgressSection.classList.remove("hidden");
        pitchCompleteSection.classList.add("hidden");
        pitchProgressBar.style.width = "0%";
        pitchProgressPercent.textContent = "0%";
        pitchProgressMessage.textContent = "Aplicando cambio de tonalidad...";

        socket.emit("start_pitch_export", {
            filename: pitchUploadedFile,
            semitones: pitchSemitones,
            speed: pitchSpeed,
            format: pitchFormat,
        });
    });
}

socket.on("pitch_progress", (data) => {
    const pct = data.percent || 0;
    pitchProgressBar.style.width = pct + "%";
    pitchProgressPercent.textContent = pct + "%";
    if (data.message) pitchProgressMessage.textContent = data.message;
});

socket.on("pitch_error", (data) => {
    pitchProgressSection.classList.add("hidden");
    pitchInputSection.classList.remove("hidden");
    showStatus(data.error || "Error al procesar la tonalidad", "error");
});

socket.on("pitch_complete", (data) => {
    pitchProgressSection.classList.add("hidden");
    pitchCompleteSection.classList.remove("hidden");
    const partes = [];
    if (data.semitones !== 0) {
        partes.push((data.semitones > 0 ? "+" : "") + data.semitones + " semitonos");
    }
    if (data.speed !== 1) partes.push("velocidad " + data.speed + "x");
    pitchCompleteMsg.textContent = data.filename + " (" + partes.join(", ") + ") — " + data.size_mb + " MB";
    pitchSaveBtn.onclick = () => forceDownload(data.url, data.filename);
});

if (pitchNewBtn) {
    pitchNewBtn.addEventListener("click", () => {
        pitchCompleteSection.classList.add("hidden");
        pitchInputSection.classList.remove("hidden");
    });
}

// La llamada inicial a updatePitchUI() va al final del bloque de vista previa:
// aqui todavia no existen sus elementos y lanzaria un error que rompe el resto
// del script.

// ---------- Vista previa en vivo de la tonalidad ----------
const pitchAudio = document.getElementById("pitch-audio");
const pitchListenBtn = document.getElementById("pitch-listen-btn");
const pitchListenText = document.getElementById("pitch-listen-text");
const pitchListenIcon = document.getElementById("pitch-listen-icon");
const pitchPreviewHint = document.getElementById("pitch-preview-hint");

const ICON_PLAY = '<polygon points="6 4 20 12 6 20 6 4"/>';
const ICON_STOP = '<rect x="6" y="6" width="12" height="12" rx="1"/>';

let previewTimer = null;
let previewCargando = false;
// Intencion del usuario. No basta con mirar audio.paused: mientras se
// regenera el fragmento el audio queda pausado un instante, y un clic en ese
// momento se interpretaba como "reproducir" en vez de "detener".
let previewActivo = false;

function previewUrl() {
    return "/pitch_preview?filename=" + encodeURIComponent(pitchUploadedFile) +
        "&semitones=" + pitchSemitones + "&speed=" + pitchSpeed;
}

function setListenUI(sonando, cargando) {
    pitchListenIcon.innerHTML = sonando ? ICON_STOP : ICON_PLAY;
    if (cargando) {
        pitchListenText.textContent = "Preparando...";
    } else {
        pitchListenText.textContent = sonando ? "Detener" : "Escuchar con este tono";
    }
    pitchListenBtn.classList.toggle("playing", sonando);
}

async function reproducirPreview(desdeSegundos) {
    if (!pitchUploadedFile) return;
    previewCargando = true;
    setListenUI(true, true);
    try {
        pitchAudio.src = previewUrl();
        pitchAudio.load();
        if (desdeSegundos > 0) {
            await new Promise((resolve) => {
                const listo = () => { pitchAudio.removeEventListener("loadedmetadata", listo); resolve(); };
                pitchAudio.addEventListener("loadedmetadata", listo);
                setTimeout(resolve, 4000);
            });
            if (isFinite(pitchAudio.duration) && desdeSegundos < pitchAudio.duration) {
                pitchAudio.currentTime = desdeSegundos;
            }
        }
        await pitchAudio.play();
        pitchPreviewHint.textContent = "Sonando con el tono aplicado. Mueve el tono o la velocidad y se actualiza solo.";
    } catch (err) {
        previewActivo = false;
        pitchPreviewHint.textContent = "No se pudo reproducir la vista previa.";
        setListenUI(false, false);
    } finally {
        previewCargando = false;
        if (previewActivo && !pitchAudio.paused) setListenUI(true, false);
    }
}

// Si el usuario cambia el tono mientras esta sonando, se regenera el fragmento
// y sigue desde el mismo punto: se siente como si el cambio fuera en vivo.
function refrescarPreviewSiSuena() {
    if (!previewActivo || !pitchUploadedFile) return;
    const pos = pitchAudio.currentTime;
    clearTimeout(previewTimer);
    setListenUI(true, true);
    previewTimer = setTimeout(() => reproducirPreview(pos), 450);
}

if (pitchListenBtn) {
    pitchListenBtn.addEventListener("click", () => {
        if (previewActivo) {
            previewActivo = false;
            clearTimeout(previewTimer);
            pitchAudio.pause();
            setListenUI(false, false);
            pitchPreviewHint.textContent = "Reproduce un fragmento con el tono aplicado, tal como quedara al exportar.";
            return;
        }
        previewActivo = true;
        reproducirPreview(0);
    });
}

if (pitchAudio) {
    pitchAudio.addEventListener("ended", () => {
        previewActivo = false;
        setListenUI(false, false);
        pitchPreviewHint.textContent = "Fin del fragmento. Pulsa de nuevo para escucharlo otra vez.";
    });
    pitchAudio.addEventListener("error", () => {
        if (pitchAudio.src) {
            previewActivo = false;
            setListenUI(false, false);
            pitchPreviewHint.textContent = "No se pudo generar la vista previa.";
        }
    });
}

// La llama updatePitchUI cada vez que cambia el tono o la velocidad.
function onPitchParamsChanged() {
    if (pitchListenBtn) pitchListenBtn.disabled = !pitchUploadedFile;
    refrescarPreviewSiSuena();
}

updatePitchUI();
