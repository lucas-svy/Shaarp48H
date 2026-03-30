"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import * as XLSX from "xlsx";

type Role = "user" | "assistant";

type Message = {
  role: Role;
  content: string;
};

type Exhibitor = {
  name?: string;
  booth?: string;
  profile_url?: string;
  categories?: string[];
  raw?: Record<string, unknown>;
};

type ApiResponse = {
  assistant?: string;
  scrape?: { exhibitors?: Exhibitor[] };
  error?: string;
  details?: string;
};

function downloadCSV(exhibitors: Exhibitor[]) {
  const headers = ["Nom", "Stand", "URL", "Catégories"];
  const rows = exhibitors.map((e) => [
    e.name ?? "",
    e.booth ?? "",
    e.profile_url ?? "",
    (e.categories ?? []).join("; "),
  ]);
  const csv = [headers, ...rows]
    .map((row) => row.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(","))
    .join("\n");
  const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "exposants.csv";
  a.click();
  URL.revokeObjectURL(url);
}

function downloadXLSX(exhibitors: Exhibitor[]) {
  const rows = exhibitors.map((e) => ({
    Nom: e.name ?? "",
    Stand: e.booth ?? "",
    URL: e.profile_url ?? "",
    Catégories: (e.categories ?? []).join("; "),
  }));
  const ws = XLSX.utils.json_to_sheet(rows);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Exposants");
  XLSX.writeFile(wb, "exposants.xlsx");
}

export default function Home() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [exhibitors, setExhibitors] = useState<Exhibitor[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const canSend = useMemo(() => input.trim().length > 0 && !loading, [input, loading]);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  async function sendMessage() {
    if (!canSend) return;

    const userMessage: Message = { role: "user", content: input.trim() };
    const nextMessages = [...messages, userMessage];
    setMessages(nextMessages);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: nextMessages }),
      });

      const json = (await res.json()) as ApiResponse;

      if (json.error) {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: `Erreur : ${json.error}\n${json.details ?? ""}` },
        ]);
      } else {
        setMessages((prev) => [
          ...prev,
          { role: "assistant", content: json.assistant ?? "" },
        ]);
        if (json.scrape?.exhibitors && json.scrape.exhibitors.length > 0) {
          setExhibitors(json.scrape.exhibitors);
        }
      }
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Erreur réseau : ${String(err)}` },
      ]);
    } finally {
      setLoading(false);
      textareaRef.current?.focus();
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  }

  return (
    <div className="flex flex-col h-screen bg-zinc-50 dark:bg-black font-sans">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-3 border-b border-black/[.08] dark:border-white/[.145] bg-white dark:bg-black shrink-0">
        <h1 className="text-base font-semibold text-black dark:text-zinc-50">
          Assistant Salons Professionnels
        </h1>
        {exhibitors.length > 0 && (
          <div className="flex gap-2">
            <button
              onClick={() => downloadCSV(exhibitors)}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-black/[.08] bg-white px-3 text-xs font-medium text-black transition-colors hover:bg-zinc-100 dark:border-white/[.145] dark:bg-zinc-900 dark:text-zinc-50 dark:hover:bg-zinc-800"
            >
              ⬇ CSV
            </button>
            <button
              onClick={() => downloadXLSX(exhibitors)}
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-black/[.08] bg-white px-3 text-xs font-medium text-black transition-colors hover:bg-zinc-100 dark:border-white/[.145] dark:bg-zinc-900 dark:text-zinc-50 dark:hover:bg-zinc-800"
            >
              ⬇ Excel
            </button>
          </div>
        )}
      </header>

      {/* Messages */}
      <main className="flex-1 overflow-y-auto px-4 py-6">
        <div className="mx-auto max-w-3xl flex flex-col gap-4">
          {messages.length === 0 && (
            <div className="text-center text-zinc-400 dark:text-zinc-600 text-sm mt-20">
              Donne-moi l&apos;URL d&apos;une page d&apos;exposants pour commencer.
              <br />
              <span className="text-xs">Ex : https://www.vivatechnology.com/exhibitors</span>
            </div>
          )}

          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm ${
                  msg.role === "user"
                    ? "bg-black text-white dark:bg-white dark:text-black rounded-br-sm"
                    : "bg-white dark:bg-zinc-900 text-black dark:text-zinc-50 border border-black/[.08] dark:border-white/[.145] rounded-bl-sm prose prose-sm max-w-none dark:prose-invert"
                }`}
              >
                {msg.role === "assistant" ? (
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      td: ({ children }) => (
                        <td className="px-4 py-2 border border-black/[.08] dark:border-white/[.145]">{children}</td>
                      ),
                      th: ({ children }) => (
                        <th className="px-4 py-2 border border-black/[.08] dark:border-white/[.145] font-semibold text-left">{children}</th>
                      ),
                      table: ({ children }) => (
                        <table className="border-collapse w-full my-4">{children}</table>
                      ),
                    }}
                  >
                    {msg.content}
                  </ReactMarkdown>
                ) : (
                  <span className="whitespace-pre-wrap">{msg.content}</span>
                )}
              </div>
            </div>
          ))}

          {loading && (
            <div className="flex justify-start">
              <div className="bg-white dark:bg-zinc-900 border border-black/[.08] dark:border-white/[.145] rounded-2xl rounded-bl-sm px-4 py-3">
                <span className="flex gap-1 items-center text-zinc-400">
                  <span className="animate-bounce [animation-delay:0ms]">●</span>
                  <span className="animate-bounce [animation-delay:150ms]">●</span>
                  <span className="animate-bounce [animation-delay:300ms]">●</span>
                </span>
              </div>
            </div>
          )}

          <div ref={bottomRef} />
        </div>
      </main>

      {/* Input */}
      <footer className="px-4 py-4 border-t border-black/[.08] dark:border-white/[.145] bg-white dark:bg-black shrink-0">
        <div className="mx-auto max-w-3xl flex gap-3 items-end">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder="Écris un message… (Entrée pour envoyer, Shift+Entrée pour sauter une ligne)"
            className="flex-1 resize-none rounded-2xl border border-black/[.08] dark:border-white/[.145] bg-zinc-50 dark:bg-zinc-900 px-4 py-3 text-sm text-black dark:text-zinc-50 outline-none focus:border-black/30 dark:focus:border-white/30 transition-colors"
            style={{ maxHeight: "200px", overflowY: "auto" }}
          />
          <button
            onClick={sendMessage}
            disabled={!canSend}
            className="shrink-0 inline-flex h-11 w-11 items-center justify-center rounded-full bg-black dark:bg-white text-white dark:text-black transition-colors disabled:opacity-30 hover:bg-zinc-800 dark:hover:bg-zinc-200"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-5 h-5">
              <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z" />
            </svg>
          </button>
        </div>
      </footer>
    </div>
  );
}
