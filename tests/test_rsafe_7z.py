#!/usr/bin/env python3
"""Selection boundaries, conflict handling, and real 7z round trips."""

from __future__ import annotations

import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import file_picker

spec = importlib.util.spec_from_file_location("rsafe7z", PROJECT / "rsafe-7z.py")
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)
SEVEN_ZIP = shutil.which("7zz") or shutil.which("7z")
FD = shutil.which("fd") or shutil.which("fdfind")
FZF = shutil.which("fzf")


class WrapperFixture(unittest.TestCase):
  def setUp(self):
    temporary = tempfile.TemporaryDirectory()
    self.addCleanup(temporary.cleanup)
    self.root = Path(temporary.name)
    self.source = self.root / "source"
    self.source.mkdir()
    self.file = self.source / "chosen.txt"
    self.file.write_text("chosen content")
    self.output = self.root / "backup.7z"
    self.logs = io.StringIO()
    for context in (redirect_stdout(self.logs), redirect_stderr(self.logs),
                    mock.patch.object(archive, "Console", None)):
      context.__enter__()
      self.addCleanup(context.__exit__, None, None, None)

  def call(self, *args):
    return archive.main(list(map(str, args)))


class WrapperTests(WrapperFixture):
  def test_aliases_native_password_header_syntax_and_options_after_paths(self):
    args = archive.parse_args([
      "a", "input", "output.7z", "-psecret", "-mhe=on", "--dry-run",
    ])
    self.assertEqual((args.action, args.password, args.headers),
                     ("archive", "secret", "on"))
    args = archive.parse_args([
      "--extract", "--password", "secret", "input.7z", "destination",
    ])
    self.assertEqual(args.action, "extract")
    self.assertEqual(args.headers, "off")

  def test_invalid_combinations_fail_before_process_or_picker(self):
    for args in (
      ["a", "input", "output.7z", "--flatten"],
      ["e", "input.7z", "output", "--encrypt-headers"],
      ["a", "input", "output.7z", "--extension", "txt"],
      ["a", "-i", "input", "output.7z", "--search-dir", "."],
      ["a", "input", "output.7z", "--password", ""],
      ["a", "input", "output.7z", "--overwrite", "--auto-rename"],
      ["a", "input", "output.7z", "--prune-empty-dirs"],
      ["e", "input.7z", "output", "--delete-source-files"],
      ["e", "input.7z", "output", "--delete-source-files",
       "--prune-empty-dirs"],
    ):
      with (self.subTest(args=args), mock.patch.object(archive, "select_inputs")
            as picker, mock.patch.object(archive, "run_7z") as run,
            self.assertRaises(SystemExit)):
        archive.main(args)
      picker.assert_not_called()
      run.assert_not_called()

  def test_dry_run_does_not_prompt_run_or_create_output_parent(self):
    target = self.root / "absent" / "backup.7z"
    with (mock.patch.object(archive.shutil, "which", return_value="7z"),
          mock.patch.object(archive.getpass, "getpass") as prompt,
          mock.patch.object(archive, "run_7z") as run):
      self.assertEqual(self.call("a", self.file, target, "--dry-run",
                                 "--encrypt-headers", "--delete-source-files",
                                 "--prune-empty-dirs"), 0)
    prompt.assert_not_called()
    run.assert_not_called()
    self.assertFalse(target.parent.exists())
    self.assertTrue(self.file.exists())
    self.assertIn("delete after archive verification and publication",
                  self.logs.getvalue())
    self.assertIn("prune empty directories", self.logs.getvalue())

  def test_both_picker_cancellation_steps_leave_output_absent(self):
    for type_picker in (False, True):
      args = ["a", "-i", "--search-dir", str(self.source), str(self.output)]
      args.extend(["--delete-source-files", "--prune-empty-dirs"])
      if type_picker:
        args.append("--pick-types")
      with (self.subTest(type_picker=type_picker),
            mock.patch.object(archive.shutil, "which", return_value="tool"),
            mock.patch.object(archive, "pick_type_candidates",
                              side_effect=SystemExit(130)) as types,
            mock.patch.object(file_picker, "select_files",
                              side_effect=SystemExit(130)) as files,
            mock.patch.object(archive, "run_7z") as run,
            self.assertRaises(SystemExit) as error):
        archive.main(args)
      self.assertEqual(error.exception.code, 130)
      run.assert_not_called()
      self.assertFalse(self.output.exists())
      self.assertTrue(self.file.exists())
      if type_picker:
        types.assert_called_once()
        files.assert_not_called()
      else:
        types.assert_not_called()
        files.assert_called_once()

  def test_type_selection_narrows_files_and_isolates_fzf_global_options(self):
    image = self.source / "photo.JPG"
    image.write_bytes(b"image")
    no_extension = self.source / "LICENSE"
    no_extension.write_text("license")
    candidates = [self.file, image, no_extension]
    args = archive.parse_args(["a", "--pick-types", str(self.output)])

    def select_jpg(command, **kwargs):
      records = kwargs["input"].split(b"\0")
      record = next(row for row in records if b'"jpg"' in row)
      return subprocess.CompletedProcess(command, 0, stdout=record + b"\0")

    with (mock.patch.object(archive.shutil, "which", return_value="tool"),
          mock.patch.object(sys.stdin, "isatty", return_value=True),
          mock.patch.object(file_picker, "discover_files",
                            return_value=candidates),
          mock.patch.object(archive.subprocess, "run", side_effect=select_jpg)
          as run, mock.patch.dict(os.environ, {
            "FZF_DEFAULT_OPTS": "--filter=txt --select-1",
            "FZF_DEFAULT_OPTS_FILE": "/tmp/untrusted-options",
          })):
      selected = archive.pick_type_candidates(args, [self.source], file_picker)
    self.assertEqual(selected, [image])
    self.assertNotIn("FZF_DEFAULT_OPTS", run.call_args.kwargs["env"])
    self.assertNotIn("FZF_DEFAULT_OPTS_FILE", run.call_args.kwargs["env"])

  def test_type_picker_rejects_failed_or_unknown_selections(self):
    args = archive.parse_args(["a", "--pick-types", str(self.output)])
    for code, output, expected in ((130, b"", 130), (1, b"", 1),
                                   (2, b"", 2), (0, b"999\tunknown\0", 2),
                                   (0, b"0\tlabel", 2)):
      with (self.subTest(code=code, output=output),
            mock.patch.object(archive.shutil, "which", return_value="tool"),
            mock.patch.object(sys.stdin, "isatty", return_value=True),
            mock.patch.object(file_picker, "discover_files",
                              return_value=[self.file]),
            mock.patch.object(archive.subprocess, "run", return_value=(
              subprocess.CompletedProcess([], code, stdout=output))),
            self.assertRaises(SystemExit) as error):
        archive.pick_type_candidates(args, [self.source], file_picker)
      self.assertEqual(error.exception.code, expected)

  def test_failed_compression_preserves_existing_archive_and_cleans_stage(self):
    self.output.write_bytes(b"previous archive")
    for exit_code in (1, 2):
      with (self.subTest(exit_code=exit_code),
            mock.patch.object(archive.shutil, "which", return_value="7z"),
            mock.patch.object(archive, "run_7z", return_value=exit_code)):
        self.assertEqual(self.call("a", self.file, self.output, "--overwrite",
                                   "--delete-source-files",
                                   "--prune-empty-dirs"), exit_code)
    self.assertEqual(self.output.read_bytes(), b"previous archive")
    self.assertTrue(self.file.exists())
    self.assertEqual(list(self.root.glob(".rsafe-7z-*")), [])

  def test_failed_verification_preserves_sources_and_old_archive(self):
    self.output.write_bytes(b"previous archive")
    for exit_code in (1, 2):
      with (self.subTest(exit_code=exit_code),
            mock.patch.object(archive.shutil, "which", return_value="7z"),
            mock.patch.object(archive, "run_7z", side_effect=[0, exit_code])
            as run):
        self.assertEqual(self.call("a", self.source, self.output, "--overwrite",
                                   "--delete-source-files",
                                   "--prune-empty-dirs"), exit_code)
      self.assertEqual(run.call_args_list[1].args[0][1], "t")
      self.assertEqual(self.output.read_bytes(), b"previous archive")
      self.assertTrue(self.file.exists())
      self.assertEqual(list(self.root.glob(".rsafe-7z-*")), [])

  def test_publication_failure_and_interruption_preserve_sources(self):
    for interrupted in (False, True):
      def run(command, *, interrupted=interrupted, **kwargs):
        if interrupted:
          raise KeyboardInterrupt
        if command[1] == "a":
          Path(command[command.index("--") + 1]).write_bytes(b"staged")
        return 0

      with (self.subTest(interrupted=interrupted),
            mock.patch.object(archive.shutil, "which", return_value="7z"),
            mock.patch.object(archive, "run_7z", side_effect=run),
            mock.patch.object(archive.os, "link", side_effect=OSError("failed")),
            self.assertRaises(KeyboardInterrupt if interrupted else OSError)):
        self.call("a", self.source, self.output, "--delete-source-files",
                  "--prune-empty-dirs")
      self.assertTrue(self.file.exists())
      self.assertFalse(self.output.exists())
      self.assertEqual(list(self.root.glob(".rsafe-7z-*")), [])

  def test_existing_archive_abort_and_skip_do_not_run_compressor(self):
    self.output.write_bytes(b"previous archive")
    with (mock.patch.object(archive.shutil, "which", return_value="7z"),
          mock.patch.object(archive, "run_7z") as run):
      with self.assertRaises(SystemExit):
        self.call("a", self.file, self.output, "--delete-source-files",
                  "--prune-empty-dirs")
      self.assertEqual(self.call("a", self.file, self.output,
                                 "--skip-conflicts", "--delete-source-files",
                                 "--prune-empty-dirs"), 0)
    run.assert_not_called()
    self.assertEqual(self.output.read_bytes(), b"previous archive")
    self.assertTrue(self.file.exists())

  def test_password_confirmation_failure_does_not_create_archive(self):
    with (mock.patch.object(archive.shutil, "which", return_value="7z"),
          mock.patch.object(sys.stdin, "isatty", return_value=True),
          mock.patch.object(archive.getpass, "getpass",
                            side_effect=["one", "two"]),
          mock.patch.object(archive, "run_7z") as run,
          self.assertRaises(SystemExit)):
      self.call("a", self.file, self.output, "--encrypt-headers")
    run.assert_not_called()
    self.assertFalse(self.output.exists())

  @unittest.skipUnless(FD and FZF, "fd/fzf are not installed")
  def test_real_fzf_type_and_file_filters_preserve_search_relative_layout(self):
    nested = self.source / "nested"
    nested.mkdir()
    image = nested / "photo.JPG"
    image.write_bytes(b"image")
    run_original = subprocess.run

    def filter_run(command, **kwargs):
      if command[0] == FZF:
        term = "jpg" if "--prompt=File types> " in command else ".JPG"
        command = [*command, f"--filter={term}"]
      return run_original(command, **kwargs)

    with (mock.patch.object(sys.stdin, "isatty", return_value=True),
          mock.patch.object(archive.subprocess, "run", side_effect=filter_run)):
      self.assertEqual(self.call("a", "--pick-types", "--no-preview",
                                 "--search-dir", self.source,
                                 "--dry-run", self.output), 0)
    self.assertIn(str(image), self.logs.getvalue())
    self.assertNotIn(str(self.file), self.logs.getvalue())
    self.assertIn(f"Archive path base: {str(self.source)!r}",
                  self.logs.getvalue())
    self.assertFalse(self.output.exists())


