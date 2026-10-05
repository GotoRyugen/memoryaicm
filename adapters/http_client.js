// Depuis Node/Deno/Bun ou un navigateur local : le service HTTP (`python -m memoryaicm serve`).
const BASE = "http://127.0.0.1:8765";

async function post(path, body) {
  const r = await fetch(BASE + path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return r.json();
}

export const memory = {
  remember: (user_text, facts, session = "js") => post("/remember", { user_text, facts, session }),
  recall: (query, k) => post("/recall", { query, k }),
  ingest: (source, content, session = "js") => post("/ingest", { source, content, session }),
  forget: (query, session = "js") => post("/forget", { query, session }),
  status: () => fetch(BASE + "/status").then((r) => r.json()),
};

// Exemple : le LLM (n'importe lequel) a extrait un fait du message de l'utilisateur
// await memory.remember("je m'appelle Camille", [{ txt: "s'appelle Camille", subject: "nom" }]);
// const { notes, prefs } = await memory.recall("comment je m'appelle ?");
