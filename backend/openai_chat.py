"""OpenAI ChatGPT client (Chat Completions) + optional salon scraping enrichment.

This script is designed to be easily integrated later into a Next.js web app:
- It can be imported as a module and called via functions.
- It also supports a JSON-in / JSON-out mode for subprocess integration.

Environment:
- Reads API_KEY, API_URL, MODEL from .env (via python-dotenv)

Examples:
  python openai_chat.py chat --message "Trouve des exposants IA" \
    --scrape-url "https://example.com/exhibitors" --scrape-spec specs/example_exhibitors_spec.json

  echo '{"messages":[{"role":"user","content":"Hello"}]}' | python openai_chat.py chat-json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Force UTF-8 output on Windows (avoids CP1252 UnicodeEncodeError with non-latin chars)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
from dataclasses import asdict
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from salon_scraper import Exhibitor, ScrapeSpec, StatusCallback, analyze_page, load_spec, load_spec_from_dict, scrape_exhibitors, scrape_hybrid


def _emit_status(msg: str) -> None:
    """Write a structured status line to stderr — streamed live to the frontend."""
    print(json.dumps({"type": "status", "msg": msg}), file=sys.stderr, flush=True)


class OpenAIClientError(RuntimeError):
    pass


def _load_env() -> Dict[str, str]:
    # Load .env from current working directory by default.
    load_dotenv(override=False)

    api_key = os.getenv("API_KEY")
    api_url = os.getenv("API_URL", "https://api.openai.com/v1/chat/completions")
    model = os.getenv("MODEL", "gpt-4.1")

    # .env values are sometimes written with surrounding quotes and/or spaces.
    def _clean(v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip()
        if len(v) >= 2 and ((v[0] == v[-1] == "\"") or (v[0] == v[-1] == "'")):
            v = v[1:-1]
        return v.strip() or None

    api_key = _clean(api_key)
    api_url = _clean(api_url) or "https://api.openai.com/v1/chat/completions"
    model = _clean(model) or "gpt-4.1"

    if not api_key:
        raise OpenAIClientError("Missing API_KEY in environment/.env")

    return {"api_key": api_key, "api_url": api_url, "model": model}


def chat_completions(
    *,
    messages: List[Dict[str, Any]],
    model: str,
    api_key: str,
    api_url: str,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    timeout_s: int = 60,
) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = int(max_tokens)

    resp = requests.post(api_url, headers=headers, json=payload, timeout=timeout_s)
    if resp.status_code >= 400:
        raise OpenAIClientError(f"OpenAI API error {resp.status_code}: {resp.text}")

    data = resp.json()
    return data


def _extract_assistant_text(chat_response: Dict[str, Any]) -> str:
    # Chat Completions schema: choices[0].message.content
    try:
        return chat_response["choices"][0]["message"]["content"]
    except Exception:
        return json.dumps(chat_response, ensure_ascii=False)


def _format_exhibitors_markdown(exhibitors: List[Exhibitor], url: str, offset: int = 0) -> str:
    """Build a markdown table directly — no LLM needed."""
    start = offset + 1
    end = offset + len(exhibitors)
    header = f"Voici les **{len(exhibitors)} exposants** ({start}–{end}) récupérés depuis {url} :\n"
    lines = [
        header,
        "| Nom | Stand | URL |",
        "|-----|-------|-----|",
    ]
    for e in exhibitors:
        name = (e.name or "—").replace("|", "\\|")
        booth = (e.booth or "—").replace("|", "\\|")
        url_cell = f"[Profil]({e.profile_url})" if e.profile_url else "—"
        lines.append(f"| {name} | {booth} | {url_cell} |")
    return "\n".join(lines)


def _exhibitors_to_context(exhibitors: List[Exhibitor], *, limit: int = 80) -> str:
    # Keep it compact but useful.
    items = []
    for ex in exhibitors[: max(0, limit)]:
        parts = []
        if ex.name:
            parts.append(f"name={ex.name}")
        if ex.booth:
            parts.append(f"booth={ex.booth}")
        if ex.categories:
            parts.append(f"categories={'; '.join(ex.categories)}")
        if ex.profile_url:
            parts.append(f"url={ex.profile_url}")
        if ex.raw:
            # include any extra fields
            extras = ", ".join([f"{k}={v}" for k, v in ex.raw.items() if v])
            if extras:
                parts.append(f"extra=({extras})")
        if parts:
            items.append(" | ".join(parts))

    if not items:
        return "Aucune donnée d'exposants récupérée."

    return "Exposants trouvés (échantillon):\n" + "\n".join(f"- {line}" for line in items)


def _generate_spec(html_snippet: str, url: str, env: Dict[str, str]) -> ScrapeSpec:
    """Ask the LLM to generate a ScrapeSpec from a raw HTML snippet."""
    import re

    prompt = (
        "Tu es un expert en web scraping de sites de salons professionnels.\n"
        "Analyse les informations ci-dessous pour générer un spec de scraping.\n\n"
        f"{html_snippet}\n\n"
        "INSTRUCTIONS :\n"
        "1. Utilise la liste 'Repeated elements with link info' pour identifier le sélecteur CSS des cartes exposants.\n"
        "   Les cartes sont généralement les éléments qui se répètent le plus (ex: 50x, 100x).\n"
        "   IMPORTANT : évite absolument les sélecteurs contenant des classes Tailwind d'animation ou\n"
        "   de visibilité : opacity-0, invisible, hidden, translate-y-full, scale-0, sr-only, etc.\n"
        "   Ces classes indiquent des éléments cachés — Playwright ne pourra pas les trouver.\n"
        "2. Pour le champ 'profile_url' : utilise le sélecteur du lien indiqué par '→ link:' dans la liste.\n"
        "   Si '→ no <a> found', utilise ':scope' uniquement si la carte elle-même est une balise <a>.\n"
        "   Ne mets JAMAIS ':scope' si la carte est un <div> — dans ce cas cherche 'a' ou 'a[href]' dedans.\n"
        "3. Analyse le HTML snippet pour trouver les sélecteurs des champs à l'intérieur d'une carte.\n"
        "3. Génère un JSON valide avec ce format exact :\n"
        "{\n"
        '  "base_url": "https://...",\n'
        '  "cards_selector": "sélecteur CSS exact de chaque carte exposant",\n'
        '  "wait_for_selector": "même valeur que cards_selector",\n'
        '  "fields": {\n'
        '    "name": "sélecteur CSS du nom de l\'exposant à l\'intérieur de la carte",\n'
        '    "booth": "sélecteur CSS du numéro de stand (null si absent)",\n'
        '    "profile_url": ":scope"\n'
        "  },\n"
        '  "pagination": {\n'
        '    "mode": "none ou next_button ou infinite_scroll",\n'
        '    "next_selector": "sélecteur du bouton page suivante (uniquement si next_button, sinon null)",\n'
        '    "max_pages": 10,\n'
        '    "max_scroll_rounds": 30,\n'
        '    "scroll_pause_ms": 800\n'
        "  },\n"
        '  "dedupe_by": "profile_url"\n'
        "}\n\n"
        "Réponds UNIQUEMENT avec le JSON brut, sans markdown, sans explication."
    )

    raw = chat_completions(
        messages=[{"role": "user", "content": prompt}],
        model=env["model"],
        api_key=env["api_key"],
        api_url=env["api_url"],
        temperature=0.1,
    )
    content = _extract_assistant_text(raw)

    # Strip markdown code fences if present
    match = re.search(r"```(?:json)?\s*([\s\S]+?)```", content)
    if match:
        content = match.group(1).strip()

    spec_dict = json.loads(content)
    return load_spec_from_dict(spec_dict)


def _save_spec(spec: ScrapeSpec, url: str) -> None:
    """Persist a generated spec to disk so it can be reused next time."""
    import os
    import re

    domain = re.sub(r"^www\.", "", url.split("/")[2])
    os.makedirs("specs", exist_ok=True)
    spec_dict = {
        "base_url": spec.base_url,
        "cards_selector": spec.cards_selector,
        "wait_for_selector": spec.wait_for_selector,
        "fields": spec.fields,
        "pagination": {
            "mode": spec.pagination.mode,
            "next_selector": spec.pagination.next_selector,
            "max_pages": spec.pagination.max_pages,
            "scroll_pause_ms": spec.pagination.scroll_pause_ms,
            "max_scroll_rounds": spec.pagination.max_scroll_rounds,
        },
        "dedupe_by": spec.dedupe_by,
    }
    with open(f"specs/{domain}_auto.json", "w", encoding="utf-8") as f:
        json.dump(spec_dict, f, indent=2, ensure_ascii=False)


async def _maybe_scrape(
    *,
    scrape_url: Optional[str],
    scrape_spec_path: Optional[str],
    headless: bool,
    timeout_ms: int,
    limit: int = 50,
    offset: int = 0,
    env: Optional[Dict[str, str]] = None,
    on_status: StatusCallback = None,
) -> Optional[List[Exhibitor]]:
    if not scrape_url:
        return None

    if scrape_spec_path:
        _status_cb(on_status, f"Spec trouvé : {scrape_spec_path}")
        spec: ScrapeSpec = load_spec(scrape_spec_path)
    elif env:
        # No spec found — analyze the page and let the LLM generate one
        _status_cb(on_status, "Aucun spec connu — analyse automatique de la page...")
        html = await analyze_page(scrape_url, headless=headless, timeout_ms=timeout_ms, on_status=on_status)
        _status_cb(on_status, "Génération du spec de scraping par le LLM...")
        spec = _generate_spec(html, scrape_url, env)
        _save_spec(spec, scrape_url)
        _status_cb(on_status, "Spec généré et sauvegardé.")
    else:
        return None

    exhibitors = await scrape_hybrid(
        scrape_url,
        spec,
        headless=headless,
        timeout_ms=timeout_ms,
        on_status=on_status,
        limit=limit,
        offset=offset,
    )
    return exhibitors


def _status_cb(on_status: StatusCallback, msg: str) -> None:
    if on_status:
        on_status(msg)


def build_messages(
    *,
    user_message: str,
    system_prompt: Optional[str] = None,
    exhibitors_context: Optional[str] = None,
) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []

    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    if exhibitors_context:
        # Put scraping results in a separate system message to steer behavior.
        messages.append(
            {
                "role": "system",
                "content": (
                    "Contexte de scraping (source: site salon professionnel). "
                    "Utilise ces informations comme base factuelle.\n\n" + exhibitors_context
                ),
            }
        )

    messages.append({"role": "user", "content": user_message})
    return messages


_AGENT_SYSTEM_PROMPT = (
    "Tu es un assistant conversationnel spécialisé dans les salons professionnels et les exposants.\n"
    "Tu peux répondre à n'importe quelle question, discuter, analyser des données, et aider l'utilisateur.\n"
    "Tu disposes aussi d'un outil de scraping automatique qui se déclenche quand l'utilisateur fournit une URL.\n\n"
    "RÈGLES :\n"
    "1. Si le contexte contient 'SCRAPING TERMINÉ AVEC SUCCÈS' :\n"
    "   → Affiche les exposants IMMÉDIATEMENT sous forme de tableau Markdown (colonnes : Nom, Stand, URL).\n"
    "   → Ne pose aucune question avant d'afficher le tableau.\n"
    "2. Si le contexte contient 'SCRAPING TERMINÉ MAIS AUCUN EXPOSANT' :\n"
    "   → Explique que le scraping a échoué et suggère de vérifier l'URL.\n"
    "3. Sans contexte de scraping (cas le plus courant) :\n"
    "   → Réponds normalement à la question ou au message de l'utilisateur.\n"
    "   → Si l'utilisateur demande une liste d'exposants d'un salon, demande-lui l'URL de la page des exposants.\n"
    "   → NE dis PAS que le scraping a échoué — aucun scraping n'a été tenté.\n"
    "4. Ne dis jamais que tu ne peux pas accéder à internet."
)


def handle_chat_request(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Pure function wrapper for later web integration.

    Expected payload (example):
      {
        "messages": [{"role":"user","content":"..."}],
        "scrape": {"url":"...","spec":"specs/x.json"}  // optional
      }

    Returns:
      {
        "assistant": "...",
        "raw": <openai response json>,
        "scrape": {"exhibitors": [...]} // only if performed
      }
    """
    env = _load_env()

    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise OpenAIClientError("payload.messages must be a non-empty list")

    # Always prepend the agent system prompt (unless a system message is already first)
    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": _AGENT_SYSTEM_PROMPT}] + list(messages)

    # Optional scrape enrichment
    scrape_cfg = payload.get("scrape") or None
    scrape_result: Optional[Dict[str, Any]] = None
    if isinstance(scrape_cfg, dict):
        url = scrape_cfg.get("url")
        spec_path = scrape_cfg.get("spec")
        if url:
            import asyncio

            try:
                exhibitors = asyncio.run(
                    _maybe_scrape(
                        scrape_url=str(url),
                        scrape_spec_path=str(spec_path) if spec_path else None,
                        headless=bool(scrape_cfg.get("headless", True)),
                        timeout_ms=int(scrape_cfg.get("timeout_ms", 30_000)),
                        limit=int(scrape_cfg.get("limit", 50)),
                        offset=int(scrape_cfg.get("offset", 0)),
                        env=env,
                        on_status=_emit_status,
                    )
                )
            except Exception as exc:
                print(json.dumps({"type": "status", "msg": f"Erreur scraping : {exc}"}), file=sys.stderr, flush=True)
                exhibitors = []

            ex_list = exhibitors or []
            scrape_result = {"exhibitors": [asdict(e) for e in ex_list]}

            if ex_list:
                # ── Fast path: build the markdown table directly, skip LLM ──
                offset_used = int(scrape_cfg.get("offset", 0))
                assistant = _format_exhibitors_markdown(ex_list, str(url), offset=offset_used)
                return {"assistant": assistant, "scrape": scrape_result}
            else:
                # Scraping failed — ask the LLM to explain
                context_txt = (
                    f"SCRAPING TERMINÉ MAIS AUCUN EXPOSANT RÉCUPÉRÉ depuis {url}.\n"
                    "Causes possibles : protection anti-bot, sélecteurs CSS incorrects, page nécessitant une authentification.\n"
                    "Informe l'utilisateur de l'échec et suggère-lui de vérifier l'URL ou de réessayer."
                )
                messages = [{"role": "system", "content": context_txt}] + messages

    raw = chat_completions(
        messages=messages,
        model=env["model"],
        api_key=env["api_key"],
        api_url=env["api_url"],
        temperature=float(payload.get("temperature", 0.2)),
        max_tokens=payload.get("max_tokens"),
    )

    assistant = _extract_assistant_text(raw)

    out: Dict[str, Any] = {"assistant": assistant, "raw": raw}
    if scrape_result is not None:
        out["scrape"] = scrape_result
    return out


