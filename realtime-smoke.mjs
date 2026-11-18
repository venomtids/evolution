/**
 * Smoke test do Neon Realtime para a EVO·VISION.
 * Seleciona os 5 últimos eventos, assina a query viva (trusted backend code),
 * insere um evento temporário (source='smoke') e verifica que a mudança chega
 * pelo WebSocket. Remove o evento de teste ao final.
 *
 * Uso: node realtime-smoke.mjs
 */
process.loadEnvFile(".env");

import { createRealtime, rawSql } from "@neon/realtime/server";
import { neon } from "@neondatabase/serverless";

const url = process.env.NEON_REALTIME_URL;
const secret = process.env.NEON_REALTIME_SECRET;
const dbUrl = process.env.DATABASE_URL;
for (const [k, v] of Object.entries({ NEON_REALTIME_URL: url, NEON_REALTIME_SECRET: secret, DATABASE_URL: dbUrl })) {
  if (!v) {
    console.error(`FALTA ${k} no ambiente (.env)`);
    process.exit(2);
  }
}

const sql = neon(dbUrl);
const realtime = createRealtime({ url, secret, db: "neondb" });

const query = rawSql("SELECT id, timestamp, status, value, source FROM events ORDER BY id DESC LIMIT 5");

let gotUpdate = false;
let sub;
const timeout = setTimeout(() => {
  console.error("TIMEOUT: nenhuma mudança recebida em 12s");
  cleanup(1);
}, 12000);

async function cleanup(code) {
  clearTimeout(timeout);
  try {
    await sql`DELETE FROM events WHERE source = 'smoke'`;
    console.log("limpeza: evento 'smoke' removido");
  } catch (e) {
    console.error("limpeza falhou:", e.message);
  }
  try { await realtime.close(); } catch {}
  process.exit(code);
}

sub = await realtime.subscribe(query);
console.log("subscrito. Aguardando resultado inicial + mudança...");

sub.onChange((payload) => {
  const txt = JSON.stringify(payload);
  console.log("onChange:", txt.slice(0, 400));
  if (txt.includes('"smoke"')) {
    gotUpdate = true;
    console.log("REALTIME PASS: mudança do evento 'smoke' recebida via WebSocket");
    cleanup(0);
  }
});
sub.onStateChange?.((s) => console.log("state:", JSON.stringify(s)));
sub.onError?.((e) => console.error("erro:", e));

// dá tempo do resultado inicial chegar, depois insere o evento de teste
setTimeout(async () => {
  try {
    await sql`INSERT INTO events (timestamp, status, value, source) VALUES (now(), 'NORMAL', 0.001, 'smoke')`;
    console.log("evento 'smoke' inserido — aguardando propagação...");
  } catch (e) {
    console.error("insert falhou:", e.message);
    cleanup(1);
  }
}, 2500);
