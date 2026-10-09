"""Unit tests for scripts/plugdrop.py. Fake data only: nothing under the real ~/.claude is touched.

Run from the plugin root:  python -m unittest discover -s tests
"""
import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import plugdrop as pd  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def entries():
    result, _ = pd.build_entries(load_fixture("plugin_list.json"), load_fixture("marketplace_list.json"))
    return {f"{e['name']}@{e['marketplace']}": e for e in result}


class ParsingTests(unittest.TestCase):
    def test_split_id_uses_last_at(self):
        self.assertEqual(pd.split_id("alpha@gh-market"), ("alpha", "gh-market"))
        self.assertEqual(pd.split_id("@scope/pkg@npm"), ("@scope/pkg", "npm"))
        self.assertEqual(pd.split_id("bare"), ("bare", ""))

    def test_marketplace_source_strings(self):
        mkts = {m["name"]: m for m in load_fixture("marketplace_list.json")}
        self.assertEqual(pd.marketplace_source(mkts["gh-market"])[:2], ("example-owner/gh-market", "github"))
        self.assertEqual(pd.marketplace_source(mkts["local-market"])[:2], (None, "directory"))
        with_ref = {"name": "x", "source": "github", "repo": "o/r", "ref": "v1"}
        self.assertEqual(pd.marketplace_source(with_ref)[0], "o/r@v1")
        git_ref = {"name": "x", "source": "git", "url": "https://h/o/r.git", "ref": "main"}
        self.assertEqual(pd.marketplace_source(git_ref)[0], "https://h/o/r.git#main")

    def test_snapshot_fields_are_whitelisted(self):
        allowed = {"name", "marketplace", "marketplace_source", "marketplace_source_type",
                   "version", "scope", "enabled", "portability"}
        for e in entries().values():
            self.assertEqual(set(e), allowed)
        dumped = json.dumps(list(entries().values()))
        for leaked in ("FAKE-SECRET-VALUE", "FAKE-TOKEN", "someone", "server.js", "installPath", "D:\\\\work"):
            self.assertNotIn(leaked, dumped)


class ClassificationTests(unittest.TestCase):
    def test_classes(self):
        e = entries()
        self.assertEqual(e["alpha@gh-market"]["portability"], "portable")
        self.assertEqual(e["beta@git-market"]["portability"], "portable")
        self.assertEqual(e["gamma@local-market"]["portability"], "local")
        self.assertEqual(e["orphan@removed-market"]["portability"], "local")
        for pid in ("delta@gh-market", "epsilon@inline", "zeta@synced"):
            self.assertEqual(e[pid]["portability"], "non-user-scope", pid)

    def test_enabled_flag_is_kept(self):
        self.assertFalse(entries()["beta@git-market"]["enabled"])

    def test_credentials_removed_and_reported(self):
        result, warnings = pd.build_entries(load_fixture("plugin_list.json"), load_fixture("marketplace_list.json"))
        beta = next(e for e in result if e["name"] == "beta")
        self.assertEqual(beta["marketplace_source"], "https://git.example.com/team/market.git")
        self.assertEqual(len([w for w in warnings if "git-market" in w]), 1)


class CredentialTests(unittest.TestCase):
    def test_userinfo_removed_from_https(self):
        self.assertEqual(pd.strip_credentials("https://user:pw@host/x.git"), ("https://host/x.git", True))
        self.assertEqual(pd.strip_credentials("https://ghp_abc@github.com/o/r"), ("https://github.com/o/r", True))

    def test_secret_query_params_removed(self):
        clean, removed = pd.strip_credentials("https://host/m.json?token=abc&ref=main&access_token=x&apiKey=k")
        self.assertTrue(removed)
        self.assertEqual(clean, "https://host/m.json?ref=main")

    def test_harmless_urls_untouched(self):
        for url in ("https://github.com/o/r.git", "git@github.com:o/r.git",
                    "ssh://git@github.com/o/r.git", "https://host/r.git#v1"):
            self.assertEqual(pd.strip_credentials(url), (url, False), url)