def _json_load_stdin() -> Dict[str, Any]:
    try:
        txt = sys.stdin.read()
        return json.loads(txt)
    except Exception as e:
        raise OpenAIClientError(f"Invalid JSON on stdin: {e}")


def _json_dump(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def cli_main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="openai_chat")
    sub = parser.add_subparsers(dest="cmd", required=True)

    chat = sub.add_parser("chat", help="Send a single user message")
    chat.add_argument("--message", required=True)
    chat.add_argument("--system", default=None)
    chat.add_argument("--temperature", type=float, default=0.2)
    chat.add_argument("--max-tokens", type=int, default=None)

    chat.add_argument("--scrape-url", default=None)
    chat.add_argument("--scrape-spec", default=None)
    chat.add_argument("--headful", action="store_true")
    chat.add_argument("--timeout-ms", type=int, default=30_000)

    chat_json = sub.add_parser("chat-json", help="Read request JSON from stdin, print JSON")

    args = parser.parse_args(argv)

    if args.cmd == "chat":
        env = _load_env()

        exhibitors_context: Optional[str] = None
        if args.scrape_url and args.scrape_spec:
            import asyncio

            exhibitors = asyncio.run(
                _maybe_scrape(
                    scrape_url=args.scrape_url,
                    scrape_spec_path=args.scrape_spec,
                    headless=not args.headful,
                    timeout_ms=args.timeout_ms,
                )
            )
            exhibitors_context = _exhibitors_to_context(exhibitors or [])

        messages = build_messages(
            user_message=args.message,
            system_prompt=args.system,
            exhibitors_context=exhibitors_context,
        )

        raw = chat_completions(
            messages=messages,
            model=env["model"],
            api_key=env["api_key"],
            api_url=env["api_url"],
            temperature=args.temperature,
            max_tokens=args.max_tokens,
        )
        print(_extract_assistant_text(raw))
        return 0

    if args.cmd == "chat-json":
        payload = _json_load_stdin()
        out = handle_chat_request(payload)
        print(_json_dump(out))
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(cli_main())
