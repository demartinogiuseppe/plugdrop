# plugdrop

A plugin for Claude Code that moves your **plugin list** between machines.

- **Export** saves the list of plugins installed on this machine as a new snapshot in a private GitHub repo
  that plugdrop creates for you (`plugdrop-snapshots`).
- **Import** shows a snapshot next to what is installed here and lets you install all of the missing plugins,
  one category, a hand-picked set, or none.

Every export is a new file with author, date, machine and a note. Snapshots are never overwritten.

> plugdrop is an independent project. It is not affiliated with, endorsed or sponsored by Anthropic, PBC.
> Claude and Claude Code are trademarks of Anthropic, PBC.

## What is saved (and what is not)

A snapshot stores **references**, not plugins: for each plugin its name, marketplace, marketplace source
(for example `owner/repo`), version, scope and whether it was enabled. On import, every plugin is downloaded
again from its original source through the official `claude plugin` CLI.

Not saved, ever: plugin files, plugin settings or data, MCP server configs, local paths, credentials or tokens
(credentials found in source URLs are removed and reported).

Consequences:
- The marketplace may ship a newer version than the one in the snapshot; the import report flags it.
- If a plugin's source repo disappears, it can no longer be reinstalled.
- Plugin options and saved data must be set up again on the new machine.

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
| `/plugdrop:plugdrop` | Asks: export or import? |
| `/plugdrop:export` | Export directly. `--dry-run` shows what would be saved without writing anything. |
| `/plugdrop:import` | Import directly. `--dry-run` stops after showing the plan. |

On first run plugdrop checks `git`, `gh` and your GitHub login, asks for author, machine and repo name, creates the
private repo if needed, clones it into `~/.plugdrop/repo/` and saves `~/.plugdrop/config.json`.

### Categories

`categories.json` at the root of your snapshot repo maps `plugin@marketplace` to a category, for example
`{"superpowers@claude-plugins-official": "method"}`. During export Claude offers to fill in missing ones. You can
edit the file by hand at any time. Import can install a single category.

### Plugin classes

| In the snapshot | Meaning | On import |
|---|---|---|
| `portable` | marketplace from GitHub, a git URL or a web URL | can be installed |
| `local` | marketplace from a folder or file on the source machine | shown as not portable |
| `non-user-scope` | project, local, session (`--plugin-dir`) or claude.ai-synced plugins | shown as info only |

## Rules plugdrop follows

- Never overwrites or deletes a snapshot.
- Never uninstalls or disables a plugin. A plugin that was disabled on the source machine is reported, not changed.
- Never reads `.credentials.json`, `~/.claude.json` or other credential files, and never edits `settings.json`:
  installs go through `claude plugin install` only.
- The snapshot repo must be private: plugdrop refuses to use an existing public repo.
- No telemetry. Network access happens only through `git`, `gh` and the `claude` CLI.

## Privacy

Your snapshots contain the author and machine names you choose and the list of your plugins. They are stored only
in your own private GitHub repo.

## Limits (v1)

- Only plugins. Standalone skills, MCP servers, hooks, commands, agents, settings and `CLAUDE.md` are out of scope.
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

## License

MIT. See [LICENSE](LICENSE).