class SnapshotFileTests(unittest.TestCase):
    WHEN = dt.datetime(2026, 10, 9, 14, 30, 12)

    def test_filename_and_suffixes(self):
        base = "2026-10-09_143012_PC-OFFICE_alex"
        self.assertEqual(pd.snapshot_filename(self.WHEN, "PC-OFFICE", "alex", set()), base + ".json")
        taken = {base + ".json", base + "-2.json"}
        self.assertEqual(pd.snapshot_filename(self.WHEN, "PC-OFFICE", "alex", taken), base + "-3.json")

    def test_unsafe_characters_sanitized(self):
        name = pd.snapshot_filename(self.WHEN, "my pc/../x", "Mario Rossi", set())
        self.assertEqual(name, "2026-10-09_143012_my-pc-..-x_Mario-Rossi.json")
        self.assertNotIn("/", name)

    def test_write_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            first = pd.write_snapshot(folder, {"note": "one"}, self.WHEN, "pc", "me")
            second = pd.write_snapshot(folder, {"note": "two"}, self.WHEN, "pc", "me")
            self.assertNotEqual(first, second)
            self.assertTrue(second.endswith("-2.json"))
            self.assertEqual(json.loads((folder / first).read_text(encoding="utf-8"))["note"], "one")
            self.assertEqual(json.loads((folder / second).read_text(encoding="utf-8"))["note"], "two")


