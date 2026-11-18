/* EVO·VISION — frontend sem build step. Consome a API FastAPI same-origin. */
"use strict";

const REFRESH_MS = 2000;
const ALERT_THRESHOLD = 1.8;
const CRIT_THRESHOLD = 3.5;
const MAX_CHART_POINTS = 160;

const $ = (id) => document.getElementById(id);
const state = {
  token: localStorage.getItem("evo_token") || null,
  user: JSON.parse(localStorage.getItem("evo_user") || "null"),
  status: "NORMAL",
  events: [],
  telemetry: [],
  cameraStream: null,
  cameraMode: null, // 'local' | 'ip'
  pendingAction: null,
};

/* ───────── util ───────── */
const pad = (n) => String(n).padStart(2, "0");
const fmtClock = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
const fmtTime = (iso) => {
  const d = new Date(iso);
  return isNaN(d) ? "—" : `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let toastTimer = null;
function toast(msg, isError = false) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.toggle("error", isError);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), 4200);
}

async function api(path, { method = "GET", body, auth = false } = {}) {
  if (window.EVO_DEMO?.isActive()) return window.EVO_DEMO.resolve(path, { method, body, auth });
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  if (auth && state.token) headers["Authorization"] = `Bearer ${state.token}`;
  let resp;
  try {
    resp = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  } catch (networkError) {
    // Sem backend ao alcance (ex.: deploy estático na Vercel) → modo demo interativo
    if (window.EVO_DEMO) return window.EVO_DEMO.resolve(path, { method, body, auth });
    throw networkError;
  }
  if (window.EVO_DEMO && (resp.status === 404 || resp.status >= 500)) {
    return window.EVO_DEMO.resolve(path, { method, body, auth });
  }
  if (resp.status === 401 && auth) {
    clearSession();
    renderAuthUI();
    throw new Error("Sessão expirada. Faça login novamente.");
  }
  return resp;
}

/* ───────── relógio ───────── */
setInterval(() => {
  const now = new Date();
  $("clock").textContent = fmtClock(now);
  const hud = $("hudTime");
  if (hud) hud.textContent = fmtClock(now);
}, 1000);

/* ───────── status / banner / big metrics ───────── */
const STATUS_COPY = {
  NORMAL: "Máquina operando dentro dos parâmetros.",
  ALERTA: "Vibração acima do limiar de alerta. Inspecionar.",
  CRITICO: "ESTADO CRÍTICO — PARADA DE EMERGÊNCIA RECOMENDADA.",
};

function applyStatus(status, value, lastUpdate, events24h, esp32) {
  state.status = status;
  document.body.className = `status-${status.toLowerCase()}`;
  $("statusWord").textContent = status;
  $("statusHint").textContent = STATUS_COPY[status] || "";
  $("bigValue").textContent = Number(value || 0).toFixed(3);
  $("events24h").textContent = events24h ?? 0;
  $("lastUpdate").textContent = fmtTime(lastUpdate);
  $("esp32State").textContent = esp32 ? "ONLINE" : "OFFLINE";
  $("esp32State").classList.toggle("warn", !esp32);
}

/* ───────── telemetria (canvas) ───────── */
const canvas = $("telemetryChart");
const ctx = canvas.getContext("2d");

function drawChart(points) {
  const wrap = canvas.parentElement;
  const w = wrap.clientWidth, h = wrap.clientHeight;
  if (!w || !h) return;
  const dpr = window.devicePixelRatio || 1;
  if (canvas.width !== w * dpr || canvas.height !== h * dpr) {
    canvas.width = w * dpr;
    canvas.height = h * dpr;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const padL = 34, padR = 8, padT = 10, padB = 22;
  const iw = w - padL - padR, ih = h - padT - padB;
  const maxVal = points.length ? Math.max(...points.map((p) => Number(p.value) || 0)) : 0;
  const yMax = Math.max(CRIT_THRESHOLD * 1.25, Math.ceil(maxVal * 1.15), 4);
  const gridStep = yMax <= 5 ? 1 : yMax <= 10 ? 2 : 5;
  const yOf = (v) => padT + ih - (Math.min(v, yMax) / yMax) * ih;

  // grid horizontal
  ctx.font = "9px ui-monospace, Menlo, Consolas, monospace";
  ctx.strokeStyle = "rgba(120,140,170,.14)";
  ctx.fillStyle = "#5f6f8c";
  ctx.lineWidth = 1;
  ctx.textAlign = "right";
  for (let g = 0; g <= yMax; g += gridStep) {
    const y = yOf(g);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
    ctx.fillText(String(g), padL - 5, y + 3);
  }

  // thresholds tracejados
  const dash = (v, color, label) => {
    const y = yOf(v);
    ctx.save();
    ctx.setLineDash([6, 5]);
    ctx.strokeStyle = color; ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = color; ctx.textAlign = "left";
    ctx.fillText(label, padL + 4, y - 3);
    ctx.restore();
  };
  dash(ALERT_THRESHOLD, "rgba(255,176,0,.75)", "ALERTA 1.8");
  dash(CRIT_THRESHOLD, "rgba(255,59,48,.8)", "CRIT 3.5");

  if (!points.length) {
    $("chartEmpty").hidden = false;
    return;
  }
  $("chartEmpty").hidden = true;
  const sliced = points.slice(-MAX_CHART_POINTS);
  const step = iw / Math.max(sliced.length - 1, 1);

  // glow + linha
  const grad = ctx.createLinearGradient(0, padT, 0, padT + ih);
  grad.addColorStop(0, "rgba(47,214,255,.28)");
  grad.addColorStop(1, "rgba(47,214,255,0)");
  ctx.beginPath();
  sliced.forEach((p, i) => {
    const x = padL + i * step, y = yOf(p.value);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.save();
  ctx.lineTo(padL + (sliced.length - 1) * step, padT + ih);
  ctx.lineTo(padL, padT + ih);
  ctx.closePath();
  ctx.fillStyle = grad;
  ctx.fill();
  ctx.restore();

  ctx.beginPath();
  sliced.forEach((p, i) => {
    const x = padL + i * step, y = yOf(p.value);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.strokeStyle = "#2fd6ff";
  ctx.lineWidth = 2;
  ctx.shadowColor = "rgba(47,214,255,.65)";
  ctx.shadowBlur = 9;
  ctx.lineJoin = "round";
  ctx.stroke();
  ctx.shadowBlur = 0;

  // eixo X: primeira e última hora
  ctx.fillStyle = "#5f6f8c";
  ctx.textAlign = "left";
  ctx.fillText(fmtTime(sliced[0].timestamp), padL, h - 6);
  ctx.textAlign = "right";
  ctx.fillText(fmtTime(sliced[sliced.length - 1].timestamp), w - padR, h - 6);
}

async function refreshTelemetry() {
  try {
    const resp = await api("/telemetry?limit=150");
    if (!resp.ok) return;
    const data = await resp.json();
    state.telemetry = data.points || [];
    drawChart(state.telemetry);
  } catch (e) { /* poll silencioso */ }
}

async function refreshStatus() {
  try {
    const resp = await api("/status");
    if (!resp.ok) throw new Error();
    const s = await resp.json();
    applyStatus(s.current_status, s.last_value, s.last_update, s.events_last_hour, s.esp32_connected);
    setApiLed(true);
  } catch (e) {
    setApiLed(false);
  }
}

const TEMP_ALERT = 60, TEMP_CRIT = 75;

async function refreshTemperature() {
  try {
    const resp = await api("/temperature");
    if (!resp.ok) throw new Error();
    const t = await resp.json();
    const temp = Number(t.temperature || 0);
    const src = String(t.source || "mock").toUpperCase();
    $("tempValue").textContent = temp > 0 ? temp.toFixed(1) : "--.-";
    $("tempUnit").textContent = temp > 0 ? `°C · ${src}` : "°C · aguardando sensor";
    const metric = $("tempMetric");
    metric.classList.toggle("warn", temp >= TEMP_ALERT && temp < TEMP_CRIT);
    metric.classList.toggle("crit", temp >= TEMP_CRIT);
  } catch (e) { /* poll silencioso */ }
}

function setApiLed(ok) {
  const demo = window.EVO_DEMO?.isActive?.();
  $("apiLed").classList.toggle("ok", ok || !!demo);
  $("apiLed").classList.toggle("fail", !ok && !demo);
  $("apiState").textContent = demo ? "MODO DEMO" : ok ? "API ONLINE" : "API OFFLINE";
}

/* ───────── ocorrências ───────── */
function frameUrl(p) {
  if (!p) return null;
  const name = String(p).split("/").pop();
  return name ? `/captures/${encodeURIComponent(name)}` : null;
}

function statusChip(s) {
  const cls = { NORMAL: "chip--normal", ALERTA: "chip--alerta", CRITICO: "chip--critico" }[s] || "chip--normal";
  return `<span class="chip ${cls}">${esc(s)}</span>`;
}

async function refreshEvents() {
  const filter = $("eventFilter").value;
  const qs = filter ? `&status=${filter}` : "";
  try {
    const resp = await api(`/events?page=1&page_size=12${qs}`);
    if (!resp.ok) return;
    const data = await resp.json();
    state.events = data.events || [];
    renderEvents();
  } catch (e) { /* poll silencioso */ }
}

function renderEvents() {
  const tbody = $("eventsBody");
  if (!state.events.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="table-empty">Nenhuma ocorrência registrada.</td></tr>';
    return;
  }
  tbody.innerHTML = state.events.slice(0, 12).map((e) => {
    const fu = frameUrl(e.frame_path);
    const thumb = fu ? `<img class="frame-thumb" src="${esc(fu)}" alt="frame" loading="lazy" onerror="this.replaceWith(document.createTextNode('—'))" onclick="window.open('${esc(fu)}','_blank')" />` : "—";
    const temp = e.temperature != null ? Number(e.temperature).toFixed(1) : "—";
    return `<tr><td>${esc(fmtTime(e.timestamp))}</td><td>${statusChip(e.status)}</td><td>${Number(e.value).toFixed(3)}</td><td>${temp}</td><td>${esc(e.source)}</td><td>${thumb}</td></tr>`;
  }).join("");
}

$("eventFilter").addEventListener("change", refreshEvents);
$("exportCsvBtn").addEventListener("click", () => {
  const filter = $("eventFilter").value;
  if (window.EVO_DEMO?.isActive()) { window.EVO_DEMO.csvDownload(filter); return; }
  const qs = filter ? `?status=${filter}` : "";
  window.location.href = `/export/csv${qs}`;
});

/* ───────── câmera ───────── */
const camVideo = $("localVideo");
const camImg = $("mjpegView");

function baseHudLabel(mode) {
  const demo = window.EVO_DEMO?.isActive?.();
  return { local: "WEB CAM · LIVE", monitor: demo ? "MONITOR · DEMO" : "MONITOR · LIVE", ip: "IP CAM · LIVE" }[mode];
}

function setCameraUI(mode) {
  state.cameraMode = mode;
  if (mode !== "monitor" && window.EVO_DEMO) window.EVO_DEMO.disconnectDemoMonitor();
  const live = mode !== null;
  $("cameraEmpty").hidden = live;
  camVideo.hidden = mode !== "local";
  const demoMonitor = window.EVO_DEMO?.isActive?.() && mode === "monitor";
  $("demoFeed").hidden = !demoMonitor;
  camImg.hidden = !(mode === "ip" || (mode === "monitor" && !demoMonitor));
  $("cameraHud").hidden = !live;
  const baseHud = baseHudLabel(mode);
  if (baseHud) $("hudLabel").textContent = state.irMode ? `${baseHud} · IR` : baseHud;
  $("camTag").textContent = live ? "AO VIVO" : "OFFLINE";
  $("camTag").classList.toggle("on", live);
  $("connectCamBtn").hidden = mode === "local";
  $("disconnectCamBtn").hidden = mode !== "local";
  $("connectMonitorBtn").hidden = mode === "monitor";
  $("disconnectMonitorBtn").hidden = mode !== "monitor";
  $("camHint").textContent = live
    ? (mode === "local" ? "Webcam local ativa — vídeo não sai desta máquina."
      : mode === "monitor" ? "Stream MJPEG do Vision Monitor (/stream)."
      : "Stream MJPEG carregado via <img>.")
    : "A câmera roda no navegador — nenhum vídeo sai desta máquina.";
}

$("connectMonitorBtn").addEventListener("click", () => {
  if (window.EVO_DEMO?.isActive()) {
    const canvas = $("demoFeed");
    camImg.removeAttribute("src");
    window.EVO_DEMO.connectDemoMonitor(canvas);
    setCameraUI("monitor");
    toast("Feed de demonstração (sintético).");
    return;
  }
  camImg.onerror = () => {
    if (window.EVO_DEMO?.isActive()) {
      const canvas = $("demoFeed");
      window.EVO_DEMO.connectDemoMonitor(canvas);
      setCameraUI("monitor");
      toast("API indisponível — feed de demonstração ativado.");
      return;
    }
    toast("Monitor sem sinal. O Vision Monitor está rodando?", true); setCameraUI(null);
  };
  camImg.onload = () => toast("Conectado ao stream do monitor.");
  camImg.src = `/stream?t=${Date.now()}`;
  setCameraUI("monitor");
});
$("disconnectMonitorBtn").addEventListener("click", () => {
  if (window.EVO_DEMO) window.EVO_DEMO.disconnectDemoMonitor();
  camImg.removeAttribute("src");
  $("demoFeed").hidden = true;
  setCameraUI(null);
});

async function connectLocalCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    toast("Navegador sem suporte a getUserMedia.", true);
    return;
  }
  try {
    state.cameraStream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 } }, audio: false });
    camVideo.srcObject = state.cameraStream;
    setCameraUI("local");
    toast("Câmera conectada.");
  } catch (err) {
    const msg = err.name === "NotAllowedError" ? "Permissão de câmera negada."
      : err.name === "NotFoundError" ? "Nenhuma webcam encontrada."
      : `Falha ao abrir câmera: ${err.message}`;
    toast(msg, true);
  }
}

function disconnectLocalCamera() {
  state.cameraStream?.getTracks().forEach((t) => t.stop());
  state.cameraStream = null;
  camVideo.srcObject = null;
  setCameraUI(null);
}

$("connectCamBtn").addEventListener("click", connectLocalCamera);
$("disconnectCamBtn").addEventListener("click", disconnectLocalCamera);
$("loadMjpegBtn").addEventListener("click", () => {
  const url = $("mjpegUrl").value.trim();
  if (!/^https?:\/\//i.test(url)) { toast("URL inválida. Use http(s)://…", true); return; }
  camImg.onerror = () => { toast("Stream não carregou. Verifique URL e rede.", true); setCameraUI(null); };
  camImg.onload = () => toast("Stream carregado.");
  camImg.src = url;
  setCameraUI("ip");
});

/* ───────── infravermelho (visão noturna) ───────── */
state.irMode = false;
const irToggleBtn = $("irToggleBtn");
function applyIrMode(on) {
  state.irMode = on;
  [camVideo, camImg, $("demoFeed")].forEach((el) => el.classList.toggle("ir-mode", on));
  irToggleBtn.classList.toggle("is-active", on);
  irToggleBtn.setAttribute("aria-pressed", String(on));
  const baseHud = baseHudLabel(state.cameraMode);
  if (baseHud) $("hudLabel").textContent = on ? `${baseHud} · IR` : baseHud;
  toast(on ? "Infravermelho ativado." : "Infravermelho desativado.");
  try { localStorage.setItem("evovision_ir", on ? "1" : "0"); } catch (e) { /* ignore */ }
}
irToggleBtn.addEventListener("click", () => applyIrMode(!state.irMode));

/* ───────── localização do movimento (Vision Monitor) ───────── */
const hudMotion = $("hudMotion");
async function pollMotion() {
  try {
    const resp = await api("/motion");
    if (!resp.ok) throw new Error("no motion");
    const d = await resp.json();
    hudMotion.textContent = d.fresh === false
      ? "MOV: SEM SINAL"
      : `MOV: ${d.region} ${d.direction || ""} · ${Number(d.magnitude).toFixed(2)}`;
    hudMotion.classList.toggle("warn", d.state === "ALERTA");
    hudMotion.classList.toggle("hot", d.state === "CRITICO");
  } catch (e) {
    hudMotion.textContent = "MOV: --";
    hudMotion.classList.remove("warn", "hot");
  }
}
setInterval(pollMotion, 2000);
pollMotion();
try {
  if (localStorage.getItem("evovision_ir") === "1") {
    state.irMode = true;
    [camVideo, camImg, $("demoFeed")].forEach((el) => el.classList.add("ir-mode"));
    irToggleBtn.classList.add("is-active");
    irToggleBtn.setAttribute("aria-pressed", "true");
  }
} catch (e) { /* ignore */ }

document.querySelectorAll("[data-cam-tab]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const tab = btn.dataset.camTab;
    document.querySelectorAll("[data-cam-tab]").forEach((b) => b.classList.toggle("is-active", b === btn));
    $("camPanelMonitor").hidden = tab !== "monitor";
    $("camPanelLocal").hidden = tab !== "local";
    $("camPanelIp").hidden = tab !== "ip";
  });
});

/* ───────── autenticação ───────── */
function saveSession(token, user) {
  state.token = token;
  state.user = user;
  localStorage.setItem("evo_token", token);
  localStorage.setItem("evo_user", JSON.stringify(user));
}
function clearSession() {
  state.token = null;
  state.user = null;
  localStorage.removeItem("evo_token");
  localStorage.removeItem("evo_user");
}
function renderAuthUI() {
  const logged = !!state.token;
  $("operatorName").textContent = logged && state.user ? state.user.username.toUpperCase() : "ANÔNIMO";
  $("loginBtn").textContent = logged ? "SAIR" : "ENTRAR";
  $("aiLocked").hidden = logged;
  $("aiBody").hidden = !logged;
  if (logged) refreshDiagnostics();
}
const openLogin = () => { $("loginModal").hidden = false; $("loginUser").focus(); };
const closeLogin = () => { $("loginModal").hidden = true; $("loginError").hidden = true; };

$("loginBtn").addEventListener("click", () => (state.token ? (clearSession(), renderAuthUI(), toast("Sessão encerrada.")) : openLogin()));
$("aiUnlockBtn").addEventListener("click", openLogin);
$("loginCancel").addEventListener("click", closeLogin);
$("loginModal").addEventListener("click", (e) => { if (e.target.id === "loginModal") closeLogin(); });

$("loginForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const username = $("loginUser").value.trim();
  const password = $("loginPass").value;
  try {
    const resp = await api("/auth/login", { method: "POST", body: { username, password } });
    if (!resp.ok) {
      const err = $("loginError");
      err.textContent = resp.status === 401 ? "Usuário ou senha incorretos." : `Erro ${resp.status} ao autenticar.`;
      err.hidden = false;
      return;
    }
    const data = await resp.json();
    saveSession(data.access_token, data.user);
    closeLogin();
    renderAuthUI();
    toast(`Bem-vindo, ${data.user.username}.`);
  } catch (err) {
    const el = $("loginError");
    el.textContent = "API inacessível. Verifique se o backend está rodando.";
    el.hidden = false;
  }
});

/* ───────── diagnóstico IA ───────── */
async function refreshDiagnostics() {
  if (!state.token) return;
  try {
    const [statsResp, latestResp] = await Promise.all([
      api("/diagnostics/stats", { auth: true }),
      api("/diagnostics/latest", { auth: true }),
    ]);
    if (statsResp.ok) {
      const s = await statsResp.json();
      $("aiErrors").textContent = s.errors_in_window ?? 0;
      $("aiLimit").textContent = s.burst_limit ?? "—";
      $("aiProviderTag").textContent = String(s.ai_provider || "local").toUpperCase();
    }
    if (latestResp.ok) {
      const d = await latestResp.json();
      const sev = (d.severidade || "media").toLowerCase();
      $("aiSev").textContent = sev.toUpperCase();
      $("aiSev").className = `ai-sev ai-sev--${sev}`;
      $("aiConf").textContent = `CONFIANÇA ${Math.round((d.confidence || 0) * 100)}%`;
      $("aiDiag").textContent = d.diagnostico || "—";
      $("aiCause").textContent = d.causa_provavel || "—";
      $("aiRec").textContent = d.recomendacao || "—";
      $("aiProviderTag").textContent = `${String(d.provider || "").toUpperCase()} · ${fmtTime(d.timestamp)}`;
    } else if (latestResp.status === 404) {
      $("aiDiag").textContent = "Sem laudo registrado ainda. Use FORÇAR ANÁLISE.";
    }
  } catch (e) { /* mantém último estado */ }
}

$("forceAnalysisBtn").addEventListener("click", async () => {
  try {
    $("forceAnalysisBtn").disabled = true;
    const resp = await api(`/diagnostics/analyze?status_snapshot=${encodeURIComponent(state.status)}`, { method: "POST", auth: true });
    if (resp.ok) { toast("Análise concluída."); await refreshDiagnostics(); }
    else toast(`Falha na análise (${resp.status}).`, true);
  } catch (e) { toast("API inacessível.", true); }
  finally { $("forceAnalysisBtn").disabled = false; }
});

/* ───────── modal de confirmação ───────── */
const confirmModal = $("confirmModal");
function askConfirm(title, text, okLabel) {
  return new Promise((resolve) => {
    $("confirmTitle").textContent = title;
    $("confirmText").textContent = text;
    $("confirmOk").textContent = okLabel;
    confirmModal.hidden = false;
    state.pendingAction = resolve;
  });
}
$("confirmCancel").addEventListener("click", () => { confirmModal.hidden = true; state.pendingAction?.(false); });
$("confirmOk").addEventListener("click", () => { confirmModal.hidden = true; state.pendingAction?.(true); });

async function callSafetyEndpoint(path, okMsg) {
  try {
    const resp = await api(path, { method: "POST", body: { user_id: state.user?.username || "operator", reason: "Ação via painel web" } });
    if (resp.ok) toast(okMsg);
    else toast(`Falha (${resp.status}).`, true);
  } catch { toast("API inacessível.", true); }
  await refreshStatus();
}

$("estopBtn").addEventListener("click", async () => {
  const ok = await askConfirm("PARADA DE EMERGÊNCIA", "Isto desligará o relé da máquina via ESP32. O sistema NÃO religa sozinho. Confirmar a parada?", "PARAR AGORA");
  if (ok) await callSafetyEndpoint("/manual-stop", "Comando de parada enviado ao ESP32.");
});

$("resetBtn").addEventListener("click", async () => {
  const ok = await askConfirm("RESET DO SISTEMA", "Isto libera o latch de emergência do ESP32 e religa o relé. Confirme somente com a máquina segura.", "CONFIRMAR RESET");
  if (ok) await callSafetyEndpoint("/reset", "Reset enviado ao ESP32.");
});

/* ───────── poll loop ───────── */
async function poll() {
  if (document.hidden) return;
  await Promise.all([refreshStatus(), refreshTelemetry(), refreshEvents(), refreshTemperature()]);
}
setInterval(poll, REFRESH_MS);
window.addEventListener("resize", () => drawChart(state.telemetry));

/* ───────── init ───────── */
setCameraUI(null);
renderAuthUI();
poll();
