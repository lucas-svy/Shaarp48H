import { spawn } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

export const runtime = "nodejs";

const LOG_FILE = path.resolve(process.cwd(), "..", "..", "backend", "logs", "frontend.log");

function writeLog(level: "INFO" | "WARN" | "ERROR", msg: string) {
  const line = `${new Date().toISOString()} [${level}] ${msg}\n`;
  try {
    fs.mkdirSync(path.dirname(LOG_FILE), { recursive: true });
    fs.appendFileSync(LOG_FILE, line, "utf-8");
  } catch { /* non-blocking */ }
}

type ChatMessage = { role: "system" | "user" | "assistant"; content: string };

type ChatRequest = {
  messages?: ChatMessage[];
  message?: string;
  temperature?: number;
  max_tokens?: number;
  scrape?: { url?: string; spec?: string; headless?: boolean; timeout_ms?: number };
};

function buildPythonCandidates(): Array<{ cmd: string; argsPrefix: string[] }> {
  const pythonBin = process.env.PYTHON_BIN;
  if (pythonBin) return [{ cmd: pythonBin, argsPrefix: [] }];
  if (process.platform === "win32") return [{ cmd: "python", argsPrefix: [] }, { cmd: "py", argsPrefix: ["-3"] }];
  return [{ cmd: "python3", argsPrefix: [] }, { cmd: "python", argsPrefix: [] }];
}

function getBackendDir(): string {
  return path.resolve(process.cwd(), "..", "..", "backend");
}

async function readRequestJson(req: Request): Promise<ChatRequest> {
  try { return (await req.json()) as ChatRequest; } catch { return {}; }
}

const isMicrosoftStoreAlias = (s: string) =>
  s.toLowerCase().includes("python was not found") && s.toLowerCase().includes("microsoft store");

export async function POST(req: Request) {
  const incoming = await readRequestJson(req);
  const payload: Record<string, unknown> = { ...incoming };

  if (!Array.isArray(incoming.messages) || incoming.messages.length === 0) {
    if (typeof incoming.message === "string" && incoming.message.trim()) {
      payload.messages = [{ role: "user", content: incoming.message.trim() } satisfies ChatMessage];
    }
  }

  if (!Array.isArray(payload.messages) || (payload.messages as unknown[]).length === 0) {
    return new Response(
      `data: ${JSON.stringify({ type: "error", error: "invalid_request" })}\n\n`,
      { status: 400, headers: { "Content-Type": "text/event-stream" } }
    );
  }

  // URL detection — trigger scraping
  const userText = (payload.messages as ChatMessage[])
    .filter((m) => m.role === "user")
    .map((m) => m.content)
    .join(" ");

  const rawUrlMatch =
    userText.match(/https?:\/\/[^\s]+/) ??
    userText.match(/(?:www\.)[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:\/[^\s]*)?/);
  const cleanedUrl = rawUrlMatch ? rawUrlMatch[0].replace(/[.,;!?)]+$/, "") : null;
  const detectedUrl = cleanedUrl
    ? cleanedUrl.startsWith("http") ? cleanedUrl : `https://${cleanedUrl}`
    : null;

  if (detectedUrl) writeLog("INFO", `URL detected: ${detectedUrl}`);

  if (detectedUrl && !payload.scrape) {
    const specMap: Record<string, string> = {
      "mwcbarcelona.com": "specs/mwcbarcelona_exhibitors.json",
      "vivatechnology.com": "specs/vivatechnology.com_auto.json",
      "vivatech.com": "specs/vivatechnology.com_auto.json",
      "vancouver.websummit.com": "specs/vancouver.websummit.com_auto.json",
    };
    let spec: string | undefined;
    for (const [domain, specPath] of Object.entries(specMap)) {
      if (detectedUrl.includes(domain)) { spec = specPath; break; }
    }
    // Preserve offset if already in payload (load-more requests), default to 0
    const existingOffset = (payload.scrape as Record<string, unknown> | undefined)?.offset ?? 0;
    payload.scrape = spec
      ? { url: detectedUrl, spec, limit: 50, offset: existingOffset }
      : { url: detectedUrl, limit: 50, offset: existingOffset };
  }

  const backendDir = getBackendDir();
  const candidates = buildPythonCandidates();
  const encoder = new TextEncoder();

  const stream = new ReadableStream({
    async start(controller) {
      function send(data: object) {
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(data)}\n\n`));
      }

      let spawned = false;

      for (const { cmd, argsPrefix } of candidates) {
        let child: ReturnType<typeof spawn>;
        try {
          child = spawn(cmd, [...argsPrefix, "openai_chat.py", "chat-json"], {
            cwd: backendDir,
            env: { ...process.env },
            stdio: ["pipe", "pipe", "pipe"],
          });
        } catch {
          continue;
        }

        const stdoutChunks: Buffer[] = [];
        let stderrBuf = "";
        let spawnError = false;

        await new Promise<void>((resolve) => {
          child.once("error", (err: NodeJS.ErrnoException) => {
            if (err.code !== "ENOENT") send({ type: "error", error: String(err) });
            spawnError = true;
            resolve();
          });

          child.stderr!.on("data", (chunk: Buffer) => {
            stderrBuf += chunk.toString("utf-8");
            // Parse complete JSON lines and forward as SSE status events
            const lines = stderrBuf.split("\n");
            stderrBuf = lines.pop() ?? "";
            for (const line of lines) {
              if (!line.trim()) continue;
              try {
                const parsed = JSON.parse(line);
                if (parsed.type === "status") send({ type: "status", msg: parsed.msg });
              } catch { /* non-JSON stderr line, ignore */ }
            }
          });

          child.stdout!.on("data", (d: Buffer) => stdoutChunks.push(d));

          child.once("close", (code) => {
            if (spawnError) { resolve(); return; }

            // Check for Microsoft Store Python alias
            if (code !== 0 && isMicrosoftStoreAlias(stderrBuf)) { resolve(); return; }

            const stdout = Buffer.concat(stdoutChunks).toString("utf-8").trim();

            if (code !== 0) {
              writeLog("ERROR", `Python exited ${code}: ${stderrBuf.slice(0, 300)}`);
              send({ type: "error", error: "python_failed", details: stderrBuf });
            } else {
              try {
                const result = JSON.parse(stdout);
                const scrapeCount = (result.scrape?.exhibitors ?? []).length;
                writeLog("INFO", `Request OK scrape_count=${scrapeCount}`);
                send({ type: "result", ...result });
              } catch {
                writeLog("WARN", `Invalid JSON from Python: ${stdout.slice(0, 200)}`);
                send({ type: "error", error: "invalid_json", details: stdout.slice(0, 500) });
              }
            }
            resolve();
          });

          child.stdin!.write(JSON.stringify(payload));
          child.stdin!.end();
        });

        if (!spawnError) { spawned = true; break; }
      }

      if (!spawned) {
        send({ type: "error", error: "python_not_found" });
      }

      controller.close();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Connection": "keep-alive",
    },
  });
}
