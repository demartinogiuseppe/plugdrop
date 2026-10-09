# NOTES — verifica sul campo

Verificato il 2026-10-09 su Windows 10, Claude Code **2.1.295**, Python 3.14.4, git 2.51, gh 2.93.

## 1. Fonte dei dati: CLI ufficiale, non file interni

Esiste un comando ufficiale con output JSON, quindi lo script usa **solo la CLI** (come chiede il brief):

| Scopo | Comando |
|---|---|
| Plugin installati | `claude plugin list --json` |
| Marketplace noti | `claude plugin marketplace list --json` |
| Aggiungere marketplace | `claude plugin marketplace add <source> [--scope user] [--json]` |
| Installare | `claude plugin install <name>@<marketplace> --scope user [--json]` |
| Versione | `claude --version` → `2.1.295 (Claude Code)` |

`claude plugin install` ha anche `-y/--yes`: serve solo per plugin con sorgente "command" o `headersHelper`. **Non lo passiamo mai in automatico**: se un'installazione lo richiede, la voce fallisce e il report lo dice (l'utente decide a mano).

### Schema reale di `claude plugin list --json`

Array di oggetti:

```json
{
  "id": "superpowers@claude-plugins-official",
  "version": "6.4.1",
  "scope": "user",
  "enabled": true,
  "installPath": "C:\\Users\\<utente>\\.claude\\plugins\\cache\\...",
  "installedAt": "2026-04-23T16:13:38.556Z",
  "lastUpdated": "2026-09-30T09:29:37.507Z",
  "projectEnabled": false,
  "mcpServers": { "...": { "command": "...", "args": ["..."] } },
  "hasUserConfig": true
}
```

