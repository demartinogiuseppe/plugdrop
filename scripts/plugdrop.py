#!/usr/bin/env python3
"""plugdrop: save your Claude Code plugins, skills, commands and agents to a private GitHub repo and reinstall them elsewhere.

This script does the deterministic work only (reading, writing, git, CLI calls). The dialog with the
user lives in commands/*.md. Every subcommand prints exactly one JSON object on stdout.

Safety rules enforced here:
- snapshots are written with exclusive create: an existing file is never overwritten or deleted;
- plugins are only ever installed, never uninstalled or disabled;
- skills, commands and agents are only created where missing, never overwritten; files that look like
  secrets are never copied;
- plugin data comes from the official CLI (`claude plugin list --json`), never from credential files;
- snapshot entries are built from a whitelist of fields, and credentials are stripped from URLs.
"""
import argparse
import datetime as dt
import getpass
import hashlib
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

PLUGDROP_VERSION = 2
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


def only_here(snapshot, here_ids):
    """Ids present on this machine but not in the snapshot."""
    in_snapshot = {f"{p['name']}@{p['marketplace']}" for p in snapshot.get("plugins", [])}
    in_snapshot |= {i["id"] for i in snapshot.get("personal", [])}
    return sorted(set(here_ids) - in_snapshot)


def select(plan, mode, categories=None, values=()):
    """Pick the `to-install` (and `to-merge`) items by mode: all | none | category | ids."""
    todo = [item for item in plan if item["status"] in (TO_INSTALL, TO_MERGE)]
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


def origin_matches(url, full_name):
    """True if a git remote URL points to GitHub repo `owner/name` (https or ssh form)."""
    clean = url.strip().rstrip("/").lower()
    if clean.endswith(".git"):
        clean = clean[:-4]
    want = full_name.lower()
    return clean.endswith("/" + want) or clean.endswith(":" + want)


def clone_dir(home, full_name, config):
    """Local clone for `full_name`: the configured one if it is the same repo, else repos/<owner>/<name>."""
    if config and config.get("repo", "").lower() == full_name.lower():
        return Path(config["local_path"]).expanduser()
    owner, _, name = full_name.partition("/")
    return home / "repos" / safe_part(owner) / safe_part(name)


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


def resolve_snapshot(folder, name):
    """'latest' -> file name of the newest snapshot; any other name is returned unchanged."""
    if name != "latest":
        return name
    snapshots, _ = list_snapshots(folder)
    if not snapshots:
        raise PlugdropError("There are no snapshots yet.")
    return snapshots[0]["file"]


def load_snapshot(folder, name):
    if Path(name).name != name or not name.endswith(".json"):
        raise PlugdropError(f"Invalid snapshot name: {name}")
    path = folder / "snapshots" / name
    if not path.exists():
        raise PlugdropError(f"Snapshot not found: {name}")
    return json.loads(path.read_text(encoding="utf-8"))


def user_scope_ids(plugins):
    return {p["id"] for p in plugins if p.get("scope") == USER_SCOPE}


# ---------------------------------------------------------------- personal skills, commands and agents
#
# Items found in the Claude config dir: skills/<name>/SKILL.md, commands/**/*.md, agents/**/*.md.
# A skill that is a clone of a remote git repo is stored as a reference (url + commit) and cloned on import.
# Everything else is stored as content-addressed blobs in the snapshot repo (files/<sha[:2]>/<sha>).
# An item that looks like it contains a secret, or is too large, is recorded by name only and never copied.
# Import only creates what is missing: an existing skill, command or agent is never overwritten.

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv"}
MAX_FILE_BYTES = 1_000_000
MAX_ITEM_BYTES = 10_000_000
SECRET_FILE = re.compile(r"^(\.env(\..*)?|.*\.(pem|key|p12|pfx)|id_(rsa|dsa|ecdsa|ed25519)|\.?credentials.*)$", re.I)
SECRET_TEXT = re.compile(
    rb"(?<![A-Za-z0-9])(sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
    rb"|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35})"
    rb"|-----BEGIN [A-Z ]*PRIVATE KEY-----")