@unittest.skipUnless(SEVEN_ZIP, "7z/7zz is not installed")
class IntegrationTests(WrapperFixture):
  def listing(self, path, *switches):
    return subprocess.run([SEVEN_ZIP, "l", "-slt", "-ba", *switches,
                           "--", str(path)], stdin=subprocess.DEVNULL,
                          capture_output=True, timeout=10, check=False)

  def test_delete_after_encrypted_verification_and_publication_then_prune(self):
    nested = self.source / "nested"
    nested.mkdir()
    data = nested / "picture.jpg"
    data.write_bytes(b"picture")
    original = archive.run_7z
    original_delete = archive.delete_source_files
    actions = []

    def run(command, **kwargs):
      actions.append(command[1])
      self.assertTrue(self.file.exists())
      self.assertTrue(data.exists())
      self.assertFalse(self.output.exists())
      return original(command, **kwargs)

    def delete(*args, **kwargs):
      self.assertTrue(self.output.exists())
      listing = self.listing(self.output, "-pcleanup-secret")
      self.assertEqual(listing.returncode, 0)
      return original_delete(*args, **kwargs)

    with (mock.patch.object(archive, "run_7z", side_effect=run),
          mock.patch.object(archive, "delete_source_files", side_effect=delete)):
      self.assertEqual(self.call("a", self.source, self.output,
                                 "--encrypt-headers", "-pcleanup-secret",
                                 "--delete-source-files", "--prune-empty-dirs",
                                 "--verbose"), 0)
    self.assertEqual(actions, ["a", "t"])
    self.assertTrue(self.output.exists())
    self.assertFalse(self.source.exists())
    self.assertTrue(self.root.is_dir())
    self.assertNotIn("cleanup-secret", self.logs.getvalue())
    destination = self.root / "restored"
    self.assertEqual(self.call("e", self.output, destination,
                               "-pcleanup-secret"), 0)
    self.assertEqual((destination / "source" / "chosen.txt").read_text(),
                     "chosen content")
    self.assertEqual((destination / "source/nested/picture.jpg").read_bytes(),
                     b"picture")

  def test_delete_without_prune_keeps_directories_and_literal_filenames(self):
    names = ["@list.txt", "-switch.txt", "a *?[x].txt", "quote ' $;\n.txt"]
    for name in names:
      (self.source / name).write_text(name)
    self.assertEqual(self.call("a", self.source, self.file, self.output,
                               "--delete-source-files"), 0)
    self.assertTrue(self.source.is_dir())
    self.assertEqual(list(self.source.iterdir()), [])
    destination = self.root / "restored"
    self.assertEqual(self.call("e", self.output, destination), 0)
    for name in names:
      self.assertEqual((destination / "source" / name).read_text(), name)

  def test_cleanup_keeps_changed_replaced_and_new_files(self):
    replacement = self.source / "replaced.txt"
    replacement.write_text("original")
    added = self.source / "new.txt"
    original = archive.run_7z

    def run(command, **kwargs):
      code = original(command, **kwargs)
      if command[1] == "a":
        self.file.write_text("new content after compression")
        new_file = self.root / "replacement"
        new_file.write_text("replacement")
        new_file.replace(replacement)
        added.write_text("new file")
      return code

    with mock.patch.object(archive, "run_7z", side_effect=run):
      self.assertEqual(self.call("a", self.source, self.output,
                                 "--delete-source-files",
                                 "--prune-empty-dirs"), 1)
    self.assertEqual(self.file.read_text(), "new content after compression")
    self.assertEqual(replacement.read_text(), "replacement")
    self.assertEqual(added.read_text(), "new file")
    destination = self.root / "restored"
    self.assertEqual(self.call("e", self.output, destination), 0)
    self.assertEqual((destination / "source" / "chosen.txt").read_text(),
                     "chosen content")
    self.assertEqual((destination / "source" / "replaced.txt").read_text(),
                     "original")

  def test_cleanup_keeps_file_when_parent_redirects_to_same_inode(self):
    original = archive.run_7z
    moved = self.root / "moved"

    def run(command, **kwargs):
      code = original(command, **kwargs)
      if command[1] == "a":
        self.source.rename(moved)
        self.source.symlink_to(moved, target_is_directory=True)
      return code

    with mock.patch.object(archive, "run_7z", side_effect=run):
      self.assertEqual(self.call("a", self.source, self.output,
                                 "--delete-source-files",
                                 "--prune-empty-dirs"), 1)
    self.assertTrue(self.source.is_symlink())
    self.assertEqual((moved / "chosen.txt").read_text(), "chosen content")

  def test_cleanup_reports_unlink_failure_and_retains_archive(self):
    unlink = Path.unlink

    def deny_source(path, *args, **kwargs):
      if path == self.file:
        raise PermissionError("source deletion denied")
      return unlink(path, *args, **kwargs)

    with mock.patch.object(Path, "unlink", autospec=True,
                           side_effect=deny_source):
      self.assertEqual(self.call("a", self.source, self.output,
                                 "--delete-source-files",
                                 "--prune-empty-dirs"), 1)
    self.assertEqual(self.file.read_text(), "chosen content")
    self.assertTrue(self.output.exists())
    self.assertEqual(self.listing(self.output).returncode, 0)
    self.assertIn("source deletion denied", self.logs.getvalue())

  def test_cleanup_excludes_output_inside_source(self):
    output = self.source / "backup.7z"
    output.write_bytes(b"previous archive")
    self.assertEqual(self.call("a", self.source, output, "--overwrite",
                               "--delete-source-files",
                               "--prune-empty-dirs"), 0)
    self.assertEqual(list(self.source.iterdir()), [output])
    listing = self.listing(output)
    self.assertEqual(listing.returncode, 0)
    self.assertIn(b"source/chosen.txt", listing.stdout)
    self.assertNotIn(b"backup.7z", listing.stdout)

  def test_cleanup_unlinks_symlinks_and_hard_links_and_keeps_targets(self):
    outside = self.root / "outside"
    outside.mkdir()
    kept = outside / "keep.txt"
    kept.write_text("outside content")
    (self.source / "linked-file").symlink_to(kept)
    (self.source / "linked-dir").symlink_to(outside, target_is_directory=True)
    (self.source / "dangling-link").symlink_to("missing")
    (self.source / "hard-link.txt").hardlink_to(self.file)
    self.assertEqual(self.call("a", self.source, self.output,
                               "--delete-source-files",
                               "--prune-empty-dirs"), 0)
    self.assertFalse(self.source.exists())
    self.assertEqual(kept.read_text(), "outside content")
    destination = self.root / "restored"
    self.assertEqual(self.call("e", self.output, destination), 0)
    restored = destination / "source"
    self.assertEqual((restored / "chosen.txt").read_text(), "chosen content")
    self.assertEqual((restored / "hard-link.txt").read_text(), "chosen content")
    self.assertTrue((restored / "linked-file").is_symlink())
    self.assertTrue((restored / "linked-dir").is_symlink())
    self.assertTrue((restored / "dangling-link").is_symlink())

  def test_cleanup_file_input_does_not_prune_its_parent(self):
    self.assertEqual(self.call("a", self.file, self.output,
                               "--delete-source-files",
                               "--prune-empty-dirs"), 0)
    self.assertFalse(self.file.exists())
    self.assertTrue(self.source.is_dir())

  @unittest.skipUnless(FD and FZF, "fd/fzf are not installed")
  def test_type_picker_cleanup_prunes_empty_directories_inside_source_roots(self):
    empty_after = self.source / "pictures" / "nested"
    empty_after.mkdir(parents=True)
    (empty_after / "photo.JPG").write_bytes(b"image")
    unrelated = self.root / "unrelated-empty"
    unrelated.mkdir()
    run_original = subprocess.run

    def filter_run(command, **kwargs):
      if command[0] == FZF:
        term = "jpg" if "--prompt=File types> " in command else ".JPG"
        command = [*command, f"--filter={term}"]
      return run_original(command, **kwargs)

    for explicit_source in (False, True):
      if explicit_source:
        empty_after.mkdir(parents=True)
        (empty_after / "photo.JPG").write_bytes(b"image")
      inputs = ([str(self.source)] if explicit_source
                else ["--search-dir", str(self.source)])
      with (self.subTest(explicit_source=explicit_source),
            mock.patch.object(sys.stdin, "isatty", return_value=True),
            mock.patch.object(archive.subprocess, "run", side_effect=filter_run)):
        self.assertEqual(self.call("a", "--pick-types", "--no-preview", *inputs,
                                   self.output, "--overwrite",
                                   "--delete-source-files",
                                   "--prune-empty-dirs"), 0)
      self.assertTrue(self.file.exists())
      self.assertFalse(empty_after.parent.exists())
      self.assertTrue(unrelated.is_dir())
      listing = self.listing(self.output)
      self.assertIn(b"pictures/nested/photo.JPG", listing.stdout)
      self.assertNotIn(b"chosen.txt", listing.stdout)

  def test_encrypted_headers_and_password_round_trip_with_redacted_logs(self):
    # Exercise literal wildcard/@listfile names and newlines through real 7z.
    names = ["@list.txt", "-switch.txt", "a *?[x].txt", "quote ' $;\n.txt"]
    for name in names:
      (self.source / name).write_text(name)
    self.assertEqual(self.call("a", self.source, self.output, "-psecret-value",
                               "-mhe=on", "--verbose"), 0)
    self.assertNotIn("secret-value", self.logs.getvalue())
    self.assertIn("-p<redacted>", self.logs.getvalue())
    hidden = self.listing(self.output)
    self.assertNotEqual(hidden.returncode, 0)
    self.assertNotIn(b"chosen.txt", hidden.stdout)
    destination = self.root / "restored"
    self.assertEqual(self.call("e", self.output, destination,
                               "--password", "secret-value"), 0)
    for name in ["chosen.txt", *names]:
      self.assertEqual((destination / "source" / name).read_bytes(),
                       (self.source / name).read_bytes())

  def test_data_encryption_without_header_encryption_and_wrong_password(self):
    self.assertEqual(self.call("a", self.file, self.output,
                               "--password", "data-password"), 0)
    listing = self.listing(self.output)
    self.assertEqual(listing.returncode, 0)
    self.assertIn(b"Path = chosen.txt", listing.stdout)
    self.assertIn(b"Encrypted = +", listing.stdout)
    destination = self.root / "restored"
    self.assertEqual(self.call("e", self.output, destination,
                               "--password", "wrong-password"), 2)
    self.assertIn("7z exited with code 2", self.logs.getvalue())
    self.assertNotIn("wrong-password", self.logs.getvalue())
    self.assertEqual(self.call("e", self.output, destination,
                               "--password", "data-password"), 0)
    self.assertEqual((destination / "chosen.txt").read_bytes(),
                     self.file.read_bytes())

  def test_creation_inside_source_excludes_previous_output_and_staging(self):
    output = self.source / "backup.7z"
    output.write_bytes(b"previous archive")
    self.assertEqual(self.call("a", self.source, output, "--overwrite"), 0)
    listing = self.listing(output)
    self.assertEqual(listing.returncode, 0)
    self.assertIn(b"source/chosen.txt", listing.stdout)
    self.assertNotIn(b"backup.7z", listing.stdout)
    self.assertNotIn(b".rsafe-7z-", listing.stdout)

  def test_extract_skip_rename_overwrite_and_flatten(self):
    self.assertEqual(self.call("a", self.source, self.output), 0)
    for mode in ([], ["--auto-rename"], ["--overwrite"]):
      with self.subTest(mode=mode):
        destination = self.root / ("extract" + "".join(mode))
        destination.mkdir()
        conflict = destination / "chosen.txt"
        conflict.write_text("existing content")
        self.assertEqual(self.call("e", self.output, destination, "--flatten",
                                   *mode), 0)
        if mode == ["--overwrite"]:
          self.assertEqual(conflict.read_bytes(), self.file.read_bytes())
        else:
          self.assertEqual(conflict.read_text(), "existing content")
        if mode == ["--auto-rename"]:
          renamed = [path for path in destination.iterdir()
                     if path.is_file() and path != conflict]
          self.assertEqual(len(renamed), 1)
          self.assertEqual(renamed[0].read_bytes(), self.file.read_bytes())

  def test_archive_rename_and_overwrite_replaces_old_members(self):
    self.assertEqual(self.call("a", self.file, self.output), 0)
    other = self.source / "other.txt"
    other.write_text("other content")
    self.assertEqual(self.call("a", other, self.output,
                               "--auto-rename"), 0)
    renamed = self.root / "backup_[1].7z"
    self.assertTrue(renamed.is_file())
    self.assertIn(b"Path = other.txt", self.listing(renamed).stdout)
    self.assertEqual(self.call("a", other, self.output,
                               "--overwrite"), 0)
    listing = self.listing(self.output)
    self.assertIn(b"Path = other.txt", listing.stdout)
    self.assertNotIn(b"chosen.txt", listing.stdout)

  def test_selected_subset_and_multiple_archive_destinations(self):
    (self.source / "unselected.txt").write_text("leave out")
    with (mock.patch.object(sys.stdin, "isatty", return_value=True),
          mock.patch.object(file_picker, "select_files",
                            return_value=[self.file])):
      self.assertEqual(self.call("a", "-i", "--search-dir", self.source,
                                 self.output), 0)
    listing = self.listing(self.output)
    self.assertIn(b"Path = chosen.txt", listing.stdout)
    self.assertNotIn(b"unselected.txt", listing.stdout)
    second = self.root / "second.7z"
    shutil.copyfile(self.output, second)
    destination = self.root / "batch"
    self.assertEqual(self.call("e", self.output, second, destination), 0)
    for name in ("backup", "second"):
      self.assertEqual((destination / name / "chosen.txt").read_bytes(),
                       self.file.read_bytes())


if __name__ == "__main__":
  unittest.main()
