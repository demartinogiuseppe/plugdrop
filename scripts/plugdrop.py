#!/usr/bin/env python3
"""plugdrop: save the list of installed Claude Code plugins to a private GitHub repo and reinstall it elsewhere.

This script does the deterministic work only (reading, writing, git, CLI calls). The dialog with the
user lives in commands/*.md. Every subcommand prints exactly one JSON object on stdout.

Safety rules enforced here:
- snapshots are written with exclusive create: an existing file is never overwritten or deleted;
- plugins are only ever installed, never uninstalled or disabled;
- plugin data comes from the official CLI (`claude plugin list --json`), never from credential files;
- snapshot entries are built from a whitelist of fields, and credentials are stripped from URLs.
"""
import argparse
import datetime as dt
import getpass
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

if sys.version_info < (3, 8):
    sys.exit('{"ok": false, "error": "plugdrop needs Python 3.8 or newer."}')

PLUGDROP_VERSION = 1
USER_SCOPE = "user"
SECRET_PARAM = re.compile(r"token|auth|key|secret|passw|pwd|sig|credential", re.I)
UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
PORTABILITY_ORDER = {"portable": 0, "local": 1, "non-user-scope": 2}

INSTALLED, TO_INSTALL, NOT_PORTABLE, INFO_ONLY = "installed", "to-install", "not-portable", "info-only"


class PlugdropError(Exception):
    pass


# ---------------------------------------------------------------- pure logic (unit tested)

def split_id(plugin_id):
    """'name@marketplace' -> (name, marketplace). Splits on the last '@'."""
    name, sep, marketplace = plugin_id.rpartition("@")
    return (name, marketplace) if sep else (plugin_id, "")


