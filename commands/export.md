---
description: Save the list of installed plugins as a new snapshot in your private repo (plugdrop)
argument-hint: "[--dry-run]"
allowed-tools: Bash(python:*), Bash(py:*), Bash(python3:*)
---

# plugdrop export

Talk to the user in their language. Run the script as described in the "Running the script" section of
`${CLAUDE_PLUGIN_ROOT}/scripts/shared-instructions.md`. If `config` says plugdrop is not configured, do its "Setup"
section first.

Arguments: $ARGUMENTS. If they contain `--dry-run`, pass `--dry-run` to the final `export` call.

Export saves the **list** of plugins (name, marketplace, source, version), never the plugins' files.

1. Run `export-preview`.
2. Show a summary grouped by `portability`, with counts:
   - **Exportable** (`portable`): name, marketplace, version.
   - **Not portable** (`local`): installed from a local folder, cannot be reinstalled elsewhere.
   - **Info only** (`non-user-scope`): project, session or claude.ai-synced plugins; recorded, not reinstallable.
   Show every item of `warnings` (for example credentials removed from a URL: say the token is NOT saved).
3. If `uncategorized` is not empty, ask whether to assign categories, with three options:
   "Yes, suggest them", "Yes, one by one", "Skip".
   - Suggest: propose one grouping for all of them (reuse category names already in `categories` when they
     fit) and let the user approve or correct it. Put the full proposal, as a table with one row per category
     and its plugins, **inside the question itself** (for example in the approve option's preview), not only
     in the text before it: text before a question may be collapsed and the user would not see the plugins.
   - Categories are optional; skipping is fine.
   If `uncategorized` is empty, ask instead whether to keep the current categories or review them, with the
   current assignments as a table (one row per category and its plugins) inside the question itself, for example
   in the "Keep" option's preview. "Keep" is the first option. On "Review", propose a new grouping for all plugins
   exactly as for "Yes, suggest them" above, and pass every changed assignment with `--category`.
   Ask every question of steps 3 and 4 on its own: never put the category approval and the note in the same
   prompt (combined with a preview, the user may be unable to answer either).
4. Ask for a **note** for this snapshot. It is required: if the user gives an empty answer, ask again.
5. Run `export --note "<note>"`, adding one `--category "<plugin@marketplace>=<category>"` per assigned category.
   Escape any double quotes inside the note.
6. Report: the snapshot file name, the counts, and the repo. If `pushed` is false, show `push_error` and `hint`.
   In dry-run, say clearly that nothing was written or pushed.
