/** VoiceAgent web client */

const API = '/api';
const SAMPLE_RATE = 16000;
const MIN_SAMPLES = SAMPLE_RATE * 0.3;
const PREFERRED_MIC = 'AK-2';

let sessionId = crypto.randomUUID();
let token = sessionStorage.getItem('va_token') || '';
let busy = false;
let recording = null; // { ctx, stream, node, chunks }
let currentAudio = null;
let micsLoaded = false;

const $ = (id) => document.getElementById(id);
const el = {
    log: $('conversation-log'),
    micBtn: $('btn-mic'),
    micLabel: $('mic-label'),
    micSelect: $('mic-select'),
    levelFill: $('level-fill'),
    personaSelect: $('persona-select'),
    backendBadge: $('backend-badge'),
    confirmBanner: $('confirm-banner'),
    confirmText: $('confirm-text'),
    textForm: $('text-form'),
    textInput: $('text-input'),
};

function init() {
    const params = new URLSearchParams(window.location.search);
    if (params.get('token')) {
        token = params.get('token');
        sessionStorage.setItem('va_token', token);
        history.replaceState(null, '', window.location.pathname); // keep the token out of the address bar
    }
    if (!token) askToken();

    el.micBtn.addEventListener('mousedown', startRecording);
    el.micBtn.addEventListener('mouseup', stopRecording);
    el.micBtn.addEventListener('mouseleave', stopRecording);
    el.micBtn.addEventListener('touchstart', (e) => { e.preventDefault(); startRecording(); });
    el.micBtn.addEventListener('touchend', (e) => { e.preventDefault(); stopRecording(); });
    window.addEventListener('keydown', (e) => {
        if (e.code === 'Space' && !e.repeat && document.activeElement !== el.textInput) {
            e.preventDefault();
            startRecording();
        }
    });
    window.addEventListener('keyup', (e) => {
        if (e.code === 'Space' && document.activeElement !== el.textInput) stopRecording();
    });
    $('btn-confirm').onclick = () => sendConfirm('yes');
    $('btn-deny').onclick = () => sendConfirm('no');
    $('btn-end').onclick = endSession;
    $('btn-stop-audio').onclick = stopAudio;
    el.personaSelect.onchange = switchPersona;
    el.micSelect.onchange = () => localStorage.setItem('va_mic', el.micSelect.value);
    el.textForm.onsubmit = (e) => {
        e.preventDefault();
        const text = el.textInput.value.trim();
        if (!text) return;
        el.textInput.value = '';
        sendText(text);
    };

    loadPersonas();
    updateHealth();
    setInterval(updateHealth, 15000);
}

function askToken() {
    token = prompt('Enter API token (leave blank if none is set):') || 'NONE';
    sessionStorage.setItem('va_token', token);
}

async function api(path, { method = 'POST', body, headers = {}, retry = true } = {}) {
    const resp = await fetch(API + path, {
        method,
        headers: { Authorization: `Bearer ${token}`, 'X-Session-Id': sessionId, ...headers },
        body,
    });
    if (resp.status === 401 && retry) {
        askToken();
        return api(path, { method, body, headers, retry: false });
    }
    return resp;
}

async function apiJson(path, payload) {
    return api(path, {
        body: JSON.stringify(payload ?? {}),
        headers: { 'Content-Type': 'application/json' },
    });
}

/* ---------- status ---------- */

async function updateHealth() {
    let services = {};
    try {
        const resp = await api('/health', { method: 'GET' });
        if (resp.ok) services = (await resp.json()).services;
    } catch { /* leave all dots red */ }
    for (const name of ['stt', 'llm', 'tts']) {
        const dot = $(`dot-${name}`);
        const state = services[name] || 'err';
        dot.className = `dot ${state === 'ok' ? 'ok' : state === 'off' ? 'off' : 'err'}`;
        dot.title = `${name.toUpperCase()}: ${state}`;
    }
}

async function loadPersonas() {
    try {
        const resp = await api('/personas', { method: 'GET' });
        if (!resp.ok) return;
        const data = await resp.json();
        el.personaSelect.innerHTML = '';
        for (const p of data.personas) {
            const opt = new Option(p.name, p.name, false, p.name === data.current);
            opt.title = p.description;
            el.personaSelect.add(opt);
        }
    } catch { /* ignore */ }
}

async function switchPersona() {
    const resp = await apiJson('/persona', { name: el.personaSelect.value });
    if (!resp.ok) {
        addSystem((await resp.json()).error?.message || 'Could not switch persona');
        loadPersonas();
    } else {
        addSystem(`Switched to ${el.personaSelect.value}.`);
    }
}

