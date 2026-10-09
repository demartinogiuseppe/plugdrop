---
description: Install plugins, skills, commands, agents and your CLAUDE.md from one of your snapshots, choosing what (plugdrop)
argument-hint: "[--direct] [--dry-run] [--diff] [--repo <name>]"
allowed-tools: Bash(python:*), Bash(py:*), Bash(python3:*)
---

# plugdrop import

Talk to the user in their language. Run the script as described in the "Running the script" section of
`${CLAUDE_PLUGIN_ROOT}/scripts/shared-instructions.md`. If `config` says plugdrop is not configured, do its "Setup"
section first.

Arguments: $ARGUMENTS. If they contain `--dry-run`, stop after showing the plan in step 5. If they contain
`--repo <name>`, first follow "Switching repo" in the shared instructions.

plugdrop only adds. It never uninstalls or disables a plugin, never overwrites a skill, command or agent that
already exists here, and only ever appends to `CLAUDE.md` (after a backup copy). **The user always chooses what
to import**, in every mode: never import a kind of item the user did not pick.

Item kinds: `plugin`, `skill`, `command`, `agent`, `claude-md` (the personal `CLAUDE.md`, one item at most).

## Compare only (`--diff`)

If the arguments contain `--diff`, install nothing and ask no guided/direct question. Run `snapshots` and let the
user pick a snapshot (the newest is the first option), then run `import-plan --snapshot "<file>" --diff` and show,
grouped by kind:
- **only in the snapshot**: `to-install` items (missing here); for `CLAUDE.md` in `to-merge`, its missing `blocks`
- **only here**: `only_here` (not in the snapshot; an export would add them)
- **different**: `different` items (same name, different content)
- **same**: just the count of `installed` items
Mention `not-portable` and `info-only` items only as counts. When a group is long, give the count and the first
names. Then stop: say that `/plugdrop:import` installs what is missing and `/plugdrop:export` saves what is only
here.

## Mode

If the arguments contain `--direct` (or `--diretta`), use direct mode. Otherwise, after setup, ask one question:
**Guided or direct?** Guided (first option): the steps below, choosing snapshot and items one by one if wanted,
with a confirmation. Direct: the most recent snapshot, one question about what to import, no confirmation.

Direct mode:
1. Run `import-plan --snapshot latest`. If nothing is `to-install` or `to-merge`, say everything is already here
   and stop.
2. Ask one question: **what to import?** Options: "Everything missing", then one option per entry of
   `kinds_available` ("Only plugins", "Only skills", "Only commands", "Only agents", "Only CLAUDE.md"), with
   counts. Allow several kinds at once.
3. Run `import --snapshot latest --all` for everything, or `import --snapshot latest --kind <kind>` (repeat
   `--kind` for several), with `--dry-run` if requested. A `CLAUDE.md` in `to-merge` gets all its missing blocks.
4. Give the final report of step 7, saying which snapshot was used (`snapshot`), and the reminder of step 8.

## Guided mode

1. Run `snapshots`. If the list is empty, say there is nothing to import yet and stop.
2. Show the snapshots, newest first: date, machine, author, note, number of plugins. Let the user pick one.
3. Run `import-plan --snapshot "<file>"`. Show items grouped by kind, each with its status:
   - `installed` → already here
   - `to-install` → to install
   - `to-merge` → only for `CLAUDE.md`, see below
   - `different` → exists here with different content; it will not be touched
   - `not-portable` → cannot be installed here (local plugin source, or an item that was not saved; show `reason`)
   - `info-only` → info only (plugin not in user scope)
   When a kind has many items, give the counts and list only the `to-install` and `different` ones.
   For `CLAUDE.md`:
   - `to-install`: it does not exist here and will be created as it is.
   - `installed`: everything in the snapshot's copy is already here.
   - `to-merge`: it exists here with different content. Show `already_present` (blocks already here), each item of
     `blocks` (its `title` and line count; these are missing here) and `local_only` (blocks only this machine has,
     which stay as they are).
   For each block, warn about: `needs` (plugins or skills it mentions that are not installed here: the instruction
   has no effect without them; say whether they are in the snapshot and can be selected), `machine_paths` (paths of
   the source machine that may not exist here), `imports` (`@` files that plugdrop does not carry).
   If nothing is `to-install` or `to-merge`, say everything is already here and stop.
4. Ask what to import, considering only `to-install` and `to-merge` items (allow several answers):
   - everything missing
   - one or more kinds (offer `kinds_available`)
   - a category (offer `categories_available`; only if not empty)
   - item by item (let the user choose several)
   - none
   If the choice includes a `CLAUDE.md` in `to-merge`, ask in a separate question: add all missing blocks
   (first option), choose blocks one by one, or leave `CLAUDE.md` alone (then drop it from the choice). Put in the
   question's preview the end of the file after the merge: the last lines of the current file, the marker line
   `<!-- plugdrop: added on <date> from <machine> -->`, then the full `text` of the blocks to add. Say below it that no
   existing line is changed or removed and that a backup copy is made first.
5. Run `import --snapshot "<file>" <choice> --dry-run`, where `<choice>` is `--all`, `--kind "<kind>"` (repeatable),
   `--category "<name>"`, `--ids "<id>,<id>"` or `--none`, plus `--blocks "<n>,<n>"` when only some `CLAUDE.md`
   blocks were chosen. Ask for confirmation, putting the full plan (what will be installed, merged and which
   marketplaces will be added, as a table) **inside the question itself** (for example in the confirm option's
   preview): text before a question may be collapsed and the user would not see the plan.
6. If confirmed, run the same command without `--dry-run`.
7. Final report:
   - installed (mention `version_changed` plugins: the marketplace now ships a different version than the snapshot)
   - `CLAUDE.md`: `merged` with the `added_blocks` and the `backup` path, or created, or unchanged
   - already installed, skipped, different (not touched), not portable, info only
   - failed, with each `error`
   - plugins with `was_disabled: true`: they were disabled on the source machine; plugdrop did not change that,
     the user can disable them with `/plugin` if they want
   - skills installed with `source: git` were cloned from their repo; if that repo has its own setup step (for
     example a `setup` script in its README), the user has to run it
8. Remind the user to restart Claude Code to load the new plugins, skills, commands, agents and `CLAUDE.md`, then
   add the "MCP notice" of the shared instructions (also in direct mode).
