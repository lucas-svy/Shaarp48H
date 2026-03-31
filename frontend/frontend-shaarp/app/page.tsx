"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Image from "next/image";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import * as XLSX from "xlsx";
import { motion, AnimatePresence } from "motion/react";

type Role = "user" | "assistant";
type Message = { role: Role; content: string };
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
type Conversation = { id: string; title: string; messages: Message[]; exhibitors: Exhibitor[] };
type ScrapeState = { url: string; spec?: string; offset: number; pageSize: number };

function downloadCSV(exhibitors: Exhibitor[]) {
  const headers = ["Nom", "Stand", "URL", "Catégories"];
  const rows = exhibitors.map((e) => [e.name ?? "", e.booth ?? "", e.profile_url ?? "", (e.categories ?? []).join("; ")]);
  const csv = [headers, ...rows].map((r) => r.map((c) => `"${String(c).replace(/"/g, '""')}"`).join(",")).join("\n");
  const blob = new Blob(["\uFEFF" + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a"); a.href = url; a.download = "exposants.csv"; a.click();
  URL.revokeObjectURL(url);
}

function downloadXLSX(exhibitors: Exhibitor[]) {
  const rows = exhibitors.map((e) => ({ Nom: e.name ?? "", Stand: e.booth ?? "", URL: e.profile_url ?? "", Catégories: (e.categories ?? []).join("; ") }));
  const ws = XLSX.utils.json_to_sheet(rows);
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, "Exposants");
  XLSX.writeFile(wb, "exposants.xlsx");
}

function HamburgerIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 20 20" fill="none">
      <path d="M2.5 5h15M2.5 10h15M2.5 15h15" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" width="14" height="14">
      <path d="M3.478 2.405a.75.75 0 00-.926.94l2.432 7.905H13.5a.75.75 0 010 1.5H4.984l-2.432 7.905a.75.75 0 00.926.94 60.519 60.519 0 0018.445-8.986.75.75 0 000-1.218A60.517 60.517 0 003.478 2.405z" />
    </svg>
  );
}

/** Counts up every second while loading */
function useElapsedTime(active: boolean) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!active) { setElapsed(0); return; }
    setElapsed(0);
    const id = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [active]);
  return elapsed;
}

