"""Regression checks for selection boundaries and transfer planning."""

from __future__ import annotations

import argparse
import base64
import importlib.util
import io
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
import file_picker


def load_script(name: str, filename: str):
  spec = importlib.util.spec_from_file_location(name, PROJECT / filename)
  module = importlib.util.module_from_spec(spec)
  sys.modules[name] = module
  spec.loader.exec_module(module)
  return module


rsafe = load_script("rsafe", "rsafe-rsync.py")
rdd = load_script("rdd", "rdd-ionice.py")
FD = shutil.which("fd") or shutil.which("fdfind")
FZF = shutil.which("fzf")


def picker_args(**changes) -> argparse.Namespace:
  values = {
    "extension": [], "hidden": False, "no_ignore": False, "no_preview": False,
  }
  values.update(changes)
  return argparse.Namespace(**values)


def selection_output(paths: list[Path]) -> bytes:
  """Simulate records returned by fzf, whose labels need not remain intact."""
  return b"".join(
    base64.b64encode(os.fsencode(path)) + b"\tlabel\0" for path in paths
  )


# -- Picker output and failure boundaries -------------------------------------

class PickerTests(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)
    self.files = [
      self.root / "a 'quote' $dollar; name\n.txt",
      self.root / os.fsdecode(b"-nonutf8-\xff.TXT"),
    ]
    for path in self.files:
      path.write_bytes(b"sample\n")
    self.stack = ExitStack()
    self.addCleanup(self.stack.close)
    self.terminal = self.stack.enter_context(
      mock.patch.object(sys.stdin, "isatty", return_value=True)
    )
    self.errors = io.StringIO()
    self.stack.enter_context(redirect_stderr(self.errors))

  def select(self, output: bytes, code=0, *, multiple=True, bat=True):
    installed = {"fd": "/bin/fd", "fzf": "/bin/fzf"}
    if bat:
      installed["bat"] = "/bin/bat"
    with (
      mock.patch.object(file_picker.shutil, "which", installed.get),
      mock.patch.object(file_picker, "discover_files", return_value=self.files),
      mock.patch.object(
        file_picker.subprocess, "run",
        return_value=subprocess.CompletedProcess([], code, stdout=output),
      ) as run,
    ):
      selected = file_picker.select_files(
        picker_args(), sources=[self.root], multiple=multiple,
      )
    return selected, run

  def test_special_filenames_round_trip_and_global_options_are_isolated(self):
    output = selection_output(self.files)
    with mock.patch.dict(os.environ, {
      "FZF_DEFAULT_OPTS": "--filter=txt --select-1 --print-query",
      "FZF_DEFAULT_OPTS_FILE": "/tmp/untrusted-fzf-options",
    }):
      selected, run = self.select(output)
    self.assertEqual(selected, self.files)
    records = run.call_args.kwargs["input"].split(b"\0")[:-1]
    input_paths = [base64.b64decode(record.partition(b"\t")[0])
                   for record in records]
    self.assertEqual(input_paths, [os.fsencode(path) for path in self.files])
    env = run.call_args.kwargs["env"]
    self.assertNotIn("FZF_DEFAULT_OPTS", env)
    self.assertNotIn("FZF_DEFAULT_OPTS_FILE", env)

  def test_missing_dependencies_exit_before_discovery(self):
    with (
      mock.patch.object(file_picker.shutil, "which", return_value=None),
      mock.patch.object(file_picker, "discover_files") as discover,
      self.assertRaises(SystemExit) as error,
    ):
      file_picker.select_files(picker_args(), sources=[self.root], multiple=True)
    self.assertEqual(error.exception.code, 2)
    discover.assert_not_called()
    self.assertIn("Missing: fd, fzf", self.errors.getvalue())

  def test_terminal_required_before_discovery(self):
    self.terminal.return_value = False
    with (
      mock.patch.object(file_picker.shutil, "which", return_value="/bin/tool"),
      mock.patch.object(file_picker, "discover_files") as discover,
      self.assertRaises(SystemExit) as error,
    ):
      file_picker.select_files(picker_args(), sources=[self.root], multiple=True)
    self.assertEqual(error.exception.code, 2)
    discover.assert_not_called()

  def test_bat_is_optional(self):
    selected, run = self.select(selection_output(self.files[:1]), bat=False)
    self.assertEqual(selected, [self.files[0]])
    self.assertIn("continuing without previews", self.errors.getvalue())
    self.assertFalse(any(
      arg.startswith("--preview=") for arg in run.call_args.args[0]
    ))

  def test_cancel_no_match_and_failed_picker_do_not_return_files(self):
    for code in (130, 1, 2):
      with self.subTest(code=code), self.assertRaises(SystemExit) as error:
        self.select(selection_output(self.files[:1]), code)
      self.assertEqual(error.exception.code, code)

  def test_invalid_paths_output_and_multiple_dd_sources_are_rejected(self):
    outputs = [
      (selection_output([self.root / "outside.txt"]), True),
      (selection_output(self.files[:1]).rstrip(b"\0"), True),
      (b"", True),
      (selection_output(self.files), False),
    ]
    for output, multiple in outputs:
      with self.subTest(output=output), self.assertRaises(SystemExit):
        self.select(output, multiple=multiple)

  def test_fd_failure_is_reported_before_selection(self):
    with (
      mock.patch.object(file_picker.subprocess, "run", return_value=(
        subprocess.CompletedProcess([], 2, stdout=b"", stderr=b"fd failed")
      )) as run,
      self.assertRaises(SystemExit) as error,
    ):
      file_picker.discover_files("/bin/fd", [self.root], picker_args())
    self.assertEqual(error.exception.code, 2)
    self.assertEqual(run.call_count, 1)
    self.assertIn("fd failed", self.errors.getvalue())

  @unittest.skipUnless(FD and FZF, "fd/fzf are not installed")
  def test_installed_fzf_accepts_picker_options_and_preserves_paths(self):
    # Filter mode validates the real options without requiring terminal input.
    original_run = subprocess.run

    def filter_run(command, **kwargs):
      if command[0] == FZF:
        command = [*command, "--filter=.txt"]
      return original_run(command, **kwargs)

    for multiple in (False, True):
      sources = self.files if multiple else self.files[:1]
      with (
        self.subTest(multiple=multiple),
        mock.patch.object(file_picker.subprocess, "run", side_effect=filter_run),
      ):
        selected = file_picker.select_files(
          picker_args(), sources=sources, multiple=multiple,
        )
      self.assertEqual(set(selected), set(sources))

  @unittest.skipUnless(FD, "fd is not installed")
  def test_real_fd_filters_hidden_ignored_and_compound_extensions(self):
    (self.root / ".hidden.txt").write_text("hidden")
    (self.root / "ignored.txt").write_text("ignored")
    (self.root / ".ignore").write_text("ignored.txt\n")
    archive = self.root / "backup.TAR.GZ"
    archive.write_text("archive")
    default = file_picker.discover_files(
      FD, [self.root], picker_args(extension=[".txt"]),
    )
    self.assertEqual(set(default), set(self.files))
    expanded = file_picker.discover_files(
      FD, [self.root],
      picker_args(extension=["txt"], hidden=True, no_ignore=True),
    )
    self.assertEqual(set(expanded), {
      *self.files, self.root / ".hidden.txt", self.root / "ignored.txt",
    })
    compound = file_picker.discover_files(
      FD, [self.root, archive], picker_args(extension=["tar.gz"]),
    )
    self.assertEqual(compound, [archive])


