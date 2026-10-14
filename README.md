# Optional interactive file selection

All three wrappers support `--interactive` (`-i`) with `fd` discovery and an `fzf`
picker. Keep [file_picker.py](file_picker.py) alongside the scripts when using
this option. Normal command line use does not load the companion module or
require `fd`, `fzf`, or `bat`.

On Arch Linux, the optional packages are:

```sh
sudo pacman -S --needed fd fzf bat
```

If `fd` or `fzf` is missing from `PATH`, requesting `--interactive` exits with
an installation hint before starting the operation. `bat` is optional: its
absence prints a warning and opens the picker without a preview. The existing
Python/Rich, GNU coreutils, util-linux, and rsync requirements still apply.

## Options and controls

| Option | Effect |
| --- | --- |
| `--interactive`, `-i` | Open the picker; requires a terminal on stdin. |
| `--search-dir DIR` | Search this directory; defaults to the current directory. |
| `--extension EXT`, `-e EXT` | Match an extension; repeat to include several. |
| `--hidden` | Include hidden files and directories. |
| `--no-ignore` | Include files excluded by ignore rules. |
| `--no-preview` | Disable the `bat` preview. |

The additional options require `--interactive`. Extensions such as `png`, `.png`,
and `tar.gz` are accepted and matched without regard to case. `fd` discovers
regular files and normally skips hidden files and ignore patterns. An explicitly
supplied source file is included when its extension matches the requested filter.
Use either positional sources or `--search-dir` to supply candidates.

Type to filter by filename/path. In rsync's picker, **Tab**, **Shift-Tab**, or
**Shift-click** marks files; **Ctrl-A** marks all currently matching files and
**Ctrl-D** clears marks. **Enter** accepts marked files, or the highlighted file
when no files are marked. **Esc** / **Ctrl-C** cancels before any transfer or
destination creation. Mouse support depends on the terminal forwarding those
events; the keyboard controls are always available.

When previews are enabled, **Ctrl-P** toggles them. `bat` shows the first 300
lines with syntax highlighting; binary files such as disk images normally show
its binary-file notice. Filenames containing spaces, quotes, or newlines are
preserved using NUL-separated records and encoded file identities. Names with
control characters or non-UTF-8 bytes are escaped for display. These pickers use
their own bindings and ignore `FZF_DEFAULT_OPTS` / `FZF_DEFAULT_OPTS_FILE` so
global options cannot
automatically accept files or change the output format.

## rsafe-rsync.py

Select several image files, then inspect the plan:

```sh
./rsafe-rsync.py --interactive --search-dir "$HOME/Pictures" \
  -e png -e jpg --dry-run /shared/pictures
```

With no positional source, selected files go directly into the destination.
To preserve subdirectories beneath the search root:

```sh
./rsafe-rsync.py --interactive --search-dir "$HOME/Pictures" \
  -e png -e jpg --preserve-structure --dry-run /shared/pictures
```

Alternatively, provide source directories or files as before, and select a
subset. A source directory retains the normal `DESTINATION/source-name/...`
layout; `--base-dir` can still be used with `--preserve-structure`.

```sh
./rsafe-rsync.py --interactive -e pdf --auto-rename --dry-run \
  "$HOME/Documents" /shared/documents
```

Conflict handling and deletion after successful copying apply to the chosen
files. `--prune-empty-dirs` prunes the supplied source directories, or the search
directory when no positional source was given. Dry runs do not create the
destination directory. Remove `--dry-run` to perform the planned transfers.

## rdd-ionice.py

The `copy` and SHA-256 `verify` commands select **one source file**. Supply the
target explicitly: `dd` performs one source-to-target operation.

```sh
./rdd-ionice.py copy --interactive --search-dir "$HOME/Downloads" \
  -e iso -e img --dry-run backup.img

./rdd-ionice.py verify --interactive --search-dir "$HOME/Images" \
  -e img backup.img
```

A positional source directory or file can also supply candidates:

```sh
./rdd-ionice.py copy --interactive "$HOME/Images" backup.img --dry-run
```

Block-device targets still require the existing permission, mount/swap, and
confirmation checks. The picker applies to regular source files for `copy` and
SHA-256 `verify`; `wipe`, `discard`, `inspect`, and `verify --zero` use their
existing explicit arguments.

## rsafe-7z.py

Create 7z archives or extract formats supported by the installed `7z`/`7zz`.
The wrapper uses Rich for the operation plan, native percentage progress, elapsed
time, and completion messages, with a plain text fallback when Rich is absent.
Keep `file_picker.py` beside the wrapper for interactive selection.

On Arch Linux, install all wrapper and picker dependencies in one command:

```sh
sudo pacman -S --needed python python-rich 7zip fd fzf bat
```

`fd`, `fzf`, and `bat` are optional for commands with explicit input paths;
`bat` is also optional within the picker. The wrapper prefers `7zz` when
available and otherwise uses `7z`.

Like `rsafe-rsync.py`, input paths come first and the output path comes last:

```sh
./rsafe-7z.py a "$HOME/Documents" documents.7z --dry-run
./rsafe-7z.py --archive "$HOME/Documents" documents.7z

./rsafe-7z.py e documents.7z restored-documents
./rsafe-7z.py --extract documents.7z restored-documents --flatten
```

`a` and `--archive` select the same action; `e` and `--extract` select the same
action. Extraction preserves directories by default, using native `7z x`.
`--flatten` uses native `7z e`, placing files directly in the output directory;
native empty directory entries can still be created. New archives always use
the 7z format; `.7z` is appended when the output name has no extension.

Choose input extensions in fzf **before choosing files**:

```sh
./rsafe-7z.py a --pick-types --search-dir "$HOME/Pictures" photos.7z

./rsafe-7z.py e --pick-types --search-dir "$HOME/Downloads" restored
```

`--pick-types` implies `--interactive`. The first picker lists discovered
extensions and file counts, including files without extensions and compound
types such as `tar.gz`. Tab marks multiple types; Enter opens the normal file
picker with only those types. Esc cancels either step without creating output.
Both pickers isolate global fzf options and preserve encoded file identities.

To specify types directly and go straight to the file picker:

```sh
./rsafe-7z.py a -i --search-dir "$HOME/Pictures" -e png -e jpg photos.7z

./rsafe-7z.py e -i --search-dir "$HOME/Downloads" -e 7z -e zip restored
```

The extraction filter applies to **input archives on disk**, rather than members
inside an archive. `--hidden`, `--no-ignore`, and `--no-preview` work as in the
other wrappers. Explicit input directories/files can supply picker candidates
instead of `--search-dir`.

Archive paths are relative to the common parent of the supplied input paths.
A supplied directory keeps its own name, for example `Documents/report.pdf`.
When inputs are omitted in interactive mode, paths are relative to the search
directory. `--base-dir DIR` sets that base explicitly. Multiple input archives
extract into separate `DESTINATION/archive-name/` directories; a single archive
extracts directly into `DESTINATION`.

For password protection and encrypted filenames:

```sh
./rsafe-7z.py a "$HOME/Documents" private.7z --encrypt-headers
./rsafe-7z.py e private.7z restored --ask-password

# Explicit passwords and the native header switch are also accepted.
./rsafe-7z.py a "$HOME/Documents" private.7z \
  --password 'example-password' -mhe=on
./rsafe-7z.py e private.7z restored -p'example-password'
```

`-p PASSWORD`, `-pPASSWORD`, and `--password PASSWORD` are supported. Use
`--ask-password` for private entry, with confirmation when archiving.
`--encrypt-headers` and `-mhe=on` are equivalent and prompt when no password was
supplied; `-mhe=off` disables header encryption. Dry runs never prompt.
Passwords are redacted from wrapper plans, commands, and captured diagnostics.
Explicit passwords can enter shell history; 7z also receives passwords in its
process arguments, including passwords collected by the private prompt.

| Conflict option | Archive creation | Extraction |
| --- | --- | --- |
| Default | Abort if the archive exists. | Skip existing files (`-aos`). |
| `--skip-conflicts` | Skip the existing archive. | Skip existing files. |
| `--auto-rename` | Create `name_[1].7z`, etc. | Rename incoming files (`-aou`). |
| `--overwrite` | Replace with a new archive. | Overwrite existing files (`-aoa`). |

Archive creation stages the result beside the output and publishes it only
after 7z succeeds. A replacement contains only the current inputs; old members
are not retained. An output inside a source directory, and the staging
directory, are excluded from compression. Source files are kept.
Extraction uses 7z directly; an error or cancellation can leave files already
extracted. Native nonzero exit codes are returned to the caller.

Use `--level 0..9` to set compression (default: 5), `--dry-run` to inspect the
plan without writing files, and `--verbose` / `-V` for redacted command output.
`./rsafe-7z.py --help` includes the full options and examples.

## Verification

Run the regression checks with Python's standard library:

```sh
python3 -m unittest discover -s tests -v
```

The tests use temporary files and mock picker interactions; discovery and
option checks also use the installed `fd`/`fzf` when available. The 7z checks
exercise real archive/extract round trips, header encryption, literal filenames,
conflict modes, and failed-compression preservation when `7z`/`7zz` is installed.
They never write to block devices.

## Installation

Install the wrappers and companion module into a directory on `$PATH`, for
example `/usr/local/bin`:

```sh
sudo install -Dm755 rsafe-rsync.py \
  /usr/local/bin/rsafe-rsync
sudo install -Dm755 rdd-ionice.py \
  /usr/local/bin/rdd-ionice
sudo install -Dm755 rsafe-7z.py \
  /usr/local/bin/rsafe-7z
sudo install -Dm644 file_picker.py \
  /usr/local/bin/file_picker.py
```

If `/usr/local/bin` is absent from `$PATH`, add this line to your shell startup
file (`~/.zshrc` for Zsh, or `~/.bashrc` for Bash):

```sh
export PATH="/usr/local/bin:$PATH"
```

Reload the startup file for the shell you use. For Zsh:

```sh
source ~/.zshrc
```