function setBusy(on, label) {
    busy = on;
    el.micBtn.disabled = on;
    el.textInput.disabled = on;
    el.micLabel.textContent = on ? label || 'Thinking...' : 'Hold to Talk';
    $('thinking')?.remove();
    if (on) {
        const t = document.createElement('div');
        t.id = 'thinking';
        t.className = 'msg system';
        t.textContent = label || 'Thinking...';
        el.log.appendChild(t);
        scrollDown();
    }
}

/* ---------- recording ---------- */

async function populateMics(preselect) {
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'audioinput');
    const saved = localStorage.getItem('va_mic');
    el.micSelect.innerHTML = '';
    for (const d of devices) el.micSelect.add(new Option(d.label || 'Microphone', d.deviceId));
    const pick = devices.find((d) => d.deviceId === saved)
        || devices.find((d) => d.label.includes(PREFERRED_MIC))
        || devices.find((d) => d.deviceId === preselect);
    if (pick) el.micSelect.value = pick.deviceId;
}

const AUDIO_FLAGS = { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 };

async function openMic(deviceId) {
    // exact first so the chosen mic is really used; fall back to any mic rather than failing
    const tries = deviceId
        ? [{ ...AUDIO_FLAGS, deviceId: { exact: deviceId } }, { ...AUDIO_FLAGS }]
        : [{ ...AUDIO_FLAGS }];
    let lastErr;
    for (const audio of tries) {
        try {
            return await navigator.mediaDevices.getUserMedia({ audio });
        } catch (e) {
            lastErr = e;
        }
    }
    throw lastErr;
}

async function getStream() {
    let stream = await openMic(el.micSelect.value);
    if (!micsLoaded) {
        micsLoaded = true;
        const active = stream.getAudioTracks()[0].getSettings().deviceId;
        await populateMics(active);
        if (el.micSelect.value && el.micSelect.value !== active) {
            stream.getTracks().forEach((t) => t.stop());
            stream = await openMic(el.micSelect.value);
        }
    }
    const label = stream.getAudioTracks()[0].label;
    el.micSelect.title = `Using: ${label}`;
    return stream;
}

function resample(samples, fromRate, toRate) {
    if (fromRate === toRate) return samples;
    const ratio = fromRate / toRate;
    const out = new Float32Array(Math.floor(samples.length / ratio));
    for (let i = 0; i < out.length; i++) {
        const start = Math.floor(i * ratio);
        const end = Math.max(start + 1, Math.min(samples.length, Math.floor((i + 1) * ratio)));
        let sum = 0;
        for (let j = start; j < end; j++) sum += samples[j];
        out[i] = sum / (end - start);
    }
    return out;
}

function normalize(samples) {
    let peak = 0;
    for (let i = 0; i < samples.length; i++) peak = Math.max(peak, Math.abs(samples[i]));
    if (peak > 0 && peak < 0.5) {
        const gain = Math.min(0.9 / peak, 20);
        for (let i = 0; i < samples.length; i++) samples[i] *= gain;
    }
    return peak;
}

async function startRecording() {
    if (recording || busy) return;
    stopAudio();
    const pending = { starting: true };
    recording = pending;
    try {
        const stream = await getStream();
        if (recording !== pending) { // released before the mic opened
            stream.getTracks().forEach((t) => t.stop());
            return;
        }
        const ctx = new AudioContext(); // native rate; resampled to 16 kHz on release
        await ctx.resume();
        const source = ctx.createMediaStreamSource(stream);
        const node = ctx.createScriptProcessor(4096, 1, 1);
        const chunks = [];
        node.onaudioprocess = (e) => {
            const input = e.inputBuffer.getChannelData(0);
            chunks.push(new Float32Array(input));
            let sum = 0;
            for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
            el.levelFill.style.width = `${Math.min(100, Math.sqrt(sum / input.length) * 500)}%`;
        };
        source.connect(node);
        node.connect(ctx.destination);
        recording = { ctx, stream, node, chunks, rate: ctx.sampleRate };
        el.micBtn.classList.add('recording');
        el.micLabel.textContent = 'Listening...';
    } catch (e) {
        recording = null;
        console.error('Mic error:', e);
        addSystem(`Microphone unavailable (${e.name}). Check permissions and the mic dropdown; needs localhost or HTTPS.`);
    }
}

async function stopRecording() {
    const rec = recording;
    if (!rec) return;
    recording = null;
    if (rec.starting) return;
    el.micBtn.classList.remove('recording');
    el.levelFill.style.width = '0%';
    rec.node.disconnect();
    rec.stream.getTracks().forEach((t) => t.stop());
    await rec.ctx.close();

    el.micLabel.textContent = 'Hold to Talk';
    const total = rec.chunks.reduce((n, c) => n + c.length, 0);
    const raw = new Float32Array(total);
    let offset = 0;
    for (const c of rec.chunks) { raw.set(c, offset); offset += c.length; }
    const samples = resample(raw, rec.rate, SAMPLE_RATE);
    if (samples.length < MIN_SAMPLES) {
        addSystem('Too short. Hold the button while you speak.');
        return;
    }
    const peak = normalize(samples);
    if (peak < 0.005) {
        addSystem('Heard silence. Wrong mic selected, or it is muted? Check the mic dropdown.');
        return;
    }
    await sendTurn(encodeWav(samples));
}

