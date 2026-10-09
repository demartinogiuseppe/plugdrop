---
description: Install plugins from one of your snapshots, choosing which ones (plugdrop)
argument-hint: "[--dry-run]"
allowed-tools: Bash(python:*), Bash(py:*), Bash(python3:*)
---

# plugdrop import

Talk to the user in their language. Run the script as described in the "Running the script" section of
`${CLAUDE_PLUGIN_ROOT}/scripts/shared-instructions.md`. If `config` says plugdrop is not configured, do its "Setup"
section first.

Arguments: $ARGUMENTS. If they contain `--dry-run`, stop after showing the plan in step 5.

plugdrop only installs plugins. It never uninstalls or disables anything.

1. Run `snapshots`. If the list is empty, say there is nothing to import yet and stop.
2. Show the snapshots, newest first: date, machine, author, note, number of plugins. Let the user pick one.
3. Run `import-plan --snapshot "<file>"` and show each item with its status:
   - `installed` → already installed
   - `to-install` → to install
   - `not-portable` → not portable (local source)
   - `info-only` → info only (not user scope)
   If there is nothing `to-install`, say everything is already here and stop.
4. Ask what to install, considering only `to-install` items:
   - all
   - a category (offer `categories_available`; only if not empty)
   - item by item (let the user choose several)
   - none
5. Run `import --snapshot "<file>" <choice> --dry-run`, where `<choice>` is `--all`, `--category "<name>"`,
   `--ids "<id>,<id>"` or `--none`. Ask for confirmation, putting the full plan (what will be installed and
   which marketplaces will be added, as a table) **inside the question itself** (for example in the confirm
   option's preview): text before a question may be collapsed and the user would not see the plan.
6. If confirmed, run the same command without `--dry-run`.
7. Final report:
   - installed (mention `version_changed` items: the marketplace now ships a different version than the snapshot)
   - already installed, skipped, not portable, info only
   - failed, with each `error`
   - items with `was_disabled: true`: they were disabled on the source machine; plugdrop did not change that,
     the user can disable them with `/plugin` if they want
8. Remind the user to restart Claude Code to load the new plugins.
