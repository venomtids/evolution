/* EVO·VISION — Modo DEMO.
   Quando a API FastAPI não responde (ex.: site hospedado estaticamente na Vercel,
   sem backend ao alcance), este módulo assume com telemetria 100% sintética,
   mantendo o HMI totalmente interativo. Identificado com o selo MODO DEMO. */
"use strict";

window.EVO_DEMO = (() => {
  let active = false;
  const t0 = Date.now();
  const nowS = () => (Date.now() - t0) / 1000;
  const iso = (offsetMs = 0) => new Date(Date.now() + offsetMs).toISOString();
  const jit = (amp) => (Math.random() - 0.5) * amp;

  // Ciclo de 150 s: NORMAL → ALERTA → CRÍTICO → recuperação
  function demoPhase() {
    const t = nowS() % 150;
    if (t < 78) {
      return { level: "NORMAL", mag: Math.max(0.15, 1.05 + 0.65 * Math.sin(t / 7) + jit(0.35)) };
    } else if (t < 108) {
      return { level: "ALERTA", mag: Math.max(1.9, 2.55 + 0.35 * Math.sin(t * 1.6) + jit(0.3)) };
    } else if (t < 113) {
      return { level: "CRITICO", mag: 5.0 + Math.abs(jit(1.6)) };
    }
    return { level: "NORMAL", mag: Math.max(0.2, 1.4 + jit(0.5)) };
  }

  const demoTemp = () => 37.6 + 2.1 * Math.sin(nowS() / 45) + jit(0.35);

  const CELLS = ["superior-esquerda", "superior-centro", "superior-direita",
                 "meio-esquerda", "centro", "meio-direita",
                 "inferior-esquerda", "inferior-centro", "inferior-direita"];
  const DIRS = ["→", "↘", "↓", "↙", "←", "↖", "↑", "↗"];
  const ANGLES = [0, 45, 90, 135, 180, 225, 270, 315];

  function gridFromMag(hotIdx, hotVal) {
    const g = [[0.004, 0.002, 0.003], [0.002, 0.006, 0.002], [0.003, 0.002, 0.005]];
    const r = Math.floor(hotIdx / 3), c = hotIdx % 3;
    g[r][c] = hotVal;
    const nb = [[r - 1, c], [r + 1, c], [r, c - 1], [r, c + 1]];
    nb.forEach(([rr, cc]) => {
      if (rr >= 0 && rr < 3 && cc >= 0 && cc < 3) g[rr][cc] = Math.max(g[rr][cc], hotVal * (0.28 + Math.random() * 0.12));
    });
    return g.map((row) => row.map((v) => Math.round((v + jit(0.004)) * 1000) / 1000));
  }

  function motionPayload() {
    const { level, mag } = demoPhase();
    const hot = Math.floor(nowS() / 6) % 9;
    const dirIdx = Math.floor(nowS() / 9) % 8;
    const hotMag = Math.round((mag * (0.55 + Math.random() * 0.25)) * 1000) / 1000;
    return {
      timestamp: iso(), state: level, magnitude: Math.round(mag * 1000) / 1000,
      region: CELLS[hot], direction: DIRS[dirIdx],
      hot_cell: { row: Math.floor(hot / 3), col: hot % 3 },
      hot_magnitude: hotMag, angle_deg: ANGLES[dirIdx],
      grid: gridFromMag(hot, hotMag),
      temperature: Math.round(demoTemp() * 100) / 100,
      age_seconds: 0.05, fresh: true, demo: true,
    };
  }

  function telemetryPoints() {
    const pts = [];
    const n = 150, step = 2.0; // 1 ponto / 2 s
    for (let i = 0; i < n; i++) {
      const t = nowS() - (n - i) * step;
      let v = 1.05 + 0.6 * Math.sin(t / 7) + 0.18 * Math.sin(t * 1.7 + 1) + Math.abs(jit(0.5));
      const cyc = t % 150;
      if (cyc > 80 && cyc < 115) v += 1.5 + Math.sin(t * 2) * 0.5; // janela ALERTA/CRÍTICO
      if (cyc > 103 && cyc < 108) v += 2.3;
      pts.push({ timestamp: iso(-(n - i) * step * 1000), value: Math.round(Math.max(0.05, v) * 1000) / 1000 });
    }
    return pts;
  }

  function demoEvents() {
    const mk = (dMin, status, value) => {
      const d = new Date(Date.now() - dMin * 60000);
      return {
        id: Math.floor(Math.random() * 1e6), timestamp: d.toISOString(),
        status, value, temperature: Math.round((37.5 + Math.random() * 2) * 10) / 10,
        source: "demo", frame_path: null,
      };
    };
    return [
      mk(3, "ALERTA", 2.41), mk(17, "NORMAL", 0.98), mk(41, "ALERTA", 2.73),
      mk(127, "CRITICO", 5.31), mk(233, "NORMAL", 1.12), mk(389, "ALERTA", 2.52),
      mk(620, "NORMAL", 0.87), mk(901, "CRITICO", 4.94), mk(1400, "NORMAL", 1.05),
    ].filter((e) => true);
  }

  function csvDownload(filter) {
    const rows = [["timestamp", "status", "value", "temperature", "source"]];
    demoEvents().filter((e) => !filter || e.status === filter)
      .forEach((e) => rows.push([e.timestamp, e.status, e.value, e.temperature, e.source]));
    const csv = rows.map((r) => r.join(",")).join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url; a.download = "evovision-telemetria-demo.csv";
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  function jsonResponse(obj, status = 200) {
    return new Response(JSON.stringify(obj), {
      status,
      headers: { "Content-Type": "application/json", "X-Evo-Demo": "1" },
    });
  }

  const demoStats = () => ({
    errors_in_window: 3, burst_limit: 5, ai_provider: "demo",
    window_minutes: 60, events_window: 41,
  });
  const demoLatest = () => ({
    severidade: "media",
    diagnostico: "Desalinhamento intermitente do eixo de alimentação com assinatura harmônica em 2× a rotação.",
    causa_provavel: "Folga incipiente no mancal do eixo (padrão harmônico de 2×/3× RPM).",
    recomendacao: "Inspecionar folga radial do mancal e reapertar parafusos de fixação. Refazer calibração da IR após manutenção.",
    confidence: 0.84, provider: "demo", timestamp: iso(),
  });

  function resolve(path, { method = "GET", body = null } = {}) {
    if (!active) { active = true; markDemo(); }
    const p = String(path).split("?")[0];
    const q = String(path).includes("?") ? String(path).split("?")[1] : "";
    const { level, mag } = demoPhase();
    const m = Math.round(mag * 1000) / 1000;

    if (p === "/status") return jsonResponse({
      current_status: level, last_value: m, last_update: iso(),
      esp32_connected: false, events_last_hour: 137, demo: true,
    });
    if (p === "/temperature") return jsonResponse({
      temperature: Math.round(demoTemp() * 100) / 100, source: "simulado (demo)",
      thresholds: { alerta: 60.0, critico: 75.0 }, timestamp: iso(), demo: true,
    });
    if (p === "/motion") return jsonResponse(motionPayload());
    if (p === "/telemetry") return jsonResponse({ points: telemetryPoints(), demo: true });
    if (p === "/events") {
      const status = (q.match(/status=([A-Z]+)/) || [null, ""])[1];
      const events = demoEvents().filter((e) => !status || e.status === status);
      return jsonResponse({ events, total: events.length, page: 1, page_size: 12, demo: true });
    }
    if (p === "/diagnostics/stats") return jsonResponse(demoStats());
    if (p === "/diagnostics/latest") return jsonResponse(demoLatest());
    if (p === "/diagnostics/analyze" && method === "POST") return jsonResponse({ ok: true, analysis_id: "demo-" + Date.now(), demo: true });
    if (p === "/auth/login" && method === "POST") {
      const username = (body && body.username) || "operador";
      return jsonResponse({ access_token: "demo-token-" + Date.now(), user: { username, role: "operador" }, demo: true });
    }
    if (p === "/manual-stop" && method === "POST") return jsonResponse({ success: true, action: "manual-stop", latched: true, demo: true });
    if (p === "/reset" && method === "POST") return jsonResponse({ success: true, action: "reset", latched: false, demo: true });
    if (p === "/health") return jsonResponse({ status: "demo", timestamp: iso() });
    return jsonResponse({ detail: "demo: endpoint não simulado", demo: true }, 404);
  }

  function markDemo() {
    const badge = document.getElementById("demoBadge");
    if (badge) badge.hidden = false;
    const apiState = document.getElementById("apiState");
    if (apiState) apiState.textContent = "MODO DEMO";
    const led = document.getElementById("apiLed");
    if (led) { led.classList.add("ok"); led.classList.remove("fail"); }
  }

  /* ───── feed sintético da câmera (canvas) ───── */
  let rafId = null;
  function connectDemoMonitor(canvas) {
    const ctx = canvas.getContext("2d");
    const draw = () => {
      rafId = requestAnimationFrame(draw);
      if (document.hidden) return;
      const w = canvas.clientWidth, h = canvas.clientHeight;
      if (!w || !h) return;
      const dpr = window.devicePixelRatio || 1;
      if (canvas.width !== w * dpr || canvas.height !== h * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const t = nowS();
      const { level, mag } = demoPhase();

      // fundo
      const bg = ctx.createLinearGradient(0, 0, 0, h);
      bg.addColorStop(0, "#0b1018"); bg.addColorStop(0.72, "#090d13"); bg.addColorStop(1, "#05070b");
      ctx.fillStyle = bg; ctx.fillRect(0, 0, w, h);
      // grade de chão
      ctx.strokeStyle = "rgba(90,120,160,.10)"; ctx.lineWidth = 1;
      for (let gx = 0; gx < w; gx += Math.max(40, w / 14)) { ctx.beginPath(); ctx.moveTo(gx, h * 0.55); ctx.lineTo(gx - w * 0.18, h); ctx.stroke(); }
      ctx.beginPath(); ctx.moveTo(0, h * 0.55); ctx.lineTo(w, h * 0.55); ctx.stroke();

      // esteira
      const beltY = h * 0.72, beltH = h * 0.07;
      ctx.fillStyle = "#121a26"; ctx.fillRect(0, beltY, w, beltH);
      ctx.fillStyle = "#1b2634"; ctx.fillRect(0, beltY + beltH - 3, w, 3);
      ctx.strokeStyle = "rgba(47,214,255,.28)"; ctx.lineWidth = 3;
      const chev = 46, off = (t * 90) % chev;
      for (let x = -chev + off; x < w + chev; x += chev) {
        ctx.beginPath(); ctx.moveTo(x, beltY + beltH - 5); ctx.lineTo(x + 14, beltY + beltH / 2); ctx.lineTo(x, beltY + 5); ctx.stroke();
      }

      // peça (hexágono) deslizando na esteira
      const partX = ((t * 90) % (w + 260)) - 130;
      const partY = beltY - 8, partR = 13;
      ctx.save(); ctx.translate(partX, partY); ctx.rotate(t * 0.6);
      ctx.beginPath();
      for (let i = 0; i < 6; i++) { const a = Math.PI / 3 * i; i ? ctx.lineTo(Math.cos(a) * partR, Math.sin(a) * partR) : ctx.moveTo(Math.cos(a) * partR, Math.sin(a) * partR); }
      ctx.closePath(); ctx.fillStyle = "#2fd6ff"; ctx.globalAlpha = 0.9; ctx.fill(); ctx.globalAlpha = 1;
      ctx.lineWidth = 2; ctx.strokeStyle = "#0b1018"; ctx.stroke(); ctx.restore();

      // braço robótico (base à direita)
      const baseX = w * 0.78, baseY = h * 0.55;
      const aimX = Math.min(Math.max(partX, w * 0.35), w * 0.72);
      const aimY = partY;
      const L1 = Math.hypot(aimX - baseX, aimY - baseY) * 0.52 + 20;
      const elbowX = (baseX + aimX) / 2 + 30, elbowY = (baseY + aimY) / 2 - L1 * 0.55;
      const seg = (x1, y1, x2, y2, wd, color) => { ctx.strokeStyle = color; ctx.lineWidth = wd; ctx.lineCap = "round"; ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); };
      ctx.fillStyle = "#141c29"; ctx.fillRect(baseX - 46, baseY + 6, 92, h * 0.17); // pedestal
      seg(baseX, baseY, elbowX, elbowY, 17, "#1f2b3d");
      seg(elbowX, elbowY, aimX, aimY, 12, "#263349");
      const joint = (x, y, r, c) => { ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fillStyle = c; ctx.fill(); };
      joint(baseX, baseY, 13, "#2fd6ff"); joint(elbowX, elbowY, 10, "#2fd6ff"); joint(aimX, aimY, 7, "#ffb000");
      // garra
      ctx.strokeStyle = "#ffb000"; ctx.lineWidth = 4;
      ctx.beginPath(); ctx.moveTo(aimX - 8, aimY + 2); ctx.lineTo(aimX - 4, aimY + 14); ctx.moveTo(aimX + 8, aimY + 2); ctx.lineTo(aimX + 4, aimY + 14); ctx.stroke();

      // moldura ROI
      const rx = w * 0.30, ry = h * 0.30, rw = w * 0.36, rh = h * 0.38, L = 22;
      ctx.strokeStyle = "rgba(46,229,157,.9)"; ctx.lineWidth = 2.5;
      const corner = (x, y, dx, dy) => { ctx.beginPath(); ctx.moveTo(x + dx * L, y); ctx.lineTo(x, y); ctx.lineTo(x, y + dy * L); ctx.stroke(); };
      corner(rx, ry, 1, 1); corner(rx + rw, ry, -1, 1); corner(rx, ry + rh, 1, -1); corner(rx + rw, ry + rh, -1, -1);
      ctx.font = "bold 11px ui-monospace, Menlo, Consolas, monospace"; ctx.fillStyle = "rgba(46,229,157,.9)";
      ctx.fillText("ROI", rx + 4, ry - 6);

      // varredura (scan)
      const sweepY = (t * 55) % h;
      const scan = ctx.createLinearGradient(0, sweepY - 30, 0, sweepY + 30);
      scan.addColorStop(0, "rgba(47,214,255,0)"); scan.addColorStop(0.5, "rgba(47,214,255,.10)"); scan.addColorStop(1, "rgba(47,214,255,0)");
      ctx.fillStyle = scan; ctx.fillRect(0, sweepY - 30, w, 60);

      // marca d'água + HUD
      ctx.font = "bold 54px system-ui, sans-serif"; ctx.textAlign = "center";
      ctx.fillStyle = "rgba(232,237,245,.06)"; ctx.fillText("DEMO", w / 2, h * 0.38); ctx.textAlign = "left";
      ctx.font = "12px ui-monospace, Menlo, Consolas, monospace";
      ctx.fillStyle = "#2ee59d"; ctx.fillText("FPS: 60", 12, 20);
      ctx.fillStyle = "#e8edf5"; ctx.fillText(`MAGNITUDE: ${mag.toFixed(3)}`, 12, 38);
      ctx.fillStyle = level === "NORMAL" ? "#2ee59d" : level === "ALERTA" ? "#ffb000" : "#ff3b30";
      ctx.fillText(`STATUS: ${level}`, 12, 56);
      ctx.fillStyle = "#7c8aa0"; ctx.fillText("SIMULAÇÃO SINTÉTICA", w - 172, h - 12);
    };
    draw();
  }
  function disconnectDemoMonitor() { if (rafId) cancelAnimationFrame(rafId); rafId = null; }

  return { resolve, markDemo, csvDownload, connectDemoMonitor, disconnectDemoMonitor, isActive: () => active };
})();