function encodeWav(samples) {
    const buffer = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(buffer);
    const str = (off, s) => { for (let i = 0; i < s.length; i++) view.setUint8(off + i, s.charCodeAt(i)); };
    str(0, 'RIFF');
    view.setUint32(4, 36 + samples.length * 2, true);
    str(8, 'WAVE');
    str(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, SAMPLE_RATE, true);
    view.setUint32(28, SAMPLE_RATE * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    str(36, 'data');
    view.setUint32(40, samples.length * 2, true);
    for (let i = 0; i < samples.length; i++) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return new Blob([buffer], { type: 'audio/wav' });
}

/* ---------- turns ---------- */

async function runRequest(promise, label, showTranscript = false) {
    setBusy(true, label);
    try {
        const resp = await promise();
        const data = await resp.json();
        if (!resp.ok) {
            addSystem(data.error?.message || `Error ${resp.status}`);
            return;
        }
        handleResponse(data, showTranscript);
    } catch (e) {
        addSystem(`Request failed: ${e.message}`);
    } finally {
        setBusy(false);
    }
}

const sendTurn = (blob) => runRequest(
    () => api('/turn', { body: blob, headers: { 'Content-Type': 'audio/wav' } }), 'Transcribing...', true);

function sendText(text) {
    addMessage('user', text);
    return runRequest(() => apiJson('/text', { text }));
}

function sendConfirm(answer) {
    el.confirmBanner.classList.add('hidden');
    addMessage('user', answer === 'yes' ? 'Yes' : 'No');
    return runRequest(() => apiJson('/confirm', { answer }));
}

function handleResponse(data, showTranscript = false) {
    if (showTranscript && data.transcript) addMessage('user', data.transcript);
    if (data.reply_text) addMessage('agent', data.reply_text, data.audio_wav_base64, data.timings_ms);
    else if (data.audio_wav_base64) playAudio(data.audio_wav_base64);

    if (data.persona) el.personaSelect.value = data.persona;
    el.backendBadge.classList.toggle('hidden', data.llm_backend !== 'cloud');
    if (data.status === 'confirm_required' && data.confirm) {
        el.confirmText.textContent = data.confirm.prompt;
        el.confirmBanner.classList.remove('hidden');
        if (!data.reply_text && data.audio_wav_base64) playAudio(data.audio_wav_base64);
    } else {
        el.confirmBanner.classList.add('hidden');
    }
}

async function endSession() {
    setBusy(true, 'Saving...');
    try {
        const resp = await apiJson('/end');
        if (resp.ok) {
            el.log.innerHTML = '';
            el.confirmBanner.classList.add('hidden');
            sessionId = crypto.randomUUID();
            addSystem('Session ended. Summary saved to today\'s note.');
            loadPersonas();
        } else {
            addSystem((await resp.json()).error?.message || 'Could not end the session');
        }
    } finally {
        setBusy(false);
    }
}

/* ---------- output ---------- */

function scrollDown() { el.log.scrollTop = el.log.scrollHeight; }

function addSystem(text) { addMessage('system', text); }

function addMessage(role, text, audioBase64 = '', timings = null) {
    const msg = document.createElement('div');
    msg.className = `msg ${role}`;
    msg.dataset.text = text;
    if (role !== 'system') {
        const meta = document.createElement('span');
        meta.className = 'msg-meta';
        meta.textContent = role === 'user' ? 'You' : 'Agent';
        if (timings?.total) meta.textContent += ` · ${(timings.total / 1000).toFixed(1)}s`;
        msg.appendChild(meta);
    }
    const body = document.createElement('div');
    body.textContent = text;
    msg.appendChild(body);
    if (audioBase64) {
        const btn = document.createElement('button');
        btn.className = 'replay';
        btn.textContent = '▶ Replay';
        btn.onclick = () => playAudio(audioBase64);
        msg.appendChild(btn);
        playAudio(audioBase64);
    }
    el.log.appendChild(msg);
    scrollDown();
}

function playAudio(base64) {
    stopAudio();
    currentAudio = new Audio(`data:audio/wav;base64,${base64}`);
    currentAudio.play().catch(() => addSystem('Autoplay blocked. Use Replay on the message.'));
}

function stopAudio() {
    if (currentAudio) {
        currentAudio.pause();
        currentAudio = null;
    }
}

init();
