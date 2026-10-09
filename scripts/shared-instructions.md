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

## Setup (first run only)

1. Run `config`. If `configured` is true, setup is done.
2. Run `check`. If there are `problems`, explain each one with its fix and stop.
3. Ask, showing the `defaults` from `config` as suggested values the user can just confirm:
   author name, machine name, snapshot repo name.
4. Run `setup --author "<author>" --machine "<machine>" --repo "<repo>"`.
5. Tell the user what happened (`actions`): the private repo was created or reused, and where it lives.
