---
description: Install plugins, skills, commands and agents from one of your snapshots, choosing which ones (plugdrop)
argument-hint: "[--direct] [--dry-run] [--diff] [--repo <name>]"
allowed-tools: Bash(python:*), Bash(py:*), Bash(python3:*)
---

# plugdrop import

Talk to the user in their language. Run the script as described in the "Running the script" section of
`${CLAUDE_PLUGIN_ROOT}/scripts/shared-instructions.md`. If `config` says plugdrop is not configured, do its "Setup"
section first.

Arguments: $ARGUMENTS. If they contain `--dry-run`, stop after showing the plan in step 5. If they contain
`--repo <name>`, first follow "Switching repo" in the shared instructions.

## Compare only (`--diff`)

If the arguments contain `--diff`, install nothing and ask no guided/direct question. Run `snapshots` and let the
user pick a snapshot (the newest is the first option), then run `import-plan --snapshot "<file>" --diff` and show,
grouped by kind (`plugin`, `skill`, `command`, `agent`):
- **only in the snapshot**: `to-install` items (missing here)
- **only here**: `only_here` (not in the snapshot; an export would add them)
- **different**: `different` items (same name, different content)
- **same**: just the count of `installed` items
Mention `not-portable` and `info-only` items only as counts. When a group is long, give the count and the first
names. Then stop: say that `/plugdrop:import` installs what is missing and `/plugdrop:export` saves what is only
here.

plugdrop only adds. It never uninstalls or disables a plugin, and never overwrites a skill, command or agent
that already exists here.

## Mode

If the arguments contain `--direct` (or `--diretta`), use direct mode. Otherwise, after setup, ask one question:
**Guided or direct?** Guided (first option): the steps below, choosing snapshot and items. Direct: the most
recent snapshot, everything missing, no questions and no confirmation.

Direct mode: run `import --snapshot latest --all` (with `--dry-run` if requested). Then give the final report of
step 7, saying which snapshot was used (`snapshot`), and the reminder of step 8. Stop there.

## Guided mode

1. Run `snapshots`. If the list is empty, say there is nothing to import yet and stop.
2. Show the snapshots, newest first: date, machine, author, note, number of plugins. Let the user pick one.
3. Run `import-plan --snapshot "<file>"`. Items have a `kind`: `plugin`, `skill`, `command` or `agent`. Show them
   grouped by kind, each with its status:
   - `installed` → already here
   - `to-install` → to install
   - `different` → exists here with different content; it will not be touched
   - `not-portable` → cannot be installed here (local plugin source, or an item that was not saved; show `reason`)
   - `info-only` → info only (plugin not in user scope)
   When a kind has many items, give the counts and list only the `to-install` and `different` ones.
   If there is nothing `to-install`, say everything is already here and stop.
4. Ask what to install, considering only `to-install` items:
   - all
   - one kind (offer `kinds_available`; only if it has more than one entry)
   - a category (offer `categories_available`; only if not empty)
   - item by item (let the user choose several)
   - none
5. Run `import --snapshot "<file>" <choice> --dry-run`, where `<choice>` is `--all`, `--kind "<kind>"`,
   `--category "<name>"`, `--ids "<id>,<id>"` or `--none`. Ask for confirmation, putting the full plan (what will
   be installed and which marketplaces will be added, as a table) **inside the question itself** (for example in
   the confirm option's preview): text before a question may be collapsed and the user would not see the plan.
6. If confirmed, run the same command without `--dry-run`.
7. Final report:
   - installed (mention `version_changed` plugins: the marketplace now ships a different version than the snapshot)
   - already installed, skipped, different (not touched), not portable, info only
   - failed, with each `error`
   - plugins with `was_disabled: true`: they were disabled on the source machine; plugdrop did not change that,
     the user can disable them with `/plugin` if they want
   - skills installed with `source: git` were cloned from their repo; if that repo has its own setup step (for
     example a `setup` script in its README), the user has to run it
8. Remind the user to restart Claude Code to load the new plugins, skills, commands and agents, then add the
   "MCP notice" of the shared instructions (also in direct mode).