# -- rsync planning and dry runs ----------------------------------------------

class TransferTests(unittest.TestCase):
  def setUp(self):
    self.temporary = tempfile.TemporaryDirectory()
    self.addCleanup(self.temporary.cleanup)
    self.root = Path(self.temporary.name)
    self.source = self.root / "source"
    self.nested = self.source / "nested"
    self.nested.mkdir(parents=True)
    self.selected = self.nested / "chosen.txt"
    self.unselected = self.nested / "leave.txt"
    self.selected.write_text("chosen")
    self.unselected.write_text("leave")
    self.destination = self.root / "destination"

  def plan(self, **changes):
    values = {
      "sources": [self.source], "destination": self.destination,
      "preserve_structure": False, "base_dir": self.root, "auto_rename": False,
      "skip_conflicts": False, "overwrite": False,
      "selected_files": [self.selected],
    }
    values.update(changes)
    return rsafe.build_plan(**values)

  def test_selected_subset_keeps_directory_layout_without_another_walk(self):
    with mock.patch.object(rsafe, "iter_source_files") as walk:
      transfers, skipped, conflicts = self.plan()
    self.assertEqual([item.source for item in transfers], [self.selected])
    self.assertEqual(
      transfers[0].target, self.destination / "source/nested/chosen.txt",
    )
    self.assertFalse(skipped or conflicts)
    walk.assert_not_called()

  def test_preserve_structure_uses_search_base(self):
    transfers, _, _ = self.plan(
      sources=[self.selected], preserve_structure=True, base_dir=self.source,
    )
    self.assertEqual(transfers[0].target, self.destination / "nested/chosen.txt")

  def test_overlap_cannot_transfer_or_delete_the_same_selected_source_twice(self):
    transfers, _, conflicts = self.plan(sources=[self.source, self.nested])
    self.assertEqual(len(transfers), 1)
    self.assertFalse(conflicts)

  def test_conflict_modes_apply_to_selected_subset(self):
    target = self.destination / "source/nested/chosen.txt"
    target.parent.mkdir(parents=True)
    target.write_text("existing")
    transfers, _, conflicts = self.plan()
    self.assertFalse(transfers)
    self.assertEqual(len(conflicts), 1)
    renamed, _, _ = self.plan(auto_rename=True)
    self.assertEqual(renamed[0].target.name, "chosen_[1].txt")
    transfers, skipped, conflicts = self.plan(skip_conflicts=True)
    self.assertFalse(transfers or conflicts)
    self.assertEqual(len(skipped), 1)
    overwritten, _, _ = self.plan(overwrite=True)
    self.assertEqual(overwritten[0].note, "overwrite")
    self.assertEqual(target.read_text(), "existing")

  def test_interactive_dry_run_leaves_destination_absent(self):
    argv = [
      "rsafe", "--interactive", "--search-dir", str(self.source),
      "--preserve-structure", "--dry-run", str(self.destination),
    ]
    with (
      mock.patch.object(sys, "argv", argv),
      mock.patch.object(rsafe, "require_rsync"),
      mock.patch.object(
        file_picker, "select_files", return_value=[self.selected],
      ),
      mock.patch.object(rsafe, "execute_transfers") as execute,
      redirect_stdout(io.StringIO()),
    ):
      self.assertEqual(rsafe.main(), 0)
    execute.assert_not_called()
    self.assertFalse(self.destination.exists())
    self.assertTrue(self.selected.exists() and self.unselected.exists())

  def test_cancel_leaves_destination_absent_even_with_source_deletion(self):
    argv = [
      "rsafe", "--interactive", "--delete-source-files",
      str(self.source), str(self.destination),
    ]
    with (
      mock.patch.object(sys, "argv", argv),
      mock.patch.object(rsafe, "require_rsync"),
      mock.patch.object(file_picker, "select_files", side_effect=SystemExit(130)),
      mock.patch.object(rsafe, "execute_transfers") as execute,
      self.assertRaises(SystemExit) as error,
    ):
      rsafe.main()
    self.assertEqual(error.exception.code, 130)
    execute.assert_not_called()
    self.assertFalse(self.destination.exists())
    self.assertTrue(self.selected.exists() and self.unselected.exists())


