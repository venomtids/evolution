import { neon } from "@neondatabase/serverless";

// Função de sanidade do stack Neon da EVO·VISION:
// Function (Node) -> Postgres (Lakebase) — prova o caminho serverless.
// NÃO expõe dados sensíveis: apenas agregados públicos + último status da máquina.
export default async function hello(): Promise<Response> {
  const databaseUrl = process.env.DATABASE_URL;
  if (!databaseUrl) {
    return new Response(
      JSON.stringify({ error: "DATABASE_URL ausente no runtime da Function" }),
      { status: 503, headers: { "content-type": "application/json" } }
    );
  }
  try {
    const sql = neon(databaseUrl);
    const rows = await sql`
      SELECT
        COUNT(*)::int AS total_eventos,
        (SELECT status FROM events ORDER BY id DESC LIMIT 1) AS ultimo_status,
        (SELECT timestamp FROM events ORDER BY id DESC LIMIT 1) AS ultimo_evento
      FROM events
    `;
    return new Response(
      JSON.stringify({ service: "evo-vision", ok: true, ...rows[0] }),
      { status: 200, headers: { "content-type": "application/json", "cache-control": "no-store" } }
    );
  } catch (err) {
    return new Response(
      JSON.stringify({ error: "consulta ao Postgres falhou", detail: String(err) }),
      { status: 500, headers: { "content-type": "application/json" } }
    );
  }
}
