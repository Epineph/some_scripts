# Optional interactive file selection

Both scripts support `--interactive` (`-i`) with `fd` discovery and an `fzf`
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

## Verification

Run the regression checks with Python's standard library:

```sh
python3 -m unittest discover -s tests -v
```

The tests use temporary files and mock picker interactions; discovery and
option checks also use the installed `fd`/`fzf` when available. They never write
to block devices.

## installation

You can install easily by choosing a path, found on `$PATH`, e.g., `/usr/local/bin` like::

```sh
sudo install -Dm755 rsafe-rsync.py \
  /usr/local/bin/rsafe-rsync
sudo install -Dm755 rdd-ionice.py \
  /usr/local/bin/rdd-ionice
sudo install -Dm644 file_picker.py \
  /usr/local/bin/file_picker.py
```

The path can be found to `path`, by appending it to the `rc` (e.g., `~/.bashrc`, `~/.zshrc`, etc.) associated with the `$SHELL` you are using - for example:

```sh
echo 'export PATH="/usr/local/bin:$PATH"' | sudo tee -a ~/.zshrc'
source ~/.zshrc
```