function formatElapsed(s: number) {
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m${s % 60 > 0 ? ` ${s % 60}s` : ""}`;
}

export default function Home() {
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [exhibitors, setExhibitors] = useState<Exhibitor[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [statusMsg, setStatusMsg] = useState("");
  const [scrapeState, setScrapeState] = useState<ScrapeState | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const elapsed = useElapsedTime(loading);

  const canSend = useMemo(() => input.trim().length > 0 && !loading, [input, loading]);
  const hasMessages = messages.length > 0;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  function newConversation() {
    setMessages([]);
    setExhibitors([]);
    setInput("");
    setActiveId(null);
    setScrapeState(null);
    textareaRef.current?.focus();
  }

  function loadConversation(conv: Conversation) {
    setMessages(conv.messages);
    setExhibitors(conv.exhibitors);
    setActiveId(conv.id);
  }

  async function loadMore() {
    if (!scrapeState || loading) return;
    const nextOffset = scrapeState.offset + scrapeState.pageSize;
    const label = `Exposants ${nextOffset + 1} à ${nextOffset + scrapeState.pageSize}`;
    await sendMessage({ overrideContent: `Affiche les exposants suivants (${label})`, overrideScrape: { url: scrapeState.url, spec: scrapeState.spec, offset: nextOffset, limit: scrapeState.pageSize }, isLoadMore: true });
  }

  async function sendMessage(opts?: { overrideContent?: string; overrideScrape?: Record<string, unknown>; isLoadMore?: boolean }) {
    const content = opts?.overrideContent ?? input.trim();
    if (!content || loading) return;
    const userMessage: Message = { role: "user", content };
    const nextMessages = [...messages, userMessage];
    setMessages(nextMessages);
    if (!opts?.overrideContent) setInput("");
    setLoading(true);
    setStatusMsg("");

    try {
      const bodyPayload: Record<string, unknown> = { messages: nextMessages };
      if (opts?.overrideScrape) bodyPayload.scrape = opts.overrideScrape;

      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(bodyPayload),
      });

      if (!res.body) throw new Error("No response body");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      let finalResult: ApiResponse | null = null;

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.type === "status") {
              setStatusMsg(data.msg);
            } else if (data.type === "result") {
              finalResult = data as ApiResponse;
            } else if (data.type === "error") {
              finalResult = { error: data.error, details: data.details };
            }
          } catch { /* incomplete chunk */ }
        }
      }

      const json = finalResult ?? { error: "no_response" };
      const assistantContent = json.error
        ? `Erreur : ${json.error}\n${json.details ?? ""}`
        : (json.assistant ?? "");
      const finalMessages: Message[] = [...nextMessages, { role: "assistant", content: assistantContent }];
      setMessages(finalMessages);

      // Accumulate exhibitors on load-more, replace otherwise
      const freshExhibitors = json.scrape?.exhibitors ?? [];
      let newExhibitors: Exhibitor[];
      if (opts?.isLoadMore && freshExhibitors.length > 0) {
        newExhibitors = [...exhibitors, ...freshExhibitors];
      } else if (freshExhibitors.length > 0) {
        newExhibitors = freshExhibitors;
      } else {
        newExhibitors = exhibitors;
      }
      if (freshExhibitors.length > 0) setExhibitors(newExhibitors);

      // Update scrape state for pagination
      if (freshExhibitors.length > 0) {
        if (opts?.overrideScrape) {
          // Load-more: update offset to the one we just used
          setScrapeState({
            url: String(opts.overrideScrape.url ?? ""),
            spec: opts.overrideScrape.spec ? String(opts.overrideScrape.spec) : undefined,
            offset: Number(opts.overrideScrape.offset ?? 0),
            pageSize: Number(opts.overrideScrape.limit ?? 50),
          });
        } else {
          // First scrape: detect URL from user message, offset starts at 0
          const urlMatch = content.match(/https?:\/\/[^\s]+/) ??
            content.match(/(?:www\.)[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:\/[^\s]*)?/);
          const cleanedUrl = urlMatch ? urlMatch[0].replace(/[.,;!?)]+$/, "") : null;
          const detectedUrl = cleanedUrl
            ? cleanedUrl.startsWith("http") ? cleanedUrl : `https://${cleanedUrl}`
            : null;
          if (detectedUrl) {
            setScrapeState({ url: detectedUrl, offset: 0, pageSize: 50 });
          }
        }
      }

      const title = userMessage.content.slice(0, 50) + (userMessage.content.length > 50 ? "…" : "");
      if (activeId) {
        setConversations((prev) => prev.map((c) => c.id === activeId ? { ...c, messages: finalMessages, exhibitors: newExhibitors } : c));
      } else {
        const id = Date.now().toString();
        setActiveId(id);
        setConversations((prev) => [{ id, title, messages: finalMessages, exhibitors: newExhibitors }, ...prev]);
      }
    } catch (err) {
      setMessages((prev) => [...prev, { role: "assistant", content: `Erreur réseau : ${String(err)}` }]);
    } finally {
      setLoading(false);
      setStatusMsg("");
      textareaRef.current?.focus();
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  }

  return (
    <div className="flex h-screen bg-[#F4F4F4] font-sans overflow-hidden">

      {/* ── Sidebar ── */}
      <motion.aside
        animate={{ width: sidebarOpen ? 280 : 60 }}
        transition={{ duration: 0.25, ease: "easeInOut" }}
        className="flex flex-col bg-white border-r border-gray-200 shrink-0 overflow-hidden"
      >
        {/* Header */}
        <div className="flex items-center h-14 px-3 border-b border-gray-100 shrink-0">
          <AnimatePresence mode="wait">
            {sidebarOpen ? (
              <motion.div
                key="open"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
                className="flex items-center w-full"
              >
                <Image
                  src="/assets/images/SHAARP_LOGO_PRINCIPAL_COULEUR1.png"
                  alt="Shaarp"
                  width={90}
                  height={28}
                  className="object-contain"
                  priority
                />
                <div className="flex-1" />
                <button
                  onClick={() => setSidebarOpen(false)}
                  className="flex items-center justify-center w-8 h-8 rounded-lg text-gray-400 hover:bg-gray-100 transition-colors"
                >
                  <HamburgerIcon />
                </button>
              </motion.div>
            ) : (
              <motion.button
                key="closed"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
                onClick={() => setSidebarOpen(true)}
                className="flex items-center justify-center w-8 h-8 rounded-lg text-gray-400 hover:bg-gray-100 transition-colors mx-auto"
              >
                <HamburgerIcon />
              </motion.button>
            )}
          </AnimatePresence>
        </div>

        {/* New conversation */}
        <div className="px-3 pt-4 pb-2 shrink-0">
          <AnimatePresence mode="wait">
            {sidebarOpen ? (
              <motion.button
                key="btn-open"
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -4 }}
                transition={{ duration: 0.15 }}
                onClick={newConversation}
                className="flex items-center gap-2 w-full px-4 py-2.5 rounded-full bg-gray-900 text-white text-sm font-medium hover:bg-gray-700 transition-colors"
              >
                <span className="text-lg leading-none">+</span>
                NOUVELLE CONVERSATION
              </motion.button>
            ) : (
              <motion.button
                key="btn-closed"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.15 }}
                onClick={newConversation}
                className="flex items-center justify-center w-9 h-9 rounded-full bg-gray-900 text-white hover:bg-gray-700 transition-colors mx-auto"
              >
                <span className="text-lg leading-none">+</span>
              </motion.button>
            )}
          </AnimatePresence>
        </div>

        {/* Recent conversations */}
        <AnimatePresence>
          {sidebarOpen && conversations.length > 0 && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="flex flex-col flex-1 overflow-y-auto px-3 pb-4"
            >
              <p className="text-[11px] font-semibold text-gray-400 uppercase tracking-wider px-1 mt-3 mb-2">Récents</p>
              <div className="flex flex-col gap-1">
                {conversations.map((conv) => (
                  <motion.button
                    key={conv.id}
                    initial={{ opacity: 0, x: -8 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ duration: 0.2 }}
                    onClick={() => loadConversation(conv)}
                    className={`text-left px-3 py-2 rounded-lg text-xs text-gray-600 truncate transition-colors hover:bg-gray-100 ${activeId === conv.id ? "bg-gray-100 font-medium" : ""}`}
                  >
                    {conv.title}
                  </motion.button>
                ))}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.aside>

      {/* ── Main ── */}
      <div className="flex flex-col flex-1 min-w-0">

        {/* Download buttons */}
        <AnimatePresence>
          {exhibitors.length > 0 && (
            <motion.div
              initial={{ opacity: 0, y: -8 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8 }}
              transition={{ duration: 0.2 }}
              className="flex justify-end gap-2 px-6 pt-4 shrink-0"
            >
              <button onClick={() => downloadCSV(exhibitors)} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-gray-200 bg-white px-3 text-xs font-medium text-gray-700 hover:bg-gray-50 transition-colors">⬇ CSV</button>
              <button onClick={() => downloadXLSX(exhibitors)} className="inline-flex h-8 items-center gap-1.5 rounded-md border border-gray-200 bg-white px-3 text-xs font-medium text-gray-700 hover:bg-gray-50 transition-colors">⬇ Excel</button>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Messages / Empty state */}
        <main className="flex-1 overflow-y-auto">
          <AnimatePresence mode="wait">
            {!hasMessages ? (
              <motion.div
                key="empty"
                initial={{ opacity: 0, y: 16 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -16 }}
                transition={{ duration: 0.3, ease: "easeOut" }}
                className="flex flex-col items-center justify-center h-full pb-32"
              >
                <h1 className="text-5xl font-bold text-gray-900 tracking-tight text-center">
                  Exhibition Scraper Agent
                </h1>
              </motion.div>
            ) : (
              <motion.div
                key="chat"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ duration: 0.2 }}
                className="py-8 px-6"
              >
                <div className="mx-auto max-w-3xl flex flex-col gap-6">
                  <AnimatePresence initial={false}>
                    {messages.map((msg, i) => (
                      <motion.div
                        key={i}
                        initial={{ opacity: 0, y: 12 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ duration: 0.25, ease: "easeOut" }}
                        className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
                      >
                        {msg.role === "user" ? (
                          <div className="max-w-[75%] bg-gray-200 text-gray-900 rounded-2xl rounded-tr-sm px-4 py-3 text-sm whitespace-pre-wrap">
                            {msg.content}
                          </div>
                        ) : (
                          <div className="max-w-[85%] text-gray-800 text-sm prose prose-sm max-w-none">
                            <ReactMarkdown
                              remarkPlugins={[remarkGfm]}
                              components={{
                                td: ({ children }) => <td className="px-4 py-2 border border-gray-200">{children}</td>,
                                th: ({ children }) => <th className="px-4 py-2 border border-gray-200 font-semibold text-left bg-gray-50">{children}</th>,
                                table: ({ children }) => <table className="border-collapse w-full my-4 text-sm">{children}</table>,
                              }}
                            >
                              {msg.content}
                            </ReactMarkdown>
                          </div>
                        )}
                      </motion.div>
                    ))}
                  </AnimatePresence>

                  {/* Load more button */}
                  <AnimatePresence>
                    {scrapeState && !loading && exhibitors.length > 0 && (
                      <motion.div
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: 8 }}
                        transition={{ duration: 0.2 }}
                        className="flex justify-start"
                      >
                        <button
                          onClick={loadMore}
                          className="inline-flex items-center gap-2 px-4 py-2 rounded-full border border-gray-300 bg-white text-xs font-medium text-gray-600 hover:bg-gray-50 hover:border-gray-400 transition-colors shadow-sm"
                        >
                          <span>↓</span>
                          Charger les 50 suivants
                        </button>
                      </motion.div>
                    )}
                  </AnimatePresence>

                  {/* Loading indicator with elapsed timer + live status */}
                  <AnimatePresence>
                    {loading && (
                      <motion.div
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: 8 }}
                        transition={{ duration: 0.2 }}
                        className="flex justify-start"
                      >
                        <div className="flex flex-col gap-1.5">
                          {/* Dots + timer */}
                          <div className="flex items-center gap-3">
                            <div className="flex gap-1 items-center">
                              <span className="w-2 h-2 rounded-full bg-gray-400 animate-bounce [animation-delay:0ms]" />
                              <span className="w-2 h-2 rounded-full bg-gray-400 animate-bounce [animation-delay:150ms]" />
                              <span className="w-2 h-2 rounded-full bg-gray-400 animate-bounce [animation-delay:300ms]" />
                            </div>
                            <motion.span
                              key={elapsed}
                              initial={{ opacity: 0, scale: 0.85 }}
                              animate={{ opacity: 1, scale: 1 }}
                              transition={{ duration: 0.15 }}
                              className="text-xs text-gray-400 tabular-nums"
                            >
                              {formatElapsed(elapsed)}
                            </motion.span>
                          </div>
                          {/* Live status message */}
                          <AnimatePresence mode="wait">
                            {statusMsg && (
                              <motion.p
                                key={statusMsg}
                                initial={{ opacity: 0, x: -6 }}
                                animate={{ opacity: 1, x: 0 }}
                                exit={{ opacity: 0 }}
                                transition={{ duration: 0.15 }}
                                className="text-xs text-gray-400 italic max-w-sm"
                              >
                                {statusMsg}
                              </motion.p>
                            )}
                          </AnimatePresence>
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>

                  <div ref={bottomRef} />
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </main>

        {/* ── Input ── */}
        <div className="px-6 pb-8 shrink-0">
          <div className="mx-auto max-w-3xl">
            <motion.div
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.3, ease: "easeOut" }}
              className="flex items-center gap-2 bg-white rounded-2xl border border-gray-200 px-4 py-2.5 shadow-sm"
            >
              <textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                rows={1}
                placeholder="Quelle est votre demande ?"
                className="flex-1 resize-none bg-transparent text-sm text-gray-800 placeholder-gray-400 outline-none leading-5"
                style={{ maxHeight: "120px", overflowY: "auto" }}
              />
              <button
                onClick={sendMessage}
                disabled={!canSend}
                className="shrink-0 flex items-center justify-center w-7 h-7 rounded-full bg-gray-900 text-white transition-colors disabled:opacity-25 hover:enabled:bg-gray-700"
              >
                <SendIcon />
              </button>
            </motion.div>
          </div>
        </div>
      </div>
    </div>
  );
}