# -- dd arguments and compatibility -------------------------------------------

class DDArgumentTests(unittest.TestCase):
  def parse(self, argv):
    parser = rdd.build_parser()
    args = parser.parse_args(rdd.normalize_legacy_argv(argv))
    rdd.validate_parsed_args(parser, args)
    return args

  def test_copy_and_verify_keep_explicit_targets_in_interactive_mode(self):
    for command in ("copy", "verify"):
      with self.subTest(command=command):
        args = self.parse([command, "--interactive", "target.img"])
        self.assertIsNone(args.source)
        self.assertEqual(args.target, "target.img")

  def test_legacy_copy_and_zero_verification_keep_their_arguments(self):
    args = self.parse(["source.img", "target.img"])
    self.assertEqual((args.command, args.source, args.target), (
      "copy", "source.img", "target.img",
    ))
    args = self.parse(["verify", "--zero", "target.img"])
    self.assertIsNone(args.source)
    self.assertEqual(args.target, "target.img")

  def test_invalid_combinations_fail_before_any_operation(self):
    invalid = [
      ["copy", "target.img"],
      ["copy", "source.img", "target.img", "-e", "iso"],
      ["copy", "source", "target", "--interactive", "--search-dir", "."],
      ["verify", "--interactive", "--zero", "target.img"],
      ["verify", "--interactive"],
    ]
    for argv in invalid:
      with (
        self.subTest(argv=argv), redirect_stderr(io.StringIO()),
        self.assertRaises(SystemExit) as error,
      ):
        self.parse(argv)
      self.assertEqual(error.exception.code, 2)