def strip_credentials(url):
    """Return (clean_url, removed). Drops http(s) userinfo and secret-looking query parameters."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return url, False
    removed = False
    netloc = parts.netloc
    if "@" in netloc:
        netloc = netloc.rpartition("@")[2]
        removed = True
    query = parts.query
    if query:
        params = parse_qsl(query, keep_blank_values=True)
        kept = [(k, v) for k, v in params if not SECRET_PARAM.search(k)]
        if len(kept) != len(params):
            query = urlencode(kept)
            removed = True
    if not removed:
        return url, False
    return urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment)), True


def marketplace_source(marketplace):
    """For one `marketplace list --json` entry return (source for `marketplace add` or None, type, cleaned)."""
    kind = marketplace.get("source") or "unknown"
    ref = marketplace.get("ref")
    if kind == "github" and marketplace.get("repo"):
        return marketplace["repo"] + (f"@{ref}" if ref else ""), kind, False
    if kind in ("git", "url") and marketplace.get("url"):
        url, cleaned = strip_credentials(marketplace["url"])
        if kind == "git" and ref:
            url += f"#{ref}"
        return url, kind, cleaned
    return None, kind, False


def build_entries(plugins, marketplaces):
    """Turn CLI output into snapshot entries (whitelisted fields only). Returns (entries, warnings)."""
    by_name = {m.get("name"): m for m in marketplaces}
    entries, warnings = [], []
    for plugin in plugins:
        name, marketplace = split_id(plugin.get("id", ""))
        source, kind, cleaned = None, "unknown", False
        if marketplace in by_name:
            source, kind, cleaned = marketplace_source(by_name[marketplace])
        warning = f"Removed credentials from the source URL of marketplace '{marketplace}'."
        if cleaned and warning not in warnings:
            warnings.append(warning)
        scope = plugin.get("scope", "")
        if scope != USER_SCOPE:
            portability = "non-user-scope"
        elif source:
            portability = "portable"
        else:
            portability = "local"
        entries.append({
            "name": name,
            "marketplace": marketplace,
            "marketplace_source": source,
            "marketplace_source_type": kind,
            "version": plugin.get("version"),
            "scope": scope,
            "enabled": bool(plugin.get("enabled", True)),
            "portability": portability,
        })
    entries.sort(key=lambda e: (PORTABILITY_ORDER[e["portability"]], e["name"]))
    return entries, warnings


def safe_part(text):
    return UNSAFE_FILENAME_CHARS.sub("-", text).strip("-") or "unknown"


def snapshot_filename(when, machine, author, existing):
    base = f"{when:%Y-%m-%d_%H%M%S}_{safe_part(machine)}_{safe_part(author)}"
    name, n = f"{base}.json", 1
    while name in existing:
        n += 1
        name = f"{base}-{n}.json"
    return name


def write_snapshot(folder, data, when, machine, author):
    """Write `data` to a new file in `folder` and return its name. Never overwrites."""
    folder.mkdir(parents=True, exist_ok=True)
    existing = {p.name for p in folder.iterdir()}
    while True:
        name = snapshot_filename(when, machine, author, existing)
        try:
            with open(folder / name, "x", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            return name
        except FileExistsError:
            existing.add(name)


def plan_statuses(snapshot_plugins, installed_ids):
    """Compare snapshot entries with the user-scope plugin ids installed here."""
    plan = []
    for entry in snapshot_plugins:
        plugin_id = f"{entry['name']}@{entry['marketplace']}"
        if entry.get("portability") == "non-user-scope":
            status = INFO_ONLY
        elif plugin_id in installed_ids:
            status = INSTALLED
        elif entry.get("portability") != "portable":
            status = NOT_PORTABLE
        else:
            status = TO_INSTALL
        plan.append({"id": plugin_id, "status": status, **entry})
    return plan


def select(plan, mode, categories=None, values=()):
    """Pick the `to-install` items by mode: all | none | category | ids."""
    todo = [item for item in plan if item["status"] == TO_INSTALL]
    if mode == "all":
        return todo
    if mode == "none":
        return []
    if mode == "category":
        categories = categories or {}
        return [item for item in todo if categories.get(item["id"]) in values]
    if mode == "ids":
        return [item for item in todo if item["id"] in values]
    raise PlugdropError(f"Unknown selection mode: {mode}")


def install(chosen, known_marketplaces, run, dry_run=False):
    """Add missing marketplaces and install each plugin at user scope. One failure never stops the rest."""
    known = set(known_marketplaces)
    report = []
    for item in chosen:
        add = item["marketplace"] not in known
        row = {"id": item["id"], "add_marketplace": add, "snapshot_version": item.get("version"),
               "was_disabled": not item.get("enabled", True)}
        if dry_run:
            known.add(item["marketplace"])
            report.append({**row, "result": "would-install"})
            continue
        try:
            if add:
                _check(run(["plugin", "marketplace", "add", item["marketplace_source"], "--scope", USER_SCOPE]))
                known.add(item["marketplace"])
            _check(run(["plugin", "install", item["id"], "--scope", USER_SCOPE]))
            report.append({**row, "result": "installed"})
        except PlugdropError as exc:
            report.append({**row, "result": "failed", "error": str(exc)})
    return report


def _check(completed):
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "").strip()
        raise PlugdropError(message or f"exit code {completed.returncode}")
    return completed


# ---------------------------------------------------------------- side effects

def plugdrop_home():
    return Path(os.environ.get("PLUGDROP_HOME") or Path.home() / ".plugdrop")


def config_path():
    return plugdrop_home() / "config.json"


def run_tool(tool, args, cwd=None, timeout=600):
    exe = shutil.which(tool)
    if not exe:
        raise PlugdropError(f"'{tool}' not found on PATH.")
    return subprocess.run([exe, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL, timeout=timeout)


def claude(args):
    return run_tool("claude", args)


def git(args, cwd):
    return _check(run_tool("git", args, cwd=cwd))


def claude_json(args):
    return json.loads(_check(claude(args)).stdout)


def installed_plugins():
    return claude_json(["plugin", "list", "--json"])


def known_marketplaces():
    return claude_json(["plugin", "marketplace", "list", "--json"])


def claude_version():
    out = _check(claude(["--version"])).stdout.strip()
    return out.split()[0] if out else "unknown"


def gh_user():
    result = run_tool("gh", ["api", "user", "--jq", ".login"])
    return result.stdout.strip() if result.returncode == 0 else ""


def requirement_problems():
    problems = []
    hints = {"git": "Install Git: https://git-scm.com/downloads",
             "gh": "Install GitHub CLI: https://cli.github.com",
             "claude": "The Claude Code CLI ('claude') must be on PATH."}
    for tool, hint in hints.items():
        if not shutil.which(tool):
            problems.append(f"'{tool}' not found. {hint}")
    if shutil.which("gh") and run_tool("gh", ["auth", "status"]).returncode != 0:
        problems.append("GitHub CLI is not logged in. Run: gh auth login")
    return problems


def load_config():
    path = config_path()
    if not path.exists():
        raise PlugdropError("plugdrop is not set up yet. Run the setup first.")
    return json.loads(path.read_text(encoding="utf-8"))


def repo_dir(config):
    return Path(config["local_path"]).expanduser()


def read_categories(folder):
    path = folder / "categories.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def list_snapshots(folder):
    snapshots, warnings = [], []
    for path in sorted((folder / "snapshots").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            warnings.append(f"Skipped unreadable snapshot {path.name}: {exc}")
            continue
        snapshots.append({"file": path.name, "created_at": data.get("created_at", ""),
                          "machine": data.get("machine"), "author": data.get("author"),
                          "note": data.get("note"), "plugins": len(data.get("plugins", []))})
    snapshots.sort(key=lambda s: (s["created_at"], s["file"]), reverse=True)
    return snapshots, warnings


def load_snapshot(folder, name):
    if Path(name).name != name or not name.endswith(".json"):
        raise PlugdropError(f"Invalid snapshot name: {name}")
    path = folder / "snapshots" / name
    if not path.exists():
        raise PlugdropError(f"Snapshot not found: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def user_scope_ids(plugins):
    return {p["id"] for p in plugins if p.get("scope") == USER_SCOPE}


# ---------------------------------------------------------------- subcommands

SNAPSHOT_REPO_README = """# plugdrop snapshots

