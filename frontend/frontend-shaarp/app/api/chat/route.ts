import { NextResponse } from "next/server";
import { spawn } from "node:child_process";
import path from "node:path";

export const runtime = "nodejs";

type ChatMessage = { role: "system" | "user" | "assistant"; content: string };

type ChatRequest = {
  messages?: ChatMessage[];
  message?: string;
  temperature?: number;
  max_tokens?: number;
  scrape?: {
    url?: string;
    spec?: string;
    headless?: boolean;
    timeout_ms?: number;
  };
};

function buildPythonCandidates(): Array<{ cmd: string; argsPrefix: string[] }> {
  const pythonBin = process.env.PYTHON_BIN;
  if (pythonBin) return [{ cmd: pythonBin, argsPrefix: [] }];

  if (process.platform === "win32") {
    // Try `python` first (common), then the Python launcher `py` (optional).
    return [
      { cmd: "python", argsPrefix: [] },
      { cmd: "py", argsPrefix: ["-3"] },
    ];
  }

  return [
    { cmd: "python3", argsPrefix: [] },
    { cmd: "python", argsPrefix: [] },
  ];
}

function getBackendDir(): string {
  // repo layout: frontend/frontend-shaarp (cwd) and backend/ at repo root
  return path.resolve(process.cwd(), "..", "..", "backend");
}

async function readRequestJson(req: Request): Promise<ChatRequest> {
  try {
    return (await req.json()) as ChatRequest;
  } catch {
    return {};
  }
}

async function runOpenAIChatJson(params: {
  backendDir: string;
  candidates: Array<{ cmd: string; argsPrefix: string[] }>;
  payload: Record<string, unknown>;
}): Promise<{ ok: true; exitCode: number; stdout: string; stderr: string } | { ok: false; response: Response }> {
  const { backendDir, candidates, payload } = params;
  let lastErr: unknown = null;

  const isMicrosoftStorePythonAlias = (stderrText: string) => {
    const s = stderrText.toLowerCase();
    return (
      s.includes("python was not found") &&
      s.includes("microsoft store")
    );
  };

  for (const { cmd, argsPrefix } of candidates) {
    let child: ReturnType<typeof spawn>;
    try {
      child = spawn(cmd, [...argsPrefix, "openai_chat.py", "chat-json"], {
        cwd: backendDir,
        env: { ...process.env },
        stdio: ["pipe", "pipe", "pipe"],
      });
    } catch (err) {
      lastErr = err;
      continue;
    }

    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];

    const result = await new Promise<
      { ok: true; exitCode: number; stdout: string; stderr: string } | { ok: false; err: unknown }
    >((resolve) => {
      child.once("error", (err) => resolve({ ok: false, err }));
      child.stdout!.on("data", (d: Buffer) => stdout.push(d));
      child.stderr!.on("data", (d: Buffer) => stderr.push(d));
      child.once("close", (code) =>
        resolve({
          ok: true,
          exitCode: typeof code === "number" ? code : 1,
          stdout: Buffer.concat(stdout).toString("utf-8"),
          stderr: Buffer.concat(stderr).toString("utf-8"),
        })
      );

      child.stdin!.write(JSON.stringify(payload));
      child.stdin!.end();
    });

    if (result.ok) {
      // If `python` is a Windows App Execution Alias stub (Microsoft Store),
      // it exits non-zero with a specific message. In that case, try the next candidate.
      if (result.exitCode !== 0 && isMicrosoftStorePythonAlias(result.stderr)) {
        lastErr = result.stderr;
        continue;
      }

      return result;
    }

    // If the executable isn't found (ENOENT), try next candidate.
    const errAny = result.err as any;
    if (errAny && typeof errAny === "object" && errAny.code === "ENOENT") {
      lastErr = result.err;
      continue;
    }

    return {
      ok: false,
      response: NextResponse.json(
        { error: "python_spawn_failed", details: String(result.err) },
        { status: 500 }
      ),
    };
  }

  return {
    ok: false,
    response: NextResponse.json(
      {
        error: "python_not_found",
        details: String(lastErr ?? "Python executable not found"),
        hint: "Set PYTHON_BIN to an absolute path (e.g. backend/.venv/Scripts/python.exe) or ensure python is on PATH.",
      },
      { status: 500 }
    ),
  };
}

export async function POST(req: Request) {
  const incoming = await readRequestJson(req);

  const payload: Record<string, unknown> = { ...incoming };

  if (!Array.isArray(incoming.messages) || incoming.messages.length === 0) {
    if (typeof incoming.message === "string" && incoming.message.trim()) {
      payload.messages = [{ role: "user", content: incoming.message.trim() } satisfies ChatMessage];
    }
  }

  if (!Array.isArray(payload.messages) || (payload.messages as unknown[]).length === 0) {
    return NextResponse.json(
      { error: "invalid_request", details: "Provide 'messages' (array) or 'message' (string)." },
      { status: 400 }
    );
  }

  const backendDir = getBackendDir();
  const candidates = buildPythonCandidates();
  const userText = (payload.messages as ChatMessage[])
  .filter(m => m.role === "user")
  .map(m => m.content)
  .join(" ");

// Match full URLs (https://...) or bare domains (www.example.com/path)
const rawUrlMatch = userText.match(/https?:\/\/[^\s]+/)
  ?? userText.match(/(?:www\.)[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:\/[^\s]*)?/);

// Clean trailing punctuation that may have been captured
const cleanedUrl = rawUrlMatch ? rawUrlMatch[0].replace(/[.,;!?)]+$/, "") : null;
const urlMatch = cleanedUrl
  ? [cleanedUrl.startsWith("http") ? cleanedUrl : `https://${cleanedUrl}`]
  : null;

if (urlMatch && !payload.scrape) {
  const url = urlMatch[0];

  // Known sites: use an existing spec (faster, no LLM generation needed)
  const specMap: Record<string, string> = {
    "mwcbarcelona.com": "specs/mwcbarcelona_exhibitors.json",
    "vivatechnology.com": "specs/vivatechnology.com_auto.json",
    "vancouver.websummit.com": "specs/vancouver.websummit.com_auto.json",
    // Add more known sites here
  };

  let spec: string | undefined;
  for (const [domain, specPath] of Object.entries(specMap)) {
    if (url.includes(domain)) {
      spec = specPath;
      break;
    }
  }

  // Always pass the URL — if no spec is found, openai_chat.py will auto-generate one
  payload.scrape = spec ? { url, spec } : { url };
}
  const result = await runOpenAIChatJson({ backendDir, candidates, payload });
  if (!result.ok) return result.response;

  if (result.exitCode !== 0) {
    return NextResponse.json(
      {
        error: "python_failed",
        exitCode: result.exitCode,
        details: result.stderr,
      },
      { status: 500 }
    );
  }

  const text = result.stdout.trim();
  try {
    return NextResponse.json(JSON.parse(text));
  } catch {
    return NextResponse.json(
      {
        error: "invalid_python_json",
        details: text,
      },
      { status: 502 }
    );
  }
}
