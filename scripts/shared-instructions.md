# plugdrop: shared instructions

Used by `commands/export.md` and `commands/import.md`. Talk to the user in their language. Keep messages short.

## Running the script

All deterministic work is done by one script. Run it as:

```
python "${CLAUDE_PLUGIN_ROOT}/scripts/plugdrop.py" <subcommand> [options]
```

If `python` is missing or prints a Microsoft Store message, retry with `py -3`, then `python3`. Use the first one
that works for the rest of the session. If none works, tell the user that plugdrop needs Python 3.8+
(https://www.python.org/downloads/) and stop.

Every subcommand prints one JSON object. If it has `"ok": false`, show the `error` or `problems` to the user in
plain words and stop.

## MCP notice

At the end of every export and import report (guided and direct), add this notice once, in the user's language:
"MCP servers you added yourself (`claude mcp add`) are deliberately not exported or imported: they often contain
private keys. MCP servers that come with a plugin are reinstalled together with the plugin. On the new machine,
add your own again with `claude mcp add`."

## Setup (first run only)

1. Run `config`. If `configured` is true, setup is done.
2. Ask where to keep the snapshots (one question):
   - **GitHub** (first option, recommended): a private repo that plugdrop creates and checks is private. Needs
     `git` and the GitHub CLI `gh`, logged in. Backend `github`.
   - **Another git server** (GitLab, Gitea, your own...): an existing repo the user created. Needs `git`. plugdrop
     cannot check that it is private. Backend `git`.
   - **A synced folder** (OneDrive, Dropbox, Google Drive, a network share...): no git and no account. plugdrop
     cannot check who can read it. Backend `folder`.
3. Run `check --backend <backend>`. If there are `problems`, explain each one with its fix and stop.
4. Ask, showing the `defaults` from `config` as suggested values the user can just confirm: author name, machine
   name, and the store: for `github` the repo name (default from `config`), for `git` the repo URL (https or ssh,
   without passwords in it), for `folder` the full folder path (suggest a `plugdrop` folder inside their synced
   folder; use the same folder on every machine).
5. Run `setup --backend <backend> --author "<author>" --machine "<machine>" --repo "<repo, URL or folder>"`.
6. Tell the user what happened (`actions`) and show every item of `warnings`.

## Switching repo

If the command arguments contain `--repo <name>` (a GitHub repo name or `owner/name`, a git URL, or a folder
path), do this before anything else: run `config`, then `setup --backend <backend> --author "<author>" --machine
"<machine>" --repo "<name>"` with author and machine from the current config (ask for them only if plugdrop is not
configured yet). The backend follows from the value: a folder path → `folder`, an https/ssh URL → `git`, otherwise
`github`; ask if unsure. Tell the user which store is now in use; the old copy is kept, never deleted. Then go on
with the command.