Private repository written by the plugdrop plugin for Claude Code.

- `snapshots/`: one JSON file per export. Files are never overwritten.
- `categories.json`: `{"plugin@marketplace": "category"}`. Shared by all snapshots; edit it freely.

Snapshots contain only plugin names, marketplace sources and versions: no plugin code, no credentials.
"""


def cmd_check(_args):
    problems = requirement_problems()
    return {"ok": not problems, "problems": problems}


def cmd_config(_args):
    if config_path().exists():
        return {"ok": True, "configured": True, "config": load_config()}
    author = (gh_user() if shutil.which("gh") else "") or getpass.getuser()
    return {"ok": True, "configured": False,
            "defaults": {"author": author, "machine": socket.gethostname(), "repo": "plugdrop-snapshots"}}


def cmd_setup(args):
    problems = requirement_problems()
    if problems:
        return {"ok": False, "problems": problems}
    owner = gh_user()
    full_name = args.repo if "/" in args.repo else f"{owner}/{args.repo}"
    local = plugdrop_home() / "repo"
    actions = []

    view = run_tool("gh", ["repo", "view", full_name, "--json", "visibility", "--jq", ".visibility"])
    if view.returncode == 0:
        if view.stdout.strip().upper() != "PRIVATE":
            return {"ok": False, "error": f"{full_name} exists but is not private. plugdrop only uses private repos."}
        actions.append(f"use existing private repo {full_name}")
    else:
        actions.append(f"create private repo {full_name}")
    if not (local / ".git").exists():
        actions.append(f"clone into {local}")
    actions.append("add README.md, categories.json, snapshots/ if missing")
    actions.append(f"save {config_path()}")
    if args.dry_run:
        return {"ok": True, "dry_run": True, "actions": actions}

    if view.returncode != 0:
        _check(run_tool("gh", ["repo", "create", full_name, "--private",
                               "--description", "Claude Code plugin snapshots (plugdrop)"]))
    if not (local / ".git").exists():
        local.parent.mkdir(parents=True, exist_ok=True)
        _check(run_tool("gh", ["repo", "clone", full_name, str(local)]))

    created = []
    for rel, content in (("README.md", SNAPSHOT_REPO_README), ("categories.json", "{}\n"),
                         ("snapshots/.gitkeep", "")):
        path = local / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            created.append(rel)
    if created:
        git(["add", *created], local)
        git(["commit", "-m", "plugdrop: initialize snapshot repo"], local)
        git(["push", "-u", "origin", "HEAD"], local)

    config = {"author": args.author, "machine": args.machine, "repo": full_name, "local_path": str(local)}
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "actions": actions, "config": config}


def _current_entries():
    return build_entries(installed_plugins(), known_marketplaces())


def cmd_export_preview(_args):
    config = load_config()
    folder = repo_dir(config)
    git(["pull", "--ff-only"], folder)
    entries, warnings = _current_entries()
    categories = read_categories(folder)
    ids = [f"{e['name']}@{e['marketplace']}" for e in entries if e["portability"] != "non-user-scope"]
    counts = {k: sum(1 for e in entries if e["portability"] == k) for k in PORTABILITY_ORDER}
    return {"ok": True, "machine": config["machine"], "author": config["author"], "counts": counts,
            "plugins": entries, "warnings": warnings, "categories": categories,
            "uncategorized": [i for i in ids if i not in categories]}


def cmd_export(args):
    note = (args.note or "").strip()
    if not note:
        return {"ok": False, "error": "A note is required."}
    config = load_config()
    folder = repo_dir(config)
    if not args.dry_run:
        git(["pull", "--ff-only"], folder)
    entries, warnings = _current_entries()
    now = dt.datetime.now().astimezone()
    snapshot = {"plugdrop_version": PLUGDROP_VERSION, "created_at": now.isoformat(timespec="seconds"),
                "author": config["author"], "machine": config["machine"], "note": note,
                "claude_code_version": claude_version(), "plugins": entries}
    new_categories = dict(c.split("=", 1) for c in args.category)
    counts = {k: sum(1 for e in entries if e["portability"] == k) for k in PORTABILITY_ORDER}
    if args.dry_run:
        existing = {p.name for p in (folder / "snapshots").glob("*.json")}
        name = snapshot_filename(now, config["machine"], config["author"], existing)
        return {"ok": True, "dry_run": True, "would_write": f"snapshots/{name}", "counts": counts,
                "categories_to_set": new_categories, "warnings": warnings}

    to_add = []
    if new_categories:
        categories = read_categories(folder)
        categories.update(new_categories)
        (folder / "categories.json").write_text(
            json.dumps(dict(sorted(categories.items())), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        to_add.append("categories.json")
    name = write_snapshot(folder / "snapshots", snapshot, now, config["machine"], config["author"])
    to_add.append(f"snapshots/{name}")
    git(["add", *to_add], folder)
    git(["commit", "-m", f"plugdrop: export {config['machine']} {now:%Y-%m-%d}"], folder)
    result = {"ok": True, "snapshot": f"snapshots/{name}", "counts": counts, "warnings": warnings,
              "repo": config["repo"]}
    try:
        git(["push"], folder)
        result["pushed"] = True
    except PlugdropError as exc:
        result.update(pushed=False, push_error=str(exc),
                      hint="The snapshot is committed locally; the next export will push it.")
    return result


def cmd_snapshots(_args):
    folder = repo_dir(load_config())
    git(["pull", "--ff-only"], folder)
    snapshots, warnings = list_snapshots(folder)
    return {"ok": True, "snapshots": snapshots, "warnings": warnings}


def _plan(folder, snapshot_name):
    snapshot = load_snapshot(folder, snapshot_name)
    plan = plan_statuses(snapshot.get("plugins", []), user_scope_ids(installed_plugins()))
    categories = read_categories(folder)
    for item in plan:
        item["category"] = categories.get(item["id"])
    return snapshot, plan, categories


def cmd_import_plan(args):
    folder = repo_dir(load_config())
    git(["pull", "--ff-only"], folder)
    snapshot, plan, _ = _plan(folder, args.snapshot)
    todo = [i for i in plan if i["status"] == TO_INSTALL]
    return {"ok": True, "snapshot": args.snapshot, "machine": snapshot.get("machine"),
            "note": snapshot.get("note"), "items": plan,
            "counts": {s: sum(1 for i in plan if i["status"] == s)
                       for s in (INSTALLED, TO_INSTALL, NOT_PORTABLE, INFO_ONLY)},
            "categories_available": sorted({i["category"] for i in todo if i["category"]})}


def cmd_import(args):
    folder = repo_dir(load_config())
    _, plan, categories = _plan(folder, args.snapshot)
    if args.all:
        chosen = select(plan, "all")
    elif args.category:
        chosen = select(plan, "category", categories, args.category)
    elif args.ids:
        wanted = [i.strip() for i in args.ids.split(",") if i.strip()]
        chosen = select(plan, "ids", values=wanted)
    else:
        chosen = []
    marketplaces = {m.get("name") for m in known_marketplaces()}
    report = install(chosen, marketplaces, claude, dry_run=args.dry_run)

    if not args.dry_run and any(r["result"] == "installed" for r in report):
        versions = {p["id"]: p.get("version") for p in installed_plugins() if p.get("scope") == USER_SCOPE}
        for row in report:
            if row["result"] == "installed":
                row["installed_version"] = versions.get(row["id"])
                row["version_changed"] = row["installed_version"] != row["snapshot_version"]

    chosen_ids = {i["id"] for i in chosen}
    return {"ok": True, "dry_run": args.dry_run, "results": report,
            "already_installed": [i["id"] for i in plan if i["status"] == INSTALLED],
            "skipped": [i["id"] for i in plan if i["status"] == TO_INSTALL and i["id"] not in chosen_ids],
            "not_portable": [i["id"] for i in plan if i["status"] == NOT_PORTABLE],
            "info_only": [i["id"] for i in plan if i["status"] == INFO_ONLY]}


def build_parser():
    parser = argparse.ArgumentParser(prog="plugdrop", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="verify git, gh, gh login and claude").set_defaults(func=cmd_check)
    sub.add_parser("config", help="show config, or defaults if not set up").set_defaults(func=cmd_config)

    p = sub.add_parser("setup", help="first-run setup")
    p.add_argument("--author", required=True)
    p.add_argument("--machine", required=True)
    p.add_argument("--repo", default="plugdrop-snapshots")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_setup)

    sub.add_parser("export-preview", help="what an export would contain").set_defaults(func=cmd_export_preview)

    p = sub.add_parser("export", help="write a new snapshot, commit and push")
    p.add_argument("--note", required=True)
    p.add_argument("--category", action="append", default=[], metavar="PLUGIN@MARKETPLACE=CATEGORY")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_export)

    sub.add_parser("snapshots", help="list snapshots, newest first").set_defaults(func=cmd_snapshots)

    p = sub.add_parser("import-plan", help="compare a snapshot with this machine")
    p.add_argument("--snapshot", required=True)
    p.set_defaults(func=cmd_import_plan)

    p = sub.add_parser("import", help="install the chosen plugins from a snapshot")
    p.add_argument("--snapshot", required=True)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true")
    group.add_argument("--category", action="append", metavar="CATEGORY")
    group.add_argument("--ids", metavar="ID,ID,...")
    group.add_argument("--none", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_import)
    return parser


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    try:
        result = args.func(args)
    except (PlugdropError, subprocess.TimeoutExpired, ValueError, KeyError) as exc:
        result = {"ok": False, "error": str(exc)}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