class LatestSnapshotTests(unittest.TestCase):
    def test_latest_resolves_to_newest_and_names_pass_through(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            snaps = folder / "snapshots"
            snaps.mkdir()
            for name, created in (("a.json", "2026-01-01T10:00:00+02:00"), ("b.json", "2026-03-01T10:00:00+02:00")):
                (snaps / name).write_text(json.dumps({"created_at": created, "plugins": []}), encoding="utf-8")
            self.assertEqual(pd.resolve_snapshot(folder, "latest"), "b.json")
            self.assertEqual(pd.resolve_snapshot(folder, "a.json"), "a.json")

    def test_latest_without_snapshots_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(pd.PlugdropError):
                pd.resolve_snapshot(Path(tmp), "latest")


SNAPSHOT_PLUGINS = [
    {"name": "alpha", "marketplace": "gh-market", "marketplace_source": "example-owner/gh-market",
     "portability": "portable", "version": "1.0.0", "enabled": True},
    {"name": "beta", "marketplace": "git-market", "marketplace_source": "https://git.example.com/m.git",
     "portability": "portable", "version": "2.0.0", "enabled": False},
    {"name": "omega", "marketplace": "gh-market", "marketplace_source": "example-owner/gh-market",
     "portability": "portable", "version": "1.0.0", "enabled": True},
    {"name": "gamma", "marketplace": "local-market", "marketplace_source": None,
     "portability": "local", "version": "0.1.0", "enabled": True},
    {"name": "zeta", "marketplace": "synced", "marketplace_source": None,
     "portability": "non-user-scope", "version": "1.2.0", "enabled": True},
]


class DiffAndSelectionTests(unittest.TestCase):
    def plan(self):
        return pd.plan_statuses(SNAPSHOT_PLUGINS, installed_ids={"alpha@gh-market", "zeta@synced"})

    def test_statuses(self):
        status = {i["id"]: i["status"] for i in self.plan()}
        self.assertEqual(status, {
            "alpha@gh-market": "installed",
            "beta@git-market": "to-install",
            "omega@gh-market": "to-install",
            "gamma@local-market": "not-portable",
            "zeta@synced": "info-only",
        })

    def test_select_all_and_none(self):
        self.assertEqual([i["id"] for i in pd.select(self.plan(), "all")],
                         ["beta@git-market", "omega@gh-market"])
        self.assertEqual(pd.select(self.plan(), "none"), [])

    def test_select_by_category(self):
        cats = {"beta@git-market": "tools", "omega@gh-market": "fun", "alpha@gh-market": "tools"}
        chosen = pd.select(self.plan(), "category", categories=cats, values=["tools"])
        self.assertEqual([i["id"] for i in chosen], ["beta@git-market"])

    def test_select_by_ids_ignores_non_installable(self):
        chosen = pd.select(self.plan(), "ids", values=["omega@gh-market", "gamma@local-market"])
        self.assertEqual([i["id"] for i in chosen], ["omega@gh-market"])


class FakeRunner:
    """Records CLI calls; fails any call whose args contain a word in `fail_on`."""

    def __init__(self, fail_on=()):
        self.calls, self.fail_on = [], set(fail_on)

    def __call__(self, args):
        self.calls.append(args)
        failed = any(word in self.fail_on for word in args)
        return subprocess.CompletedProcess(args, 1 if failed else 0, "", "boom" if failed else "")


class InstallTests(unittest.TestCase):
    def chosen(self):
        plan = pd.plan_statuses(SNAPSHOT_PLUGINS, installed_ids=set())
        return pd.select(plan, "all")

    def test_adds_unknown_marketplace_once_then_installs(self):
        run = FakeRunner()
        report = pd.install(self.chosen(), known_marketplaces={"gh-market"}, run=run)
        self.assertEqual(run.calls, [
            ["plugin", "install", "alpha@gh-market", "--scope", "user"],
            ["plugin", "marketplace", "add", "https://git.example.com/m.git", "--scope", "user"],
            ["plugin", "install", "beta@git-market", "--scope", "user"],
            ["plugin", "install", "omega@gh-market", "--scope", "user"],
        ])
        self.assertEqual([r["result"] for r in report], ["installed"] * 3)
        self.assertTrue(next(r for r in report if r["id"] == "beta@git-market")["was_disabled"])

    def test_failure_does_not_stop_others(self):
        run = FakeRunner(fail_on={"alpha@gh-market"})
        report = pd.install(self.chosen(), known_marketplaces={"gh-market", "git-market"}, run=run)
        results = {r["id"]: r["result"] for r in report}
        self.assertEqual(results, {"alpha@gh-market": "failed", "beta@git-market": "installed",
                                   "omega@gh-market": "installed"})
        self.assertIn("boom", next(r for r in report if r["id"] == "alpha@gh-market")["error"])

    def test_dry_run_calls_nothing(self):
        run = FakeRunner()
        report = pd.install(self.chosen(), known_marketplaces=set(), run=run, dry_run=True)
        self.assertEqual(run.calls, [])
        self.assertEqual({r["result"] for r in report}, {"would-install"})
        self.assertEqual(sum(1 for r in report if r["add_marketplace"]), 2)


class RepoChoiceTests(unittest.TestCase):
    def test_origin_matches(self):
        for url in ("https://github.com/me/snaps.git", "https://github.com/Me/Snaps", "git@github.com:me/snaps.git\n"):
            self.assertTrue(pd.origin_matches(url, "me/snaps"), url)
        for url in ("https://github.com/me/other.git", "https://github.com/notme/snaps.git", ""):
            self.assertFalse(pd.origin_matches(url, "me/snaps"), url)

    def test_clone_dir(self):
        home = Path("/h")
        legacy = {"repo": "me/snaps", "local_path": "/h/repo"}
        self.assertEqual(pd.clone_dir(home, "me/snaps", legacy), Path("/h/repo"))
        self.assertEqual(pd.clone_dir(home, "me/other", legacy), home / "repos" / "me" / "other")
        self.assertEqual(pd.clone_dir(home, "me/snaps", None), home / "repos" / "me" / "snaps")


class BackendTests(unittest.TestCase):
    def test_remote_url_rules(self):
        for url in ("https://gitlab.com/me/snaps.git", "git@codeberg.org:me/snaps.git", "ssh://git@host/x.git"):
            self.assertEqual(pd.check_remote_url(url + " "), url)
        for url in ("https://me:token@gitlab.com/me/snaps.git", "C:/repos/snaps", "file:///x", "snaps"):
            with self.assertRaises(pd.PlugdropError, msg=url):
                pd.check_remote_url(url)

    def test_git_clone_dir(self):
        home = Path("/h")
        self.assertEqual(pd.clone_dir(home, "https://gitlab.com/me/snaps.git", None, url=True),
                         home / "repos" / "git" / "gitlab.com_me_snaps.git")
        self.assertEqual(pd.clone_dir(home, "git@codeberg.org:me/s.git", None, url=True),
                         home / "repos" / "git" / "codeberg.org-me_s.git")

    def test_git_only_for_git_stores(self):
        self.assertTrue(pd.uses_git({}))
        self.assertTrue(pd.uses_git({"backend": "git"}))
        self.assertFalse(pd.uses_git({"backend": "folder"}))
        self.assertEqual(pd.TOOLS_BY_BACKEND["folder"], ("claude",))


class OnlyHereTests(unittest.TestCase):
    def test_lists_what_the_snapshot_lacks(self):
        snapshot = {"plugins": [{"name": "alpha", "marketplace": "m"}], "personal": [{"id": "skill:a"}]}
        here = {"alpha@m", "beta@m", "skill:a", "agent:z"}
        self.assertEqual(pd.only_here(snapshot, here), ["agent:z", "beta@m"])
        self.assertEqual(pd.only_here({}, {"x@m"}), ["x@m"])


def make_home(root):
    """A fake Claude config dir with every kind of personal item."""
    home = Path(root) / "claude"
    (home / "skills" / "notes" / "scripts").mkdir(parents=True)
    (home / "skills" / "notes" / "SKILL.md").write_text("# notes\n", encoding="utf-8")
    (home / "skills" / "notes" / "scripts" / "run.py").write_text("print(1)\n", encoding="utf-8")
    (home / "skills" / "notes" / "node_modules").mkdir()
    (home / "skills" / "notes" / "node_modules" / "big.js").write_text("x", encoding="utf-8")
    (home / "skills" / "leaky").mkdir()
    (home / "skills" / "leaky" / "SKILL.md").write_text("key: ghp_" + "a1" * 20 + "\n", encoding="utf-8")
    (home / "skills" / "cloned" / ".git").mkdir(parents=True)
    (home / "skills" / "cloned" / "SKILL.md").write_text("# cloned\n", encoding="utf-8")
    (home / "skills" / "not-a-skill").mkdir()
    (home / "skills" / ".trash" / "old").mkdir(parents=True)
    (home / "commands" / "team").mkdir(parents=True)
    (home / "commands" / "hello.md").write_text("Say hello\n", encoding="utf-8")
    (home / "commands" / "team" / "sync.md").write_text("Sync\n", encoding="utf-8")
    (home / "agents").mkdir()
    (home / "agents" / "reviewer.md").write_text("Review\n", encoding="utf-8")
    return home


def fake_origin(path):
    return {"url": "https://user:pw@github.com/o/cloned.git", "commit": "abc", "dirty": True}


class PersonalScanTests(unittest.TestCase):
    def scan(self, tmp):
        return pd.scan_personal(make_home(tmp), fake_origin)

    def test_finds_items_and_skips_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            items, _, _ = self.scan(tmp)
            by_id = {i["id"]: i for i in items}
            self.assertEqual(set(by_id), {"skill:notes", "skill:leaky", "skill:cloned", "command:hello",
                                          "command:team/sync", "agent:reviewer"})
            self.assertEqual([f["path"] for f in by_id["skill:notes"]["files"]], ["SKILL.md", "scripts/run.py"])

    def test_secret_excludes_whole_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            items, blobs, warnings = self.scan(tmp)
            leaky = next(i for i in items if i["id"] == "skill:leaky")
            self.assertEqual(leaky["source"], "excluded")
            self.assertNotIn("files", leaky)
            self.assertFalse(any("leaky" in str(p) for p in blobs.values()))
            self.assertTrue(any("skill:leaky" in w for w in warnings))

    def test_git_skill_is_a_reference_without_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            items, _, warnings = self.scan(tmp)
            cloned = next(i for i in items if i["id"] == "skill:cloned")
            self.assertEqual((cloned["source"], cloned["url"]), ("git", "https://github.com/o/cloned.git"))
            self.assertTrue(any("credentials" in w for w in warnings))
            self.assertTrue(any("local changes" in w for w in warnings))

    def test_secret_detection(self):
        self.assertTrue(pd.looks_secret(".env", b"A=1"))
        self.assertTrue(pd.looks_secret("x.md", b"-----BEGIN RSA PRIVATE KEY-----"))
        self.assertTrue(pd.looks_secret("x.md", b"token sk-ant-api03-abcdefghij1234567890"))
        self.assertFalse(pd.looks_secret("x.md", b"use the task-management-and-planning-workflow"))
        self.assertFalse(pd.looks_secret("SKILL.md", b"# plain skill"))
        self.assertFalse(pd.looks_secret("x.md", b"aws example AKIAIOSFODNN7EXAMPLE"))


class PersonalRoundTripTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.source = make_home(self.tmp.name)
        self.items, blobs, _ = pd.scan_personal(self.source, fake_origin)
        self.added = pd.store_blobs(self.repo, blobs)
        self.dest = Path(self.tmp.name) / "other-machine"

    def tearDown(self):
        self.tmp.cleanup()

    def statuses(self):
        return {i["id"]: i["status"] for i in pd.plan_personal(self.items, self.dest)}

    def test_blobs_stored_once(self):
        self.assertEqual(len(self.added), 5)
        self.assertEqual(pd.store_blobs(self.repo, pd.scan_personal(self.source, fake_origin)[1]), [])

    def test_install_then_everything_is_installed(self):
        self.assertEqual(set(self.statuses().values()), {"to-install", "not-portable"})
        plan = pd.plan_personal(self.items, self.dest)
        clones = []

        def clone(url, target):
            clones.append(url)
            (target / ".git").mkdir(parents=True)

        report = pd.install_personal(pd.select(plan, "all"), self.dest, self.repo, clone)
        self.assertEqual({r["result"] for r in report}, {"installed"})
        self.assertEqual(clones, ["https://github.com/o/cloned.git"])
        self.assertEqual((self.dest / "skills" / "notes" / "scripts" / "run.py").read_text(encoding="utf-8"),
                         "print(1)\n")
        self.assertTrue((self.dest / "commands" / "team" / "sync.md").is_file())
        self.assertFalse(list((self.dest / "skills").glob(".plugdrop-staging-*")))
        self.assertEqual(set(self.statuses().values()), {"installed", "not-portable"})

    def test_existing_different_item_is_never_overwritten(self):
        (self.dest / "agents").mkdir(parents=True)
        (self.dest / "agents" / "reviewer.md").write_text("mine\n", encoding="utf-8")
        self.assertEqual(self.statuses()["agent:reviewer"], "different")
        item = next(i for i in self.items if i["id"] == "agent:reviewer")
        report = pd.install_personal([item], self.dest, self.repo, clone=None)
        self.assertEqual(report[0]["result"], "failed")
        self.assertEqual((self.dest / "agents" / "reviewer.md").read_text(encoding="utf-8"), "mine\n")

    def test_dry_run_writes_nothing(self):
        plan = pd.plan_personal(self.items, self.dest)
        report = pd.install_personal(pd.select(plan, "all"), self.dest, self.repo, clone=None, dry_run=True)
        self.assertEqual({r["result"] for r in report}, {"would-install"})
        self.assertFalse(self.dest.exists())

    def test_unsafe_names_rejected(self):
        for item in ({"kind": "skill", "name": "../evil"}, {"kind": "skill", "name": "a/b"},
                     {"kind": "command", "name": "..\\x"}, {"kind": "agent", "name": "C:/x"},
                     {"kind": "plugin", "name": "x"}):
            with self.assertRaises(pd.PlugdropError, msg=str(item)):
                pd.target_path(self.dest, item)
        bad_file = {"id": "skill:ok", "kind": "skill", "name": "ok", "source": "files",
                    "files": [{"path": "../../x", "sha256": "0" * 64}]}
        report = pd.install_personal([bad_file], self.dest, self.repo, clone=None)
        self.assertEqual(report[0]["result"], "failed")
        self.assertFalse((Path(self.tmp.name) / "x").exists())

    def test_corrupted_blob_rejected(self):
        item = next(i for i in self.items if i["id"] == "command:hello")
        pd.blob_path(self.repo, item["files"][0]["sha256"]).write_bytes(b"tampered")
        report = pd.install_personal([item], self.dest, self.repo, clone=None)
        self.assertIn("corrupted", report[0]["error"])
        self.assertFalse((self.dest / "commands" / "hello.md").exists())


SNAPSHOT_CLAUDE_MD = """At the start of every session load the skill `example-plugin:example-guidelines`.


# notes-graph
- **notes-graph** (`~/.claude/skills/notes-graph/SKILL.md`) - any input to knowledge graph.
When the user types `/notes-graph`, invoke the Skill tool.

# paths
Notes live in G:/Projects/Notes. See @~/.claude/rules.md
```bash
# not a heading inside a fence
```
"""
LOCAL_CLAUDE_MD = "Always answer in Italian.\r\n\r\nAt the start of every session load the skill " \
                  "`example-plugin:example-guidelines`.\r\n"


class ClaudeMdTests(unittest.TestCase):
    WHEN = dt.datetime(2026, 10, 9, 17, 0)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.source, self.dest, self.repo = root / "a", root / "b", root / "repo"
        self.source.mkdir()
        self.dest.mkdir()
        (self.source / "CLAUDE.md").write_text(SNAPSHOT_CLAUDE_MD, encoding="utf-8")
        self.items, blobs, _ = pd.scan_personal(self.source, fake_origin)
        pd.store_blobs(self.repo, blobs)
        self.item = next(i for i in self.items if i["kind"] == "claude-md")

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self):
        return pd.plan_personal([self.item], self.dest, self.repo)[0]

    def test_split_blocks(self):
        blocks = pd.split_blocks(SNAPSHOT_CLAUDE_MD)
        self.assertEqual([b.split("\n")[0][:13] for b in blocks], ["At the start ", "# notes-graph", "# paths"])
        self.assertIn("# not a heading", blocks[2])

    def test_missing_creates_identical_file(self):
        self.assertEqual(self.plan()["status"], "to-install")
        report = pd.install_personal([self.plan()], self.dest, self.repo, clone=None)
        self.assertEqual(report[0]["result"], "installed")
        self.assertEqual((self.dest / "CLAUDE.md").read_text(encoding="utf-8"), SNAPSHOT_CLAUDE_MD)

    def test_merge_appends_only_missing_blocks_with_backup(self):
        (self.dest / "CLAUDE.md").write_bytes(LOCAL_CLAUDE_MD.encode("utf-8"))
        plan = self.plan()
        self.assertEqual((plan["status"], plan["already_present"], plan["local_only"]), ("to-merge", 1, 1))
        self.assertEqual([b["title"] for b in plan["blocks"]], ["# notes-graph", "# paths"])
        report = pd.install_personal([plan], self.dest, self.repo, clone=None, machine="PC HOME", when=self.WHEN)
        self.assertEqual((report[0]["result"], report[0]["added_blocks"]), ("merged", ["# notes-graph", "# paths"]))
        merged = (self.dest / "CLAUDE.md").read_bytes().decode("utf-8")
        self.assertTrue(merged.startswith(LOCAL_CLAUDE_MD))
        self.assertIn("<!-- plugdrop: added on 2026-10-09 from PC-HOME -->", merged)
        self.assertNotIn("\n", merged.replace("\r\n", ""))  # kept Windows line endings
        backup = self.dest / "CLAUDE.md.plugdrop-backup-2026-10-09"
        self.assertEqual(backup.read_bytes().decode("utf-8"), LOCAL_CLAUDE_MD)
        # A second import finds nothing missing and changes nothing.
        self.assertEqual(self.plan()["status"], "installed")

    def test_merge_only_chosen_blocks_and_backups_never_overwritten(self):
        (self.dest / "CLAUDE.md").write_text("Mine.\n", encoding="utf-8")
        (self.dest / "CLAUDE.md.plugdrop-backup-2026-10-09").write_text("older\n", encoding="utf-8")
        report = pd.install_personal([self.plan()], self.dest, self.repo, clone=None, machine="m",
                                     when=self.WHEN, blocks={2})
        self.assertEqual(report[0]["added_blocks"], ["# notes-graph"])
        self.assertTrue(report[0]["backup"].endswith("plugdrop-backup-2026-10-09-2"))
        self.assertEqual((self.dest / "CLAUDE.md.plugdrop-backup-2026-10-09").read_text(encoding="utf-8"), "older\n")
        self.assertNotIn("# paths", (self.dest / "CLAUDE.md").read_text(encoding="utf-8"))

    def test_block_notes(self):
        blocks = pd.split_blocks(SNAPSHOT_CLAUDE_MD)
        names = {"example-plugin", "notes-graph", "review"}
        self.assertEqual(pd.block_notes(blocks[0], names)["mentions"], ["example-plugin"])
        self.assertEqual(pd.block_notes(blocks[1], names)["mentions"], ["notes-graph"])
        notes = pd.block_notes(blocks[2], names)
        self.assertEqual((notes["machine_paths"], notes["imports"]), (["G:/Projects/Notes."], ["~/.claude/rules.md"]))


if __name__ == "__main__":
    unittest.main()
