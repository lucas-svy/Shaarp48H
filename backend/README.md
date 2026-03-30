# Challenge48h – Scripts Python

Deux scripts Python prêts à être branchés plus tard dans une web app Next.js (chat conversationnel).

## 1) Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

> Le navigateur Playwright doit être installé une fois via `playwright install chromium`.

## 2) Script de scraping (Playwright)

Fichier: `salon_scraper.py`

- Scraping configurable via une spec JSON (sélecteurs CSS + pagination)
- Sortie JSON structurée (liste d’exposants)

Exemple:

```bash
python salon_scraper.py scrape \
  --url "https://example.com/exhibitors" \
  --spec specs/example_exhibitors_spec.json
```

## 3) Script ChatGPT (OpenAI) avec enrichissement scraping

Fichier: `openai_chat.py`

- Lit `API_KEY`, `API_URL`, `MODEL` depuis `.env`
- En option, exécute un scraping et injecte le résultat comme contexte dans le prompt

Exemple simple:

```bash
python openai_chat.py chat --message "Donne-moi les exposants les plus pertinents" 
```

Exemple avec scraping:

```bash
python openai_chat.py chat \
  --message "Analyse ces exposants et propose un ciblage" \
  --scrape-url "https://example.com/exhibitors" \
  --scrape-spec specs/example_exhibitors_spec.json
```

## 4) Mode JSON (prévu pour intégration Next.js)

Le mode `chat-json` lit une requête JSON sur stdin et renvoie une réponse JSON sur stdout.

```bash
echo '{
  "messages": [{"role":"user","content":"Hello"}],
  "scrape": {"url":"https://example.com/exhibitors","spec":"specs/example_exhibitors_spec.json"}
}' | python openai_chat.py chat-json
```
