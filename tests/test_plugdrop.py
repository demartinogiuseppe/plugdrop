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


if __name__ == "__main__":
    unittest.main()
