"use client";

import { useMemo, useState } from "react";

type ChatResponse = {
  assistant?: string;
  raw?: unknown;
  scrape?: { exhibitors?: unknown[] };
  error?: string;
  details?: string;
};

export default function Home() {
  const [message, setMessage] = useState("Bonjour ! Peux-tu me proposer des exposants IA ?");
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<ChatResponse | null>(null);

  const canSend = useMemo(() => message.trim().length > 0 && !loading, [message, loading]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSend) return;

    setLoading(true);
    setResponse(null);
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message }),
      });

      const json = (await res.json()) as ChatResponse;
      setResponse(json);
    } catch (err) {
      setResponse({ error: "client_failed", details: String(err) });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex flex-col flex-1 items-center justify-center bg-zinc-50 font-sans dark:bg-black">
      <main className="flex flex-1 w-full max-w-3xl flex-col items-center justify-center py-20 px-6 bg-white dark:bg-black sm:items-start">
        <h1 className="text-2xl font-semibold tracking-tight text-black dark:text-zinc-50">
          Chat (Python intégré)
        </h1>

        <form onSubmit={onSubmit} className="mt-6 w-full">
          <label className="block text-sm font-medium text-zinc-700 dark:text-zinc-300">
            Message
          </label>
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            rows={5}
            className="mt-2 w-full rounded-md border border-black/[.08] bg-white p-3 text-black outline-none dark:border-white/[.145] dark:bg-black dark:text-zinc-50"
          />
          <button
            type="submit"
            disabled={!canSend}
            className="mt-4 inline-flex h-10 items-center justify-center rounded-full bg-foreground px-5 text-background transition-colors disabled:opacity-50"
          >
            {loading ? "Envoi…" : "Envoyer"}
          </button>
        </form>

        <section className="mt-8 w-full">
          <h2 className="text-sm font-medium text-zinc-700 dark:text-zinc-300">Réponse</h2>
          <div className="mt-2 whitespace-pre-wrap rounded-md border border-black/[.08] bg-zinc-50 p-3 text-sm text-black dark:border-white/[.145] dark:bg-black dark:text-zinc-50">
            {response?.assistant ??
              (response?.error
                ? `Erreur: ${response.error}\n${response.details ?? ""}`
                : "(Aucune réponse pour l’instant)")}
          </div>
        </section>
      </main>
    </div>
  );
}
