# haiku.rag for Raycast

Search the files haiku.rag indexes and land on the exact page and passage.
It talks to the local search service (`haiku-ingester serve`, contract in
haiku.rag's [`docs/local-search-api.md`](https://github.com/tolgaerdonmez/haiku.rag/blob/main/docs/local-search-api.md)).
The [installer](../README.md) builds, registers and updates it.

## Commands

- **Search Documents**: results as you type, one section per file, files in
  the order of their best passage. A section shows the file's three best
  passages; **Show all N in this file** (or ⌘↵ on any of them) opens every
  passage of that file for the query, which you can refine there. Each row
  shows the page, the section heading and the snippet with the matched words
  in bold. The detail pane (⌘D) previews the selected passage under one
  heading line, the document's name and the page:

  | File | Preview |
  |---|---|
  | PDF, scanned PDF | The page rendered with PDFKit, the passage's boxes highlighted: first zoomed on the passage, then the whole page. |
  | Markdown, text, code | The passage's lines in the file, numbered and marked, with three lines around them. |
  | Word, PowerPoint, HTML, EPUB, other | Quick Look's thumbnail of the file. |

  Only the selected row is rendered. Images are cached in the extension's
  support folder (`previews/pages`, at most 200 MB, least recently used
  removed first) by file, modification time, page and boxes. A first render
  takes about 0.1-0.3 s; when one fails the pane shows the passage's text.
- **Manage Indexed Folders**: the folder register. Add a folder, edit its
  include and exclude patterns (gitignore-style), stop watching it or remove it from the index,
  and see and retry files that failed to ingest.

## Hotkey: Cmd+Shift+F

Raycast keeps command hotkeys only in its own settings store. An extension's
manifest cannot declare one and Raycast has no configuration file for them,
so a hotkey can be set in two ways:

- **Declaratively, through Karabiner-Elements.**
  [`karabiner/haiku-search.json`](../karabiner/haiku-search.json)
  is a complex-modification rule mapping ⌘⇧F to
  `open 'raycast://extensions/tolgaerdonmez/haiku-rag/search-documents'`.
  Add it to Karabiner's `assets/complex_modifications` and enable it (a
  configuration manager can merge it into the selected profile's
  `complex_modifications.rules`; the installer's `hotkey` component does
  that). The first time Raycast receives the link
  from outside it may ask to confirm opening the command.
- **Once, in Raycast's settings.** Raycast Settings → Extensions → Haiku RAG →
  Search Documents → Record Hotkey, then press ⌘⇧F. Raycast's settings
  export (Settings → Advanced → Export) carries the hotkey to another Mac.

Either way ⌘⇧F becomes global: other apps no longer receive it, for example
Find in Files in VS Code or Recents in Finder. Inside the extension, Show in
Finder is ⌘⇧R for the same reason.

## Opening a result

Enter opens the result where the passage is:

| File | Opens in | Lands on |
|---|---|---|
| PDF, scanned PDF | Skim | The page, scrolled to the passage. The snippet sentence with the most matched words is selected when it lies inside the passage's boxes, else all the text in them; a scanned page or a picture gets a selection box around its element. Nothing is written to the PDF. |
| Markdown, text, code | Neovim in a new Rex tab | The passage's line, centred. |
| Word | Microsoft Word | The page, with the passage copied for ⌘F. Without Word, the default app with the passage copied. |
| PowerPoint | Microsoft PowerPoint | The slide, with the passage copied. Without PowerPoint, the default app with the passage copied. |
| HTML | The default browser | The section, through a text fragment that highlights the heading and the passage. |
| EPUB | The default browser | The chapter file holding the passage, at the section. **Open in Books** opens it in Books with the passage copied. |
| Anything else | The default app | The passage is copied for ⌘F. |

Rex tabs go to the session named in the preferences (`haiku` by default),
created on first use, and Rex is brought forward on that tab.

## Actions (⌘K)

| Action | Shortcut |
|---|---|
| Show All in This File | ⌘↵ |
| Show in Finder | ⌘⇧R |
| Search in This Document | ⌘F |
| Quick Look Page (the PDF page with the passage outlined) | ⌘Y |
| Copy Citation (file, page, heading trail, path) | ⌘⇧C |
| Ask About This Result | ⌘⇧A |
| Copy Passage, Copy Path, Open in Default App | |
| Manage Indexed Folders | |

**Ask About This Result** sends the passage (its section when the service
returned one) and your question to OpenRouter. The API key is a password
preference. Answers are capped at 700 tokens, and asking stops once the
month's recorded spend reaches **Monthly Spending Cap** (default $1.00). The
spend is OpenRouter's reported cost for each answer, kept in the extension's
local storage. A spending limit on the OpenRouter key itself is the hard
backstop.

## Preferences

| Preference | Default |
|---|---|
| Search Service URL | `http://127.0.0.1:8765` |
| Service Token | empty; set when `ingester.api.auth_token` is set |
| Mock Data | off; on serves the sample files in `assets/samples` without a service |
| Result Limit | 50, the most the service returns; grouped by file |
| Rex CLI | empty finds the CLI bundled with Rex |
| Rex Session | `haiku` |
| Editor | `nvim` |
| OpenRouter API Key, Ask Model, Monthly Spending Cap | empty, `google/gemini-2.5-flash-lite`, `1.00` |

## Development

```bash
cd raycast
npm install
npm run dev        # ray develop: loads the extension into Raycast until you stop it
npm test           # vitest: coordinate mapping, opener dispatch, scripts, client
npm run lint       # ray lint
npm run typecheck
npm run build      # ray build into ./dist
```

Turn on **Mock Data** to try the commands and every opener against the
bundled samples without the service.
