# plugdrop

A plugin for Claude Code that moves your **setup** between machines: your plugins and your personal skills,
commands and agents.

- **Export** saves what is on this machine as a new snapshot in a private GitHub repo that plugdrop creates for
  you (`plugdrop-snapshots`).
- **Import** shows a snapshot next to what is here and lets you install everything that is missing, one kind
  (only skills, only plugins...), one category, a hand-picked set, or nothing.

Every export is a new snapshot with author, date, machine and a note. Snapshots are never overwritten, and import
never overwrites anything you already have.

> plugdrop is an independent project. It is not affiliated with, endorsed or sponsored by Anthropic, PBC.
> Claude and Claude Code are trademarks of Anthropic, PBC.

## What is saved (and what is not)

**Plugins** are stored as **references**, not code: for each plugin its name, marketplace, marketplace source
(for example `owner/repo`), version, scope and whether it was enabled. On import, every plugin is downloaded
again from its original source through the official `claude plugin` CLI.

**Personal skills, commands and agents** are the ones in your Claude config folder (`~/.claude/skills/<name>/`,
`~/.claude/commands/`, `~/.claude/agents/`), not those that come with plugins.
- A skill that is a clone of a git repo (it has a `.git` folder and an `origin` remote) is stored as a reference
  to that repo and cloned again on import. Local changes you have not pushed are not carried over (export warns).
- Everything else is copied into the snapshot repo, under `files/`, one file per content hash, so repeated
  exports do not duplicate anything. `.git`, `node_modules`, `__pycache__` and virtualenv folders are left out.
- An item is **not saved at all** if one of its files looks like a credential (`.env`, private keys, `*.pem`,
  tokens such as `ghp_…`, `sk-…`, `AKIA…`), is larger than 1 MB, or the item is larger than 10 MB, or contains a
  link. Export lists every item left out and why.

Not saved, ever: plugin files, plugin settings or data, MCP server configs, `settings.json`, `CLAUDE.md`,
credentials or tokens (credentials found in source URLs are removed and reported).

Consequences:
- The marketplace may ship a newer version than the one in the snapshot; the import report flags it.
- If a plugin's or a skill's source repo disappears, it can no longer be reinstalled.
- Plugin options and saved data must be set up again on the new machine. A skill cloned from a repo that has
  its own setup step needs that step run by hand.

## Requirements

- Claude Code with the `claude` CLI on PATH
- Python 3.8+ (standard library only). On Windows `python` or `py` works; the `python3` Microsoft Store alias is not used.
- Git and the GitHub CLI `gh`, logged in (`gh auth login`)

Works on Windows (cmd and Windows Terminal), macOS and Linux.

## Install

In Claude Code:

```
/plugin marketplace add demartinogiuseppe/plugdrop
/plugin install plugdrop@plugdrop
```

Restart Claude Code afterwards.

## Use

| Command | What it does |
|---|---|
| `/plugdrop:export` | Save your plugins, skills, commands and agents as a new snapshot. |
| `/plugdrop:import` | Install from a snapshot. |

Each command first asks: **guided or direct?** Add `--direct` to skip the question.

- **Guided**: summary, categories and a note on export; snapshot choice, selection (all, one kind, one category,
  item by item, none) and a confirmation on import.
- **Direct**: no questions. Export keeps the existing categories and writes an automatic note. Import takes the most
  recent snapshot and installs everything missing without asking.

Both commands accept `--dry-run`: they show what would happen and change nothing.

`/plugdrop:import --diff` only compares: what is in a snapshot but missing here, what is here but not in the
snapshot, and what has the same name but different content. It installs nothing.

On first run plugdrop checks `git`, `gh` and your GitHub login, asks for author, machine and repo name, creates the
private repo if needed, clones it into `~/.plugdrop/repos/<owner>/<name>/` and saves `~/.plugdrop/config.json`.

To use another snapshot repo later, add `--repo <name>` (or `--repo owner/name`) to either command. plugdrop
clones the new repo next to the old one and switches to it; the old clone is kept. If a local clone turns out to
belong to a different repo than the one you chose, plugdrop stops and tells you, instead of using it.