BAD_PATH_CHARS = re.compile(r'[\\:*?"<>|\x00-\x1f]')
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DIFFERENT, TO_MERGE = "different", "to-merge"
CLAUDE_MD = "CLAUDE.md"
MARKER = re.compile(r"^\s*<!--\s*plugdrop:.*-->\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
MACHINE_PATH = re.compile(r"(?:\b[A-Za-z]:[\\/]|/Users/|/home/)[^\s`'\")]*")
IMPORT_REF = re.compile(r"(?:^|\s)@((?:~/|\./|\.\./)?[\w./-]+\.md)\b", re.M)


def claude_home():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def looks_secret(name, data):
    """True if a file name or its content looks like a credential."""
    if SECRET_FILE.match(name):
        return True
    for match in SECRET_TEXT.finditer(data):
        text = match.group(0)
        if b"EXAMPLE" in text.upper():  # documentation keys such as AKIAIOSFODNN7EXAMPLE
            continue
        if text.startswith(b"-----") or re.search(rb"\d", text):
            return True
    return False


def _excluded(kind, name, reason):
    return {"id": f"{kind}:{name}", "kind": kind, "name": name, "source": "excluded", "reason": reason}


def _read_files(kind, name, files, blobs):
    """files: [(relative posix path, Path)]. Returns (file entries, None) or (None, excluded item)."""
    entries, total = [], 0
    for rel, path in files:
        if path.is_symlink():
            return None, _excluded(kind, name, f"contains a link: {rel}")
        size = path.stat().st_size
        total += size
        if size > MAX_FILE_BYTES or total > MAX_ITEM_BYTES:
            return None, _excluded(kind, name, f"too large: {rel}")
        data = path.read_bytes()
        if looks_secret(path.name, data):
            return None, _excluded(kind, name, f"may contain a secret: {rel}")
        sha = hashlib.sha256(data).hexdigest()
        blobs[sha] = path
        entries.append({"path": rel, "sha256": sha, "size": size})
    return entries, None


def _skill_files(folder):
    files = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for n in sorted(names):
            path = Path(root) / n
            files.append((path.relative_to(folder).as_posix(), path))
    return files


def scan_personal(home, git_origin):
    """Find personal items under `home`. git_origin(path) -> {url, commit, dirty} or None.

    Returns (items, blobs, warnings); blobs maps sha256 -> source file.
    """
    items, blobs, warnings = [], {}, []
    skills = home / "skills"
    for folder in sorted(skills.iterdir()) if skills.is_dir() else []:
        name = folder.name
        if name.startswith(".") or not folder.is_dir() or not (folder / "SKILL.md").is_file():
            continue
        origin = git_origin(folder) if (folder / ".git").exists() else None
        url = (origin or {}).get("url", "")
        if re.match(r"^(https?://|ssh://|git@)", url):
            url, cleaned = strip_credentials(url)
            if cleaned:
                warnings.append(f"Removed credentials from the git URL of skill '{name}'.")
            if origin.get("dirty"):
                warnings.append(f"Skill '{name}' has local changes that are not exported; import clones the remote.")
            items.append({"id": f"skill:{name}", "kind": "skill", "name": name, "source": "git",
                          "url": url, "commit": origin.get("commit")})
            continue
        files, excluded = _read_files("skill", name, _skill_files(folder), blobs)
        items.append(excluded or {"id": f"skill:{name}", "kind": "skill", "name": name, "source": "files",
                                  "files": files})
    for kind, sub in (("command", "commands"), ("agent", "agents")):
        base = home / sub
        for path in sorted(base.rglob("*.md")) if base.is_dir() else []:
            rel = path.relative_to(base)
            if any(part.startswith(".") for part in rel.parts):
                continue
            name = rel.with_suffix("").as_posix()
            files, excluded = _read_files(kind, name, [(rel.name, path)], blobs)
            items.append(excluded or {"id": f"{kind}:{name}", "kind": kind, "name": name, "source": "files",
                                      "files": files})
    path = home / CLAUDE_MD
    if path.is_file():
        files, excluded = _read_files("claude-md", CLAUDE_MD, [(CLAUDE_MD, path)], blobs)
        items.append(excluded or {"id": f"claude-md:{CLAUDE_MD}", "kind": "claude-md", "name": CLAUDE_MD,
                                  "source": "files", "files": files})
    for item in items:
        if item["source"] == "excluded":
            warnings.append(f"Not exported: {item['id']} ({item['reason']}).")
    return items, blobs, warnings


# CLAUDE.md is merged, not replaced: blocks of the snapshot's file that are missing here are appended at the end,
# under a plugdrop marker comment, after a backup copy. Nothing already in the file is changed or removed.

def split_blocks(text):
    """Split markdown into blocks. A heading starts a block that runs to the next heading; before the first
    heading every paragraph is a block. Headings inside code fences don't count; plugdrop markers are dropped."""
    blocks, current, in_fence, headed = [], [], False, False

    def flush():
        if any(line.strip() for line in current):
            blocks.append("\n".join(current).strip("\n"))
        current.clear()

    for line in text.replace("\r\n", "\n").split("\n"):
        if MARKER.match(line):
            continue
        if FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and line.startswith("#"):
            flush()
            headed = True
        elif not in_fence and not headed and not line.strip():
            flush()
            continue
        current.append(line)
    flush()
    return blocks


def _normalized(text):
    lines = (line.strip() for line in text.replace("\r\n", "\n").split("\n"))
    return "\n" + "\n".join(line for line in lines if line and not MARKER.match(line)) + "\n"


def missing_blocks(snapshot_text, local_text):
    """Blocks of the snapshot's CLAUDE.md whose lines do not already appear, in order, in the local one."""
    local = _normalized(local_text)
    return [b for b in split_blocks(snapshot_text) if _normalized(b) not in local]


def block_notes(block, names):
    """What a block depends on: plugin/skill names it mentions, machine paths, @-imported files."""
    mentions = [n for n in sorted(names) if re.search(
        rf"`{re.escape(n)}`|skills/{re.escape(n)}\b|(?<![\w/-]){re.escape(n)}:|(?<![\w/-])/{re.escape(n)}\b", block)]
    return {"mentions": mentions, "machine_paths": MACHINE_PATH.findall(block), "imports": IMPORT_REF.findall(block)}


def _backup_path(target, when):
    base = f"{target.name}.plugdrop-backup-{when:%Y-%m-%d}"
    path, n = target.with_name(base), 1
    while path.exists():
        n += 1
        path = target.with_name(f"{base}-{n}")
    return path


def merge_claude_md(item, home, folder, machine, when, numbers=None):
    """Append the chosen missing blocks (1-based `numbers`, all if empty). Returns (added, backup path)."""
    target = target_path(home, item)
    snapshot_text = _read_blob(folder, item["files"][0]["sha256"]).decode("utf-8")
    raw = target.read_bytes()
    local = raw.decode("utf-8")
    missing = missing_blocks(snapshot_text, local)
    chosen = [b for i, b in enumerate(missing, 1) if not numbers or i in numbers]
    if not chosen:
        return [], None
    backup = _backup_path(target, when)
    with open(backup, "xb") as f:
        f.write(raw)
    eol = "\r\n" if "\r\n" in local else "\n"
    text = local.replace("\r\n", "\n")
    text += ("" if text.endswith("\n") or not text else "\n") + \
        f"\n<!-- plugdrop: added on {when:%Y-%m-%d} from {safe_part(machine or 'unknown')} -->\n" + \
        "\n\n".join(chosen) + "\n"
    tmp = target.with_name(f"{target.name}.plugdrop-tmp-{os.getpid()}")
    tmp.write_bytes(text.replace("\n", eol).encode("utf-8"))
    os.replace(tmp, target)
    return [b.split("\n", 1)[0][:80] for b in chosen], str(backup)


def _check_relative(text, what):
    parts = text.split("/")
    if any(p in ("", ".", "..") or BAD_PATH_CHARS.search(p) for p in parts):
        raise PlugdropError(f"Unsafe {what} in snapshot: {text!r}")


def target_path(home, item):
    """Where an item lives on this machine. Rejects names that would escape the config dir."""
    kind, name = item.get("kind"), item.get("name", "")
    if kind == "skill":
        _check_relative(name, "skill name")
        if "/" in name:
            raise PlugdropError(f"Unsafe skill name in snapshot: {name!r}")
        return home / "skills" / name
    if kind in ("command", "agent"):
        _check_relative(name, f"{kind} name")
        return home / f"{kind}s" / f"{name}.md"
    if kind == "claude-md":
        if name != CLAUDE_MD:
            raise PlugdropError(f"Unsafe CLAUDE.md name in snapshot: {name!r}")
        return home / CLAUDE_MD
    raise PlugdropError(f"Unknown item kind in snapshot: {kind!r}")


def _file_targets(home, item):
    """[(target file, sha256)] for a files item, with paths and hashes validated."""
    target = target_path(home, item)
    pairs = []
    for f in item.get("files", []):
        if not SHA256.match(f.get("sha256", "")):
            raise PlugdropError(f"Invalid hash in snapshot for {item['id']}")
        _check_relative(f.get("path", ""), "file path")
        pairs.append((target / f["path"] if item["kind"] == "skill" else target, f["sha256"]))
    return pairs


def _sha_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def plan_personal(items, home, folder=None):
    """Status of each snapshot item on this machine: installed | to-install | different | to-merge | not-portable.

    A CLAUDE.md that differs is `to-merge` when the snapshot has blocks missing here (listed in `blocks`, which
    needs `folder` to read the snapshot's copy), else `installed`.
    """
    plan = []
    for item in items:
        extra = {}
        if item.get("source") == "excluded":
            status = NOT_PORTABLE
        else:
            target = target_path(home, item)
            if not target.exists():
                status = TO_INSTALL
            elif item["source"] == "git":
                status = INSTALLED if (target / ".git").exists() else DIFFERENT
            elif all(_sha_of(path) == sha for path, sha in _file_targets(home, item)):
                status = INSTALLED
            elif item["kind"] == "claude-md" and folder is not None:
                snapshot_text = _read_blob(folder, item["files"][0]["sha256"]).decode("utf-8")
                local_text = target.read_text(encoding="utf-8")
                missing = missing_blocks(snapshot_text, local_text)
                snapshot_blocks = split_blocks(snapshot_text)
                status = TO_MERGE if missing else INSTALLED
                extra = {"blocks": [{"n": i, "title": b.split("\n", 1)[0][:80], "text": b}
                                    for i, b in enumerate(missing, 1)],
                         "already_present": len(snapshot_blocks) - len(missing),
                         "local_only": len(missing_blocks(local_text, snapshot_text))}
            else:
                status = DIFFERENT
        plan.append({**item, **extra, "status": status})
    return plan


def blob_path(folder, sha):
    return folder / "files" / sha[:2] / sha


def store_blobs(folder, blobs):
    """Copy each blob into the snapshot repo once. Returns the repo-relative paths of new files."""
    added = []
    for sha, source in sorted(blobs.items()):
        dest = blob_path(folder, sha)
        if dest.exists():
            continue
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise PlugdropError(f"{source} changed during export; run the export again.")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "xb") as f:
            f.write(data)
        added.append(dest.relative_to(folder).as_posix())
    return added


