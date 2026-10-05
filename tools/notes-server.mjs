// Local server for the notes tracker: serves notes.html and saves every
// change to tools/notes.json, so notes live in a real file (backed up, and
// committable) rather than only in browser storage.
//
//   node tools/notes-server.mjs        -> http://localhost:4321
//
// No dependencies. Listens on localhost only, so nothing outside this
// machine can read or change the notes.

import { createServer } from "node:http";
import { readFile, rename, writeFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HOST = "127.0.0.1";
const PORT = Number(process.env.NOTES_PORT) || 4321;
const DIR = dirname(fileURLToPath(import.meta.url));
const PAGE = join(DIR, "notes.html");
const DATA = join(DIR, "notes.json");
const MAX_BODY = 5 * 1024 * 1024;

async function readNotes() {
  try {
    const data = JSON.parse(await readFile(DATA, "utf8"));
    return Array.isArray(data) ? data : data.notes ?? [];
  } catch (err) {
    if (err.code === "ENOENT") return [];
    throw err;
  }
}

async function writeNotes(notes) {
  // Write to a temp file then rename, so a crash mid-write can't leave a
  // half-written (corrupt) notes.json behind.
  const tmp = `${DATA}.tmp`;
  await writeFile(tmp, JSON.stringify({ savedAt: new Date().toISOString(), notes }, null, 2) + "\n", "utf8");
  await rename(tmp, DATA);
}

function send(res, status, body, type = "application/json; charset=utf-8") {
  res.writeHead(status, { "Content-Type": type, "Cache-Control": "no-store" });
  res.end(typeof body === "string" ? body : JSON.stringify(body));
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on("data", (c) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(new Error("Request too large"));
        req.destroy();
      } else chunks.push(c);
    });
    req.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

const server = createServer(async (req, res) => {
  const { pathname } = new URL(req.url, `http://${req.headers.host}`);
  try {
    if (req.method === "GET" && (pathname === "/" || pathname === "/notes.html")) {
      return send(res, 200, await readFile(PAGE, "utf8"), "text/html; charset=utf-8");
    }
    if (pathname === "/api/notes") {
      if (req.method === "GET") return send(res, 200, { notes: await readNotes() });
      if (req.method === "PUT") {
        const { notes } = JSON.parse(await readBody(req));
        if (!Array.isArray(notes)) return send(res, 400, { error: "Expected { notes: [...] }" });
        await writeNotes(notes);
        return send(res, 200, { saved: notes.length });
      }
      return send(res, 405, { error: "Method not allowed" });
    }
    send(res, 404, { error: "Not found" });
  } catch (err) {
    console.error(err);
    send(res, 500, { error: String(err.message || err) });
  }
});

server.on("error", (err) => {
  if (err.code === "EADDRINUSE") {
    console.error(`Port ${PORT} is in use -- is the notes server already running? (Or set NOTES_PORT.)`);
  } else console.error(err);
  process.exit(1);
});

server.listen(PORT, HOST, () => {
  console.log(`Notes: http://localhost:${PORT}  (saving to ${DATA})`);
});