- `id` = `nome@marketplace` (si separa sull'**ultimo** `@`).
- `mcpServers`, `hasUserConfig`, `installPath` sono opzionali.

**Valori di `scope` trovati (più di quelli previsti dal brief):**

| scope | esempio | trattamento |
|---|---|---|
| `user` | `ecc@ecc` | importabile |
| `project`, `local` | (non presenti qui, documentati) | `non-user-scope`, solo info |
| `session` | `my-plugin@inline` (caricato con `--plugin-dir`) | `non-user-scope`, solo info |
| `synced` | `postiz@synced` (sincronizzato dall'account claude.ai) | `non-user-scope`, solo info: arriva già da claude.ai, non si installa via CLI |

`inline`, `synced`, `builtin`, `skills-dir` sono nomi di marketplace **riservati** (doc ufficiale): non sono marketplace reali, non si possono aggiungere.

> Nota sul brief: lo schema d'esempio cita `postiz@postiz`; su questa macchina postiz è `postiz@synced`, quindi finisce in "solo info".

### Schema reale di `claude plugin marketplace list --json`

Array; i campi dipendono da `source`:

```json
{"name": "caveman",  "source": "github",    "repo": "JuliusBrussee/caveman", "installLocation": "..."}
{"name": "ecc",      "source": "git",       "url": "https://github.com/affaan-m/ECC.git", "installLocation": "..."}
{"name": "my-local-market", "source": "directory", "path": "G:\\...", "installLocation": "..."}
```

Tipi di sorgente documentati: `github` (`repo`, `ref`, `path`), `git` (`url`, `ref`, `path`), `url` (`url`, `headers`, `headersHelper`), `file` (`path`), `directory` (`path`), `settings`, `npm`.

**Mappatura sorgente → stringa per `marketplace add` e portabilità:**

| source | stringa per `add` | portability |
|---|---|---|
| `github` | `repo` (+ `@ref` se presente) | `portable` |
| `git` | `url` (+ `#ref` se presente) | `portable` |
| `url` | `url` | `portable` |
| `directory`, `file` | — | `local` |
| `settings`, `npm`, altro | — | `local` (non riproducibile via CLI) |

### File interni (solo per confronto, NON usati dallo script)

- `~/.claude/plugins/installed_plugins.json`: `{"version": 2, "plugins": {"<id>": [{scope, installPath, version, installedAt, lastUpdated, gitCommitSha}]}}`
- `~/.claude/plugins/known_marketplaces.json`: `{"<name>": {"source": {"source": "github", "repo": "..."}, "installLocation", "lastUpdated"}}`
- `~/.claude/settings.json` → `enabledPlugins: {"<id>": true}`. Il campo `enabled` della CLI lo riflette già.

## 2. Struttura plugin (doc ufficiale: plugins-reference, marketplace-reference)

- `.claude-plugin/plugin.json`: unico campo obbligatorio `name` (kebab-case). Consigliati `version`, `description`, `author.name`, `license`.
- `commands/*.md`: file Markdown piatti; i comandi sono **namespaced** `/<plugin>:<comando>` → `/plugdrop:export`, `/plugdrop:import`, e `/plugdrop:plugdrop` (il file `plugdrop.md`). La doc dice "Prefer `skills/` for new plugins", ma `commands/` è ancora supportato: teniamo `commands/` come da brief.
- `${CLAUDE_PLUGIN_ROOT}`: nel corpo Markdown dei comandi viene **sostituito inline** al caricamento (con `/` anche su Windows). Non è invece nell'ambiente dei comandi Bash → va scritto nel `.md`, es. `python "${CLAUDE_PLUGIN_ROOT}/scripts/plugdrop.py" ...`.
- `.claude-plugin/marketplace.json`: obbligatori `name`, `owner.name`, `plugins[]`; ogni voce richiede `name` e `source`. Plugin alla radice del repo → `"source": "./"` (o `"."`).
- Verifica finale: `claude plugin validate <dir>`.

## 3. Ambiente Windows

- **`python3` su Windows è l'alias del Microsoft Store e non funziona** ("Python non è stato trovato"). `python` funziona. I comandi devono provare in ordine: `python`, `py -3`, `python3`, e scartare lo stub dello Store (exit code ≠ 0 su `--version`).
- `gh auth status` → OK (account loggato). `git` OK.
- Percorsi con backslash in `installPath`/`path`: da non riportare nello snapshot (vedi §4).

## 4. Sicurezza e privacy — cosa NON va nello snapshot

Scoperte concrete che estendono la "pulizia di sicurezza" del brief:

- **`mcpServers`** nell'output di `plugin list` contiene comandi e argomenti completi (e in generale può contenere `env` con token). **Mai copiato.**
- **`installPath` / `installLocation` / `path`** contengono il nome utente Windows (`C:\Users\<utente>`). **Mai copiati**; per le sorgenti `local` si salva solo il tipo (`directory`/`file`) e il nome del marketplace.
- **`headers` / `headersHelper`** delle sorgenti `url` possono contenere credenziali. **Mai copiati.**
- URL con credenziali (`https://user:token@host`, query `token=`, `access_token=`, `key=`, ecc.) → ripuliti e segnalati.
- Lo script **non apre mai** `.credentials.json`, `~/.claude.json`, `settings.json`.
- Whitelist, non blacklist: lo snapshot contiene solo i campi elencati nello schema, costruiti uno per uno.

## 5. Parte legale ("legal safe")

### Marchio "Claude" / Anthropic

- La doc ufficiale riserva i nomi di plugin che **iniziano** con `claude-`/`anthropic-` o sono `claude`, `claude-code` ecc. (errore), e dà **warning** se `claude` compare come parola intera altrove.
- `name: "plugdrop"` → nessun problema.
- Nome del marketplace: `plugdrop` → nessun conflitto con i nomi riservati.
- **Repo**: il brief proponeva `plugdrop-for-claude`. **Decisione dell'utente (2026-10-09): rinominato in `plugdrop`**, così il marchio non compare nel nome. "Claude Code" compare solo nel testo, in modo descrittivo ("a plugin for Claude Code").
- Mitigazioni comunque previste: niente logo/colori Anthropic, `displayName` senza "Claude", disclaimer di non affiliazione nel README ("Not affiliated with, endorsed or sponsored by Anthropic, PBC. Claude and Claude Code are trademarks of Anthropic, PBC.").

### Licenze di terzi

- Lo snapshot contiene **solo riferimenti** (nome, marketplace, sorgente pubblica, versione), mai codice o file dei plugin. Nessuna redistribuzione → nessun obbligo verso le licenze dei plugin di terzi. L'installazione avviene dalla sorgente originale tramite la CLI ufficiale.
- Il plugin non ha dipendenze di terzi (solo stdlib Python): niente NOTICE da aggiungere.

### Privacy / dati personali

- Lo snapshot contiene autore e nome macchina scelti dall'utente, in un repo **privato** suo. Il README lo dice esplicitamente.
- Nessuna telemetria, nessuna chiamata di rete oltre a GitHub (via `gh`/`git`) e alla CLI di Claude Code.

### Licenza

- MIT, con file `LICENSE` e `"license": "MIT"` in `plugin.json`. Titolare del copyright: **demartinogiuseppe** (decisione dell'utente).
- Clausola "AS IS" della MIT + nota nel README: il plugin installa software di terzi scelto dall'utente; l'utente resta responsabile di cosa installa.
