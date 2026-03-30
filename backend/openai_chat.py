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
from dataclasses import asdict
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from salon_scraper import Exhibitor, ScrapeSpec, load_spec, scrape_exhibitors


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


async def _maybe_scrape(
    *,
    scrape_url: Optional[str],
    scrape_spec_path: Optional[str],
    headless: bool,
    timeout_ms: int,
) -> Optional[List[Exhibitor]]:
    if not scrape_url or not scrape_spec_path:
        return None

    spec: ScrapeSpec = load_spec(scrape_spec_path)
    exhibitors = await scrape_exhibitors(
        scrape_url,
        spec,
        headless=headless,
        timeout_ms=timeout_ms,
    )
    return exhibitors


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

    # Optional scrape enrichment
    scrape_cfg = payload.get("scrape") or None
    scrape_result: Optional[Dict[str, Any]] = None
    if isinstance(scrape_cfg, dict):
        url = scrape_cfg.get("url")
        spec_path = scrape_cfg.get("spec")
        if url and spec_path:
            import asyncio

            exhibitors = asyncio.run(
                _maybe_scrape(
                    scrape_url=str(url),
                    scrape_spec_path=str(spec_path),
                    headless=bool(scrape_cfg.get("headless", True)),
                    timeout_ms=int(scrape_cfg.get("timeout_ms", 30_000)),
                )
            )
            ex_list = exhibitors or []
            scrape_result = {"exhibitors": [asdict(e) for e in ex_list]}

            # Prepend as system context
            context_txt = _exhibitors_to_context(ex_list)
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
