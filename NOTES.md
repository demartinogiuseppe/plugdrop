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
- `commands/*.md`: file Markdown piatti; i comandi sono **namespaced** `/<plugin>:<comando>` → `/plugdrop:export`, `/plugdrop:import`. **Verificato (2026-10-09): la forma breve `/plugdrop` non esiste** (un file `plugdrop.md` diventa solo `/plugdrop:plugdrop`). Per questo, su decisione dell'utente, il comando d'ingresso `/plugdrop` del brief è stato tolto (v0.2.0): restano solo export e import. Le istruzioni comuni stanno in `scripts/shared-instructions.md`, che non è un comando. La doc dice "Prefer `skills/` for new plugins", ma `commands/` è ancora supportato: teniamo `commands/` come da brief.
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

### Skill, comandi e agent personali (0.4.0, decisione dell'utente 2026-10-09: "procedi con il punto 1")

- Sorgenti: `<config>/skills/<nome>/SKILL.md`, `<config>/commands/**/*.md`, `<config>/agents/**/*.md`, dove `<config>` è `CLAUDE_CONFIG_DIR` o `~/.claude`. Cartelle senza `SKILL.md` (es. `synced/`, `learned/`, `.trash/`) ignorate: `synced` arriva già da claude.ai.
- Skill con `.git` e remote `origin` remoto → riferimento `{url, commit}`, import con `git clone`. Sulla macchina di test erano 3, una delle quali da 1,3 GB con `node_modules`. Modifiche locali non pushate → warning.
- Altri file → blob `files/<sha[:2]>/<sha>` nel repo snapshot (deduplicati). Esclusi `.git`, `node_modules`, `__pycache__`, `.venv`, `venv`. Sulla macchina di test: 66 elementi, 274 file.
- Item escluso per intero (salvato solo il nome) se: nome file da credenziale, token riconoscibile (`ghp_`, `github_pat_`, `sk-`, `xox?-`, `AKIA`, `AIza`, chiave privata PEM), file > 1 MB, item > 10 MB, link simbolico. Token con `EXAMPLE` ignorati: una skill reale citava la chiave d'esempio AWS `AKIAIOSFODNN7EXAMPLE` (falso positivo trovato nel test reale).
- Import: crea solo ciò che manca. Esiste uguale → `installed`; esiste diverso → `different`, mai toccato. Le skill vengono costruite in una cartella di staging e rinominate in un colpo solo. Nomi e percorsi dallo snapshot validati (niente `..`, `\`, `:`), hash verificati prima di scrivere.
- `CLAUDE.md` personale (punto 3, 0.6.0, decisioni dell'utente 2026-10-09): opzione A "unione guidata" con marcatore `<!-- plugdrop: added on <data> from <macchina> -->` (commento HTML, non un'istruzione; serve a riconoscere i blocchi già aggiunti). Unica eccezione alla regola "mai modificare": solo **aggiunte in fondo**, solo dopo scelta dell'utente, sempre con copia `CLAUDE.md.plugdrop-backup-<data>` (mai sovrascritta, suffisso -2, -3). Blocchi: un titolo e il suo contenuto, oppure un paragrafo prima del primo titolo; titoli dentro i blocchi di codice ignorati. Un blocco "c'è già" se le sue righe (senza spazi ai bordi) compaiono in fila nel file locale. Terminazioni di riga dell'originale conservate. Avvisi per blocchi che nominano plugin/skill non installati, percorsi della macchina d'origine, import `@file.md`.
- Archivio degli snapshot (punto 5, 0.7.0, decisione dell'utente 2026-10-09: "aggiungiamo anche la cartella condivisa"): `config.backend` = `github` (default, anche per i config vecchi senza il campo; unico caso in cui la privacy è verificata), `git` (URL di un repo esistente su qualsiasi server; rifiutati URL con credenziali; clone in `~/.plugdrop/repos/git/<host_percorso>`) o `folder` (cartella sincronizzata usata così com'è, nessun git: niente pull/commit/push, `export` restituisce `saved_to`). Per `git` e `folder` plugdrop avvisa che non può verificare chi legge i dati. Testati: `folder` con ciclo completo export→import; `git` con setup e lettura sul repo privato reale (nessuna scrittura). Rischio noto con `folder`: due macchine che modificano `categories.json` insieme possono generare copie in conflitto del servizio di sync; gli snapshot no, perché ogni export crea un file nuovo.
- **Regola dell'utente:** è sempre l'utente a scegliere cosa importare (tutto, solo plugin, solo skill, solo comandi, solo agent, solo `CLAUDE.md`). Anche la modalità diretta fa questa domanda (una sola), poi procede senza conferma.
- MCP (punto 2) **escluso per scelta dell'utente (2026-10-09)**, con un avviso alla fine di ogni export e import (sezione "MCP notice" di `scripts/shared-instructions.md`). Motivi: la CLI (`claude mcp list/get`) non ha uscita JSON, avvia ogni server e unisce gli argomenti con spazi; l'unica fonte affidabile è `~/.claude.json`, vietato dal brief. Verificato invece che `${VAR}` nella config utente viene espanso dalle variabili d'ambiente.

## 5. Parte legale ("legal safe")

### Marchio "Claude" / Anthropic

- La doc ufficiale riserva i nomi di plugin che **iniziano** con `claude-`/`anthropic-` o sono `claude`, `claude-code` ecc. (errore), e dà **warning** se `claude` compare come parola intera altrove.
- `name: "plugdrop"` → nessun problema.
- Nome del marketplace: `plugdrop` → nessun conflitto con i nomi riservati.
- **Repo**: il brief proponeva `plugdrop-for-claude`. **Decisione dell'utente (2026-10-09): rinominato in `plugdrop`**, così il marchio non compare nel nome. "Claude Code" compare solo nel testo, in modo descrittivo ("a plugin for Claude Code").
- Mitigazioni comunque previste: niente logo/colori Anthropic, `displayName` senza "Claude", disclaimer di non affiliazione nel README ("Not affiliated with, endorsed or sponsored by Anthropic, PBC. Claude and Claude Code are trademarks of Anthropic, PBC.").

### Licenze di terzi

- Per i **plugin** lo snapshot contiene **solo riferimenti** (nome, marketplace, sorgente pubblica, versione), mai codice o file. Nessuna redistribuzione → nessun obbligo verso le licenze dei plugin di terzi. L'installazione avviene dalla sorgente originale tramite la CLI ufficiale.
- Dalla 0.4.0 le **skill/comandi/agent personali** vengono copiati, ma solo nel repo **privato** dell'utente e per suo uso: è una copia di backup personale, non una redistribuzione. Le skill che sono clone git  restano riferimenti al repo originale. Il README dice che il repo deve restare privato e plugdrop rifiuta i repo pubblici.
- Il plugin non ha dipendenze di terzi (solo stdlib Python): niente NOTICE da aggiungere.

### Revisione legale delle funzioni 0.4–0.7 (2026-10-09, richiesta dell'utente: "non dimentichiamo l'aspetto legalsafe")

- **Marchi di terzi**: README nomina GitHub, GitLab, Gitea, OneDrive, Dropbox, Google Drive solo in modo descrittivo (dove tenere gli snapshot); aggiunta la dichiarazione "marchi dei rispettivi titolari, nessuna affiliazione" accanto a quella su Anthropic.
- **Contenuti di terzi**: nuova sezione README "Third-party content": copia solo per uso personale nell'archivio scelto dall'utente; tenerlo privato; pubblicarlo può essere redistribuzione soggetta alle licenze altrui; responsabilità dell'utente.
- **Dati personali**: `CLAUDE.md` e skill possono contenere dati personali; con server git di terzi o cartella sincronizzata li conserva quel fornitore secondo i suoi termini (README, sezione Privacy).
- **Pulizia del repo pubblico**: nelle 0.1–0.6 erano finiti nel repo pubblico dettagli del setup dell'autore (nomi di sue skill, una copia del suo `CLAUDE.md`, nomi di macchina, il suo nome di battesimo in un test). Sostituiti con esempi generici anche nella cronologia, riscritta con `git filter-branch` e force push (decisione dell'utente, 2026-10-09). Regola per il futuro: negli esempi, nei test e nelle NOTES usare solo nomi inventati, mai quelli della macchina di sviluppo.
- `CLAUDE.md` viene modificato (solo aggiunte, con backup): coperto dalla clausola "AS IS" della MIT; il README descrive esattamente cosa viene scritto.

### Privacy / dati personali

- Lo snapshot contiene autore e nome macchina scelti dall'utente, in un repo **privato** suo. Il README lo dice esplicitamente.
- Nessuna telemetria, nessuna chiamata di rete oltre a GitHub (via `gh`/`git`) e alla CLI di Claude Code.

### Licenza

- MIT, con file `LICENSE` e `"license": "MIT"` in `plugin.json`. Titolare del copyright: **demartinogiuseppe** (decisione dell'utente).
- Clausola "AS IS" della MIT + nota nel README: il plugin installa software di terzi scelto dall'utente; l'utente resta responsabile di cosa installa.