### Categories

`categories.json` at the root of your snapshot repo maps an item to a category, for example
`{"superpowers@claude-plugins-official": "method", "skill:notes": "memory"}`. Items are `plugin@marketplace`,
`skill:<name>`, `command:<name>` or `agent:<name>`. During export Claude offers to fill in missing ones, or to review the current ones when none is missing. You can
edit the file by hand at any time. Import can install a single category.

### Plugin classes

| In the snapshot | Meaning | On import |
|---|---|---|
| `portable` | marketplace from GitHub, a git URL or a web URL | can be installed |
| `local` | marketplace from a folder or file on the source machine | shown as not portable |
| `non-user-scope` | project, local, session (`--plugin-dir`) or claude.ai-synced plugins | shown as info only |

On import, a skill, command or agent that already exists here is shown as `installed` when its content is the
same and `different` when it is not. A `different` item is never touched.

## When you don't need plugdrop

Claude Code can already rebuild your plugins on a new machine by itself:

- **Copy `~/.claude/settings.json`** (for example through a dotfiles repo). Its `enabledPlugins` and
  `extraKnownMarketplaces` keys list your plugins and marketplaces; on a machine where they are missing, Claude Code
  clones the marketplaces and downloads the enabled plugins on its own. See the
  [plugin loading reference](https://code.claude.com/docs/en/plugins/loading).
- **Plugins turned on in your claude.ai account** sync to Claude Code automatically (they appear as `name@synced`).
- Third-party tools sync your whole `~/.claude` configuration, not just plugins.

If you already keep your settings in dotfiles, that is faster than plugdrop. plugdrop is for when you want:

- to **choose** what to install (a category, a few plugins) instead of all or nothing;
- to move **only the plugin list**, without the rest of `settings.json` (permissions, hooks, environment variables,
  sometimes tokens), which is often machine-specific or sensitive;
- a **history** of snapshots with date, machine, author and note;
- all this **without managing a git repo of your own**.

## Rules plugdrop follows

- Never overwrites or deletes a snapshot.
- Never uninstalls or disables a plugin. A plugin that was disabled on the source machine is reported, not changed.
- Never overwrites a skill, command or agent: it only creates the missing ones (each skill is built in a temporary
  folder and moved into place in one step).
- Never reads `.credentials.json`, `~/.claude.json` or other credential files, and never edits `settings.json`:
  plugin installs go through `claude plugin install` only.
- Never copies a skill, command or agent that looks like it contains a secret.
- The snapshot repo must be private: plugdrop refuses to use an existing public repo.
- No telemetry. Network access happens only through `git`, `gh` and the `claude` CLI.

## Privacy

Your snapshots contain the author and machine names you choose, the list of your plugins and the content of your
personal skills, commands and agents. They are stored only in your own private GitHub repo.

The secret check is a safety net, not a guarantee: it recognizes common token formats and credential file names,
not every password written in plain text. Do not keep secrets inside skills.

## Limits

- MCP servers you added yourself (`claude mcp add`) are deliberately left out, and every export and import says
  so: their configuration often holds private keys, and it lives in a file plugdrop does not read. MCP servers that
  come with a plugin are reinstalled together with the plugin.
- Also out of scope: hooks, settings and `CLAUDE.md`.
- No uninstall, no background sync.
- Plugins whose install needs an interactive confirmation (marketplace-declared commands) fail with a clear error;
  install them by hand with `/plugin`.
- If a marketplace registers under a different name than in the snapshot, that install fails and is reported.

You choose which third-party plugins to install; plugdrop just repeats the official install command for you.

## Tests

```
python -m unittest discover -s tests
```

Unit tests use fake data only. Manual end-to-end check:

1. Export on machine A: the snapshot appears in your `plugdrop-snapshots` repo on GitHub.
2. Export again on A with a different note: a second file appears, the first is unchanged.
3. Import on machine B choosing a single category: only that category is installed.
4. Import the same snapshot again: everything is "already installed", nothing happens.
5. Change a personal skill on B and import again: it shows as "different" and stays as it is.

## License

MIT. See [LICENSE](LICENSE).
