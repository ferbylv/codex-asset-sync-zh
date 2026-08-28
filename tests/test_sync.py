import contextlib
import ast
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "sync.py"
spec = importlib.util.spec_from_file_location("sync", SCRIPT)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class SyncSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "codex"
        self.root.mkdir()
        self.backups = self.root / ".codex_backups"
        (self.root / "AGENTS.md").write_text("version one\\n", encoding="utf-8")
        (self.root / "rules").mkdir()
        (self.root / "rules" / "example.md").write_text("rule one\\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *args],
            text=True,
            capture_output=True,
        )

    def test_default_mode_is_read_only(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.backups.exists())
        self.assertIn("plan", result.stdout.lower())

    def test_mutation_requires_explicit_confirmation(self):
        result = self.run_cli("--apply")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.backups.exists())
        self.assertIn("--yes", result.stderr)

    def test_rollback_without_backup_fails_with_nonzero_exit(self):
        result = self.run_cli("--rollback", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("backup", result.stderr.lower())

    def test_backup_and_rollback_restore_assets(self):
        assets = ["AGENTS.md", "rules"]
        backup = sync.create_local_backup(self.root, assets, self.backups, reason="test")
        (self.root / "AGENTS.md").write_text("version two\\n", encoding="utf-8")
        (self.root / "rules" / "example.md").write_text("rule two\\n", encoding="utf-8")

        sync.rollback_latest_backup(self.root, assets, self.backups)

        self.assertEqual((self.root / "AGENTS.md").read_text(encoding="utf-8"), "version one\\n")
        self.assertEqual(
            (self.root / "rules" / "example.md").read_text(encoding="utf-8"),
            "rule one\\n",
        )
        self.assertTrue((backup / "manifest.json").exists())
        manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["assets"], assets)

    def test_asset_path_cannot_escape_root(self):
        with self.assertRaises(sync.SyncError):
            sync.validate_assets(self.root, ["../outside"])

    def test_failed_command_raises_and_preserves_stderr(self):
        with self.assertRaises(sync.SyncError) as context:
            sync.run_command([sys.executable, "-c", "import sys; print('boom', file=sys.stderr); sys.exit(3)"])
        self.assertIn("boom", str(context.exception))

    def test_partial_backup_rollback_preserves_unbacked_assets(self):
        backup = sync.create_local_backup(
            self.root,
            ["AGENTS.md", "rules"],
            self.backups,
            reason="partial",
        )
        skills = self.root / "skills"
        skills.mkdir()
        (skills / "new-skill.md").write_text("keep me\n", encoding="utf-8")
        (self.root / "AGENTS.md").write_text("version two\n", encoding="utf-8")

        restored = sync.rollback_latest_backup(
            self.root,
            ["AGENTS.md", "rules", "skills"],
            self.backups,
        )

        self.assertEqual(restored, backup)
        self.assertEqual((self.root / "AGENTS.md").read_text(encoding="utf-8"), "version one\\n")
        self.assertEqual((skills / "new-skill.md").read_text(encoding="utf-8"), "keep me\n")

    def test_rollback_to_history_when_requested_assets_are_currently_absent(self):
        backup = sync.create_local_backup(
            self.root,
            ["AGENTS.md", "rules"],
            self.backups,
            reason="before_removal",
        )
        (self.root / "AGENTS.md").unlink()
        (self.root / "rules" / "example.md").unlink()
        (self.root / "rules").rmdir()

        restored = sync.rollback_latest_backup(
            self.root,
            ["AGENTS.md", "rules"],
            self.backups,
        )

        self.assertEqual(restored, backup)
        self.assertEqual((self.root / "AGENTS.md").read_text(encoding="utf-8"), "version one\\n")
        self.assertEqual(
            (self.root / "rules" / "example.md").read_text(encoding="utf-8"),
            "rule one\\n",
        )

    def test_rollback_rejects_incomplete_backup_before_mutating(self):
        backup = sync.create_local_backup(
            self.root,
            ["AGENTS.md", "rules"],
            self.backups,
            reason="incomplete",
        )
        (backup / "AGENTS.md").unlink()
        before = (self.root / "AGENTS.md").read_text(encoding="utf-8")

        with self.assertRaises(sync.SyncError):
            sync.rollback_latest_backup(
                self.root,
                ["AGENTS.md", "rules"],
                self.backups,
            )

        self.assertEqual((self.root / "AGENTS.md").read_text(encoding="utf-8"), before)

    def test_symlinked_asset_cannot_escape_root(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link = self.root / "outside-link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink unsupported: {error}")

        with self.assertRaises(sync.SyncError):
            sync.validate_assets(self.root, ["outside-link"])

    def test_copy_assets_only_replaces_selected_paths(self):
        source = Path(self.temp.name) / "source"
        source.mkdir()
        (source / "AGENTS.md").write_text("remote\n", encoding="utf-8")
        (source / "rules").mkdir()
        (source / "rules" / "example.md").write_text("remote rule\n", encoding="utf-8")
        untouched = self.root / "unselected.txt"
        untouched.write_text("local\n", encoding="utf-8")

        copied = sync.copy_assets(source, self.root, ["AGENTS.md", "rules"])

        self.assertEqual(copied, ["AGENTS.md", "rules"])
        self.assertEqual((self.root / "AGENTS.md").read_text(encoding="utf-8"), "remote\n")
        self.assertEqual(untouched.read_text(encoding="utf-8"), "local\n")

    def test_apply_from_tree_backs_up_before_replacing(self):
        source = Path(self.temp.name) / "source"
        source.mkdir()
        (source / "AGENTS.md").write_text("remote\n", encoding="utf-8")

        backup = sync.apply_assets_from_tree(
            source,
            self.root,
            ["AGENTS.md"],
            self.backups,
            reason="apply",
        )

        self.assertEqual((self.root / "AGENTS.md").read_text(encoding="utf-8"), "remote\n")
        self.assertEqual((backup / "AGENTS.md").read_text(encoding="utf-8"), "version one\\n")

    def test_plan_never_invokes_git_or_creates_files(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with mock.patch.object(sync, "run_command", side_effect=AssertionError("git called")):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = sync.main(["--root", str(self.root)])

        self.assertEqual(result, 0, stderr.getvalue())
        self.assertIn("plan", stdout.getvalue().lower())
        self.assertFalse(self.backups.exists())

    def test_mutating_home_directory_is_rejected(self):
        with self.assertRaises(sync.SyncError):
            sync.validate_root(Path.home())

    def test_restore_requires_explicit_remote(self):
        result = self.run_cli("--restore", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("remote", result.stderr.lower())

    def test_push_requires_explicit_confirmation(self):
        result = self.run_cli("--push", "--remote", "https://example.invalid/repo.git")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--yes", result.stderr)

    def test_mutating_action_holds_and_releases_root_lock(self):
        observed = []

        def fail_rollback(*_args):
            observed.append((self.root / ".meta-sync-lock").is_file())
            raise sync.SyncError("planned failure")

        stderr = io.StringIO()
        with mock.patch.object(sync, "rollback_latest_backup", side_effect=fail_rollback):
            with contextlib.redirect_stderr(stderr):
                result = sync.main(["--root", str(self.root), "--rollback", "--yes"])

        self.assertEqual(result, 2)
        self.assertEqual(observed, [True])
        self.assertFalse((self.root / ".meta-sync-lock").exists())

    def test_existing_root_lock_blocks_mutation(self):
        lock = self.root / ".meta-sync-lock"
        lock.write_text("existing\n", encoding="utf-8")
        stderr = io.StringIO()

        with mock.patch.object(sync, "rollback_latest_backup") as rollback:
            with contextlib.redirect_stderr(stderr):
                result = sync.main(["--root", str(self.root), "--rollback", "--yes"])

        self.assertEqual(result, 2)
        rollback.assert_not_called()
        self.assertIn("mutation", stderr.getvalue().lower())
        self.assertTrue(lock.exists())

    def test_run_command_uses_argument_list_without_shell(self):
        with mock.patch("subprocess.run") as run:
            run.return_value = subprocess.CompletedProcess(["git", "--version"], 0, "git version\n", "")
            sync.run_command(["git", "--version"])

        _, kwargs = run.call_args
        self.assertNotIn("shell", kwargs)
        self.assertEqual(run.call_args.args[0], ["git", "--version"])

    def test_nested_directory_symlink_cannot_escape_root(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        nested_link = self.root / "rules" / "nested"
        try:
            nested_link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink unsupported: {error}")

        with self.assertRaises(sync.SyncError):
            sync._ensure_asset_tree_safe(sync.validate_root(self.root), "rules")

    def test_absolute_symlink_inside_selected_tree_is_rejected_as_nonportable(self):
        source = Path(self.temp.name) / "source"
        source.mkdir()
        rules = source / "rules"
        rules.mkdir()
        target = rules / "target.md"
        target.write_text("target\n", encoding="utf-8")
        try:
            (rules / "absolute-link.md").symlink_to(target)
        except OSError as error:
            self.skipTest(f"symlink unsupported: {error}")

        with self.assertRaises(sync.SyncError):
            sync.copy_assets(source, self.root, ["rules"])

    def test_relative_symlink_cannot_cross_selected_asset_boundary(self):
        source = Path(self.temp.name) / "source"
        source.mkdir()
        (source / "AGENTS.md").write_text("remote\n", encoding="utf-8")
        rules = source / "rules"
        rules.mkdir()
        try:
            (rules / "agents-link.md").symlink_to("../AGENTS.md")
        except OSError as error:
            self.skipTest(f"symlink unsupported: {error}")

        with self.assertRaises(sync.SyncError):
            sync.copy_assets(source, self.root, ["rules"])

    def test_backup_directory_must_be_safe_and_separate_from_assets(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        symlink_parent = self.root / "linked"
        try:
            symlink_parent.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink unsupported: {error}")
        internal_parent = self.root / "backup-parent"
        internal_parent.mkdir()
        internal_link = self.root / "internal-link"
        try:
            internal_link.symlink_to(internal_parent, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink unsupported: {error}")

        invalid_locations = [
            self.root,
            self.root / "rules",
            self.root / "rules" / "backups",
            Path(self.temp.name) / "backups",
            symlink_parent / "backups",
            internal_link / "backups",
        ]
        for backup_dir in invalid_locations:
            with self.subTest(backup_dir=backup_dir):
                with self.assertRaises(sync.SyncError):
                    sync.create_local_backup(self.root, ["rules"], backup_dir, reason="test")

    def test_reserved_asset_paths_are_rejected(self):
        for asset in [".git", ".git/config", ".codex_backups", ".codex_backups/old", ".meta-sync-stage-x"]:
            with self.subTest(asset=asset):
                with self.assertRaises(sync.SyncError):
                    sync.validate_assets(self.root, [asset])

    def test_branch_and_remote_validation_reject_option_like_values(self):
        for branch in [
            "--force", "-main", "bad ref", "foo//bar", "foo/.bar", "@",
            "control\x01", "foo~bar", "foo^bar", "foo:bar", "foo?bar",
            "foo*bar", "foo[bar", "foo\\bar", "branch..name", "branch@{name",
            "/foo", "foo/", "foo.", "foo.lock", "foo/bar.lock",
        ]:
            with self.subTest(branch=branch):
                with self.assertRaises(sync.SyncError):
                    sync.validate_branch(branch)
        for branch in ["feature/safe-sync_1", "main"]:
            with self.subTest(branch=branch):
                self.assertEqual(sync.validate_branch(branch), branch)
        with self.assertRaises(sync.SyncError):
            sync.validate_remote("--upload-pack=evil")

    def test_apply_to_empty_root_skips_backup(self):
        empty_root = Path(self.temp.name) / "empty-root"
        empty_root.mkdir()
        source = Path(self.temp.name) / "source"
        source.mkdir()
        (source / "AGENTS.md").write_text("remote\n", encoding="utf-8")
        backup_dir = empty_root / ".codex_backups"

        backup = sync.apply_assets_from_tree(
            source, empty_root, ["AGENTS.md"], backup_dir, reason="apply"
        )

        self.assertIsNone(backup)
        self.assertFalse(backup_dir.exists())
        self.assertEqual((empty_root / "AGENTS.md").read_text(encoding="utf-8"), "remote\n")

    def test_empty_backup_does_not_leave_directory(self):
        empty_root = Path(self.temp.name) / "empty-root"
        empty_root.mkdir()
        backup_dir = empty_root / ".codex_backups"

        with self.assertRaises(sync.SyncError):
            sync.create_local_backup(empty_root, ["AGENTS.md"], backup_dir, reason="empty")

        self.assertFalse(backup_dir.exists())

    def test_sensitive_error_text_is_redacted_without_hiding_plain_stderr(self):
        text = (
            "fatal boom https://alice:password@example.test/repo "
            "ssh://bob:secret@example.test/repo Bearer top-secret "
            "token=token-value&access_token=access-value&api_key=key-value "
            "password=pass-value&secret=secret-value&auth=auth-value"
        )

        redacted = sync._redact_sensitive_text(text)

        self.assertIn("fatal boom", redacted)
        for secret in ["password", "secret", "top-secret", "token-value", "access-value", "key-value", "pass-value", "secret-value", "auth-value"]:
            self.assertNotIn(secret, redacted)

    def test_backup_supports_nested_file_asset(self):
        backup = sync.create_local_backup(
            self.root, ["rules/example.md"], self.backups, reason="nested"
        )

        self.assertEqual(
            (backup / "rules" / "example.md").read_text(encoding="utf-8"), "rule one\\n"
        )

    def test_atomic_replace_turns_swap_and_cleanup_errors_into_sync_errors(self):
        source = Path(self.temp.name) / "source.txt"
        source.write_text("new", encoding="utf-8")
        target = self.root / "AGENTS.md"
        original_replace = os.replace
        calls = 0

        def fail_second_replace(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("swap failed")
            return original_replace(*args)

        with mock.patch.object(sync.os, "replace", side_effect=fail_second_replace):
            with self.assertRaises(sync.SyncError):
                sync._atomic_replace(source, target)

        self.assertEqual(target.read_text(encoding="utf-8"), "version one\\n")

        with mock.patch.object(sync, "_remove_path", side_effect=OSError("cleanup failed")):
            with self.assertRaises(sync.SyncError):
                sync._atomic_replace(source, target)

    def test_push_checks_cached_selected_paths_instead_of_worktree_status(self):
        command_calls = []

        def command(args, cwd=None):
            command_calls.append(args)
            if args[:4] == ["git", "diff", "--cached", "--name-only"]:
                return "AGENTS.md\nrules/example.md\n"
            return ""

        with mock.patch.object(sync, "_clone_remote", return_value=self.root), mock.patch.object(
            sync, "copy_assets"
        ), mock.patch.object(sync, "run_command", side_effect=command):
            result = sync.main(
                [
                    "--root",
                    str(self.root),
                    "--push",
                    "--yes",
                    "--remote",
                    "https://example.invalid/repo.git",
                    "--asset",
                    "AGENTS.md",
                    "--asset",
                    "rules",
                ]
            )

        self.assertEqual(result, 0)
        self.assertIn(["git", "diff", "--cached", "--name-only", "--"], command_calls)
        self.assertNotIn(["git", "status", "--porcelain"], command_calls)

    def test_backup_rejects_real_path_overlap_with_internal_asset_symlink(self):
        real_asset = self.root / "real-asset"
        real_asset.mkdir()
        link = self.root / "linked-asset"
        try:
            link.symlink_to(real_asset, target_is_directory=True)
        except OSError as error:
            self.skipTest(f"symlink unsupported: {error}")

        with self.assertRaises(sync.SyncError):
            sync.create_local_backup(self.root, ["linked-asset"], real_asset / "backups", "test")

    def test_push_rejects_staged_path_outside_selected_assets(self):
        command_calls = []

        def command(args, cwd=None):
            command_calls.append(args)
            if args == ["git", "diff", "--cached", "--name-only", "--"]:
                return "unselected.txt\n"
            return ""

        with mock.patch.object(sync, "_clone_remote", return_value=self.root), mock.patch.object(
            sync, "copy_assets"
        ), mock.patch.object(sync, "run_command", side_effect=command):
            result = sync.main(
                ["--root", str(self.root), "--push", "--yes", "--remote", "https://example.invalid/repo.git"]
            )

        self.assertNotEqual(result, 0)
        self.assertFalse(any(call[:2] in (["git", "commit"], ["git", "push"]) for call in command_calls))

    def test_push_skips_commit_and_push_when_cached_diff_is_empty(self):
        command_calls = []

        def command(args, cwd=None):
            command_calls.append(args)
            return ""

        with mock.patch.object(sync, "_clone_remote", return_value=self.root), mock.patch.object(
            sync, "copy_assets"
        ), mock.patch.object(sync, "run_command", side_effect=command):
            result = sync.main(
                ["--root", str(self.root), "--push", "--yes", "--remote", "https://example.invalid/repo.git"]
            )

        self.assertEqual(result, 0)
        self.assertFalse(any(call[:2] in (["git", "commit"], ["git", "push"]) for call in command_calls))

    def test_source_parses_with_python_39_grammar_mode(self):
        ast.parse(SCRIPT.read_text(encoding="utf-8"), filename=str(SCRIPT), feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
