# Frontend — Shaarp48H

Interface chat Next.js pour interagir avec le scraper d'exposants.

## Installation

```bash
npm install
npm run dev
```

Ouvrir [http://localhost:3000](http://localhost:3000).

## Structure

```
frontend-shaarp/
├── app/
│   ├── page.tsx          # Interface chat (React)
│   ├── layout.tsx        # Layout global
│   └── api/chat/
│       └── route.ts      # Route API — spawn Python, stream SSE
└── package.json
```

## Fonctionnement

1. L'utilisateur envoie un message dans le chat
2. Si le message contient une URL, `route.ts` détecte le domaine et associe le bon spec de scraping
3. Le payload JSON est envoyé au subprocess Python (`openai_chat.py chat-json`)
4. Le résultat est streamé vers le frontend via Server-Sent Events (SSE)
5. Les exposants sont affichés dans un tableau Markdown

## Export

| Format | Séparateur | Encodage | Compatibilité |
|--------|-----------|----------|--------------|
| CSV | `;` | UTF-8 BOM | Excel FR/EN |
| Excel (.xlsx) | — | — | Excel, LibreOffice |

## Variables d'environnement

| Variable | Description |
|----------|-------------|
| `PYTHON_BIN` | Chemin Python custom (optionnel, auto-détecté sinon) |

## Logs

Les logs serveur sont écrits dans `../../backend/logs/frontend.log` (chemin relatif au `cwd` Next.js).
Format : `ISO8601 [LEVEL] message`