# -- I/O scheduling -----------------------------------------------------------

class IoniceTests(unittest.TestCase):
  @unittest.skipUnless(shutil.which("ionice"), "ionice is not installed")
  def test_rdd_priority_is_observable_in_an_isolated_child_process(self):
    # Change the child's priority, then ask the kernel through ionice itself.
    probe = """
import argparse
import importlib.util
import os
import subprocess
import sys
spec = importlib.util.spec_from_file_location("priority_probe", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module.configure_io_priority(argparse.Namespace(
  command="copy", no_ionice=False, ionice_priority=int(sys.argv[2]), verbose=False,
))
result = subprocess.run(
  ["ionice", "-p", str(os.getpid())], capture_output=True, text=True, check=False,
)
sys.stdout.write(result.stdout)
sys.exit(result.returncode)
"""
    for priority in (0, 7):
      with self.subTest(priority=priority):
        result = subprocess.run(
          [sys.executable, "-B", "-c", probe,
           str(PROJECT / "rdd-ionice.py"), str(priority)],
          capture_output=True, text=True, check=False,
          env={**os.environ, "LC_ALL": "C"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), f"best-effort: prio {priority}")

  def test_rdd_disabled_and_inspect_do_not_change_priority(self):
    for command, disabled in (("copy", True), ("inspect", False)):
      with (
        self.subTest(command=command, disabled=disabled),
        mock.patch.object(rdd.subprocess, "run") as run,
      ):
        rdd.configure_io_priority(argparse.Namespace(
          command=command, no_ionice=disabled,
        ))
        run.assert_not_called()

  @unittest.skipUnless(
    shutil.which("rsync") and shutil.which("ionice"),
    "rsync/ionice are not installed",
  )
  def test_rsync_copies_through_ionice_with_the_requested_priority(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory)
      source = root / "source with space.txt"
      target = root / "target.txt"
      source.write_bytes(b"ionice regression sample\n")
      logs = []
      rsafe.rsync_one_file(
        rsafe.TransferItem(source, target, source.stat().st_size),
        quiet=True, ionice=True, priority=7, verbose=True, log=logs.append,
      )
      self.assertEqual(target.read_bytes(), source.read_bytes())
      command = shlex.split(logs[0].removeprefix("Running: "))
      self.assertEqual(command[:5], ["ionice", "-c", "2", "-n", "7"])


if __name__ == "__main__":
  unittest.main()