def _read_blob(folder, sha):
    path = blob_path(folder, sha)
    if not path.is_file():
        raise PlugdropError(f"Missing file {sha[:12]} in the snapshot repo.")
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != sha:
        raise PlugdropError(f"File {sha[:12]} in the snapshot repo is corrupted.")
    return data


def _write_files_item(item, home, folder):
    target = target_path(home, item)
    contents = [(path, _read_blob(folder, sha)) for path, sha in _file_targets(home, item)]
    if target.exists():
        raise PlugdropError(f"{target} already exists; plugdrop never overwrites it.")
    if item["kind"] != "skill":
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "xb") as f:
            f.write(contents[0][1])
        return
    # Build the skill next to its final place, then move it in with one rename.
    staging = target.parent / f".plugdrop-staging-{target.name}-{os.getpid()}"
    try:
        staging.mkdir(parents=True)
        for path, data in contents:
            dest = staging / path.relative_to(target)
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "xb") as f:
                f.write(data)
        os.rename(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def install_personal(chosen, home, folder, clone, dry_run=False, machine=None, when=None, blocks=None):
    """Create each chosen item, or merge a `to-merge` CLAUDE.md (`blocks`: 1-based numbers, all if empty).

    clone(url, target) clones a git skill. One failure never stops the rest.
    """
    report = []
    for item in chosen:
        row = {"id": item["id"], "kind": item["kind"], "source": item["source"]}
        merge = item.get("status") == TO_MERGE
        if dry_run:
            report.append({**row, "result": "would-merge" if merge else "would-install"})
            continue
        try:
            if merge:
                added, backup = merge_claude_md(item, home, folder, machine,
                                                when or dt.datetime.now().astimezone(), blocks)
                report.append({**row, "result": "merged" if added else "unchanged", "added_blocks": added,
                               "backup": backup})
                continue
            if item["source"] == "git":
                target = target_path(home, item)
                if target.exists():
                    raise PlugdropError(f"{target} already exists; plugdrop never overwrites it.")
                clone(item["url"], target)
            else:
                _write_files_item(item, home, folder)
            report.append({**row, "result": "installed"})
        except (PlugdropError, OSError) as exc:
            report.append({**row, "result": "failed", "error": str(exc)})
    return report


def git_origin(path):
    """{url, commit, dirty} of a git checkout, or None when it has no 'origin' remote."""
    result = run_tool("git", ["-C", str(path), "remote", "get-url", "origin"])
    if result.returncode != 0:
        return None
    head = run_tool("git", ["-C", str(path), "rev-parse", "HEAD"])
    status = run_tool("git", ["-C", str(path), "status", "--porcelain"])
    return {"url": result.stdout.strip(), "commit": head.stdout.strip() if head.returncode == 0 else None,
            "dirty": bool(status.stdout.strip())}


def git_clone(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    _check(run_tool("git", ["clone", "--quiet", url, str(target)], timeout=1800))


# ---------------------------------------------------------------- subcommands

SNAPSHOT_REPO_README = """# plugdrop snapshots

Private repository written by the plugdrop plugin for Claude Code.

- `snapshots/`: one JSON file per export. Files are never overwritten.
- `files/`: contents of your personal skills, commands and agents, one file per content hash.
- `categories.json`: `{"plugin@marketplace" or "skill:name": "category"}`. Shared by all snapshots; edit it freely.

Plugins are stored as references (name, marketplace source, version), never as code. Files that look like
credentials are never copied.
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
    previous = load_config() if config_path().exists() else None
    local = clone_dir(plugdrop_home(), full_name, previous)
    actions = []
    if (local / ".git").exists():
        origin = run_tool("git", ["-C", str(local), "remote", "get-url", "origin"])
        if origin.returncode != 0 or not origin_matches(origin.stdout, full_name):
            return {"ok": False, "error": f"{local} is a clone of {origin.stdout.strip() or 'an unknown repo'}, "
                                          f"not of {full_name}. plugdrop does not delete or reuse it; "
                                          "move or remove that folder yourself, then run the setup again."}
    if previous and previous.get("repo") != full_name:
        actions.append(f"switch from {previous.get('repo')} to {full_name} "
                       f"(the old clone stays in {previous.get('local_path')})")

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


def _personal_summary(items):
    """Compact view of personal items: no file lists."""
    rows = [{"id": i["id"], "source": i["source"], **({"reason": i["reason"]} if "reason" in i else {}),
             **({"files": len(i["files"])} if "files" in i else {})} for i in items]
    counts = {k: sum(1 for i in items if i["source"] == k) for k in ("files", "git", "excluded")}
    return rows, counts


def cmd_export_preview(_args):
    config = load_config()
    folder = repo_dir(config)
    git(["pull", "--ff-only"], folder)
    entries, warnings = _current_entries()
    personal, _, personal_warnings = scan_personal(claude_home(), git_origin)
    categories = read_categories(folder)
    ids = [f"{e['name']}@{e['marketplace']}" for e in entries if e["portability"] != "non-user-scope"]
    ids += [i["id"] for i in personal if i["source"] != "excluded"]
    counts = {k: sum(1 for e in entries if e["portability"] == k) for k in PORTABILITY_ORDER}
    rows, personal_counts = _personal_summary(personal)
    return {"ok": True, "machine": config["machine"], "author": config["author"], "counts": counts,
            "plugins": entries, "personal": rows, "personal_counts": personal_counts,
            "warnings": warnings + personal_warnings, "categories": categories,
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
    personal, blobs, personal_warnings = scan_personal(claude_home(), git_origin)
    warnings += personal_warnings
    now = dt.datetime.now().astimezone()
    snapshot = {"plugdrop_version": PLUGDROP_VERSION, "created_at": now.isoformat(timespec="seconds"),
                "author": config["author"], "machine": config["machine"], "note": note,
                "claude_code_version": claude_version(), "plugins": entries, "personal": personal}
    new_categories = dict(c.split("=", 1) for c in args.category)
    counts = {k: sum(1 for e in entries if e["portability"] == k) for k in PORTABILITY_ORDER}
    _, personal_counts = _personal_summary(personal)
    if args.dry_run:
        existing = {p.name for p in (folder / "snapshots").glob("*.json")}
        name = snapshot_filename(now, config["machine"], config["author"], existing)
        new_files = sum(1 for sha in blobs if not blob_path(folder, sha).exists())
        return {"ok": True, "dry_run": True, "would_write": f"snapshots/{name}", "counts": counts,
                "personal_counts": personal_counts, "new_files": new_files,
                "categories_to_set": new_categories, "warnings": warnings}

    # Add the folder, not each blob: hundreds of paths would overflow the Windows command line.
    to_add = ["files"] if store_blobs(folder, blobs) else []
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
    result = {"ok": True, "snapshot": f"snapshots/{name}", "counts": counts, "personal_counts": personal_counts,
              "warnings": warnings, "repo": config["repo"]}
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
    snapshot = load_snapshot(folder, resolve_snapshot(folder, snapshot_name))
    plan = [{**item, "kind": "plugin"}
            for item in plan_statuses(snapshot.get("plugins", []), user_scope_ids(installed_plugins()))]
    plan += plan_personal(snapshot.get("personal", []), claude_home(), folder)
    categories = read_categories(folder)
    for item in plan:
        item["category"] = categories.get(item["id"])
    # Flag CLAUDE.md blocks that mention a plugin or skill that is not installed here.
    status_by_name = {i["name"]: i["status"] for i in plan if i["kind"] in ("plugin", "skill")}
    for item in plan:
        for block in item.get("blocks", []):
            notes = block_notes(block["text"], status_by_name)
            block["needs"] = [n for n in notes.pop("mentions") if status_by_name[n] != INSTALLED]
            block.update(notes)
    return snapshot, plan, categories


def cmd_import_plan(args):
    folder = repo_dir(load_config())
    git(["pull", "--ff-only"], folder)
    snapshot, plan, _ = _plan(folder, args.snapshot)
    todo = [i for i in plan if i["status"] in (TO_INSTALL, TO_MERGE)]
    items = [{k: v for k, v in i.items() if k != "files"} for i in plan]
    result = {"ok": True, "snapshot": resolve_snapshot(folder, args.snapshot), "machine": snapshot.get("machine"),
              "note": snapshot.get("note"), "items": items,
              "counts": {s: sum(1 for i in plan if i["status"] == s)
                         for s in (INSTALLED, TO_INSTALL, TO_MERGE, DIFFERENT, NOT_PORTABLE, INFO_ONLY)},
              "categories_available": sorted({i["category"] for i in todo if i["category"]}),
              "kinds_available": sorted({i["kind"] for i in todo})}
    if args.diff:
        personal, _, _ = scan_personal(claude_home(), git_origin)
        here = user_scope_ids(installed_plugins()) | {i["id"] for i in personal}
        result["only_here"] = only_here(snapshot, here)
    return result


def cmd_import(args):
    folder = repo_dir(load_config())
    snapshot, plan, categories = _plan(folder, args.snapshot)
    if args.all:
        chosen = select(plan, "all")
    elif args.category:
        chosen = select(plan, "category", categories, args.category)
    elif args.kind:
        chosen = [i for i in select(plan, "all") if i["kind"] in args.kind]
    elif args.ids:
        wanted = [i.strip() for i in args.ids.split(",") if i.strip()]
        chosen = select(plan, "ids", values=wanted)
    else:
        chosen = []
    chosen_plugins = [i for i in chosen if i["kind"] == "plugin"]
    marketplaces = {m.get("name") for m in known_marketplaces()} if chosen_plugins else set()
    report = install(chosen_plugins, marketplaces, claude, dry_run=args.dry_run)
    blocks = {int(n) for n in (args.blocks or "").split(",") if n.strip()}
    report += install_personal([i for i in chosen if i["kind"] != "plugin"], claude_home(), folder, git_clone,
                               dry_run=args.dry_run, machine=snapshot.get("machine"), blocks=blocks)

    installed_plugin_rows = [r for r in report if r["result"] == "installed" and "snapshot_version" in r]
    if not args.dry_run and installed_plugin_rows:
        versions = {p["id"]: p.get("version") for p in installed_plugins() if p.get("scope") == USER_SCOPE}
        for row in installed_plugin_rows:
            row["installed_version"] = versions.get(row["id"])
            row["version_changed"] = row["installed_version"] != row["snapshot_version"]

    chosen_ids = {i["id"] for i in chosen}
    return {"ok": True, "snapshot": resolve_snapshot(folder, args.snapshot), "dry_run": args.dry_run,
            "results": report,
            "already_installed": [i["id"] for i in plan if i["status"] == INSTALLED],
            "skipped": [i["id"] for i in plan if i["status"] in (TO_INSTALL, TO_MERGE) and i["id"] not in chosen_ids],
            "different": [i["id"] for i in plan if i["status"] == DIFFERENT],
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
    p.add_argument("--snapshot", required=True, help="snapshot file name, or \"latest\"")
    p.add_argument("--diff", action="store_true", help="also list what is here but not in the snapshot")
    p.set_defaults(func=cmd_import_plan)

    p = sub.add_parser("import", help="install the chosen plugins from a snapshot")
    p.add_argument("--snapshot", required=True, help="snapshot file name, or \"latest\"")
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true")
    group.add_argument("--category", action="append", metavar="CATEGORY")
    group.add_argument("--kind", action="append", choices=["plugin", "skill", "command", "agent", "claude-md"])
    group.add_argument("--ids", metavar="ID,ID,...")
    group.add_argument("--none", action="store_true")
    p.add_argument("--blocks", metavar="N,N,...", help="CLAUDE.md merge: only these missing blocks (default all)")
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
