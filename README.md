# Document Stamper

A portable Windows desktop app that adds a configurable text stamp (case number, document type, page numbers and other fields) to every page of a document and saves a new copy. The original file is never modified.

Version 1 handles PDF only. Images, Word and text files are planned through the same handler interface. All agency-specific rules (fields, case number formats, document types, stamp layout) live in `config.json`, so any agency or organization can adapt it without code changes.

**Status:** pre-alpha. Not ready for use on real documents.

## Development setup (PowerShell)

Requires Python 3.14 and Git.

```powershell
git clone https://github.com/MBryan897/document-stamper.git
Set-Location document-stamper
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
.\tools\fetch_font.ps1          # bundled fonts, hash-checked
```

## Running tests

```powershell
python tools\make_corpus.py   # optional; pytest builds the corpus if it is missing
pytest
```

The test corpus is synthetic and generated on demand into `tests/corpus/generated/` (not committed). See `tests/corpus/README.md`. Never add real case material to this repository.

## Window

```powershell
python -m stamper                                  # the drop screen
python -m stamper "D:\Returns\provider return.pdf"  # straight to the stamp dialog, as dragging a file onto the exe does
```

Drop one PDF on the window (or use "Choose files…"), check the fields and placement against the live preview, then "Stamp and save…". The copy is saved as a new file; the window never replaces an existing file or the original. With no arguments, or only existing files and no options, `python -m stamper` opens the window; anything else runs the command line.

## Help

Every screen has a Help button, and F1 opens the help at the topic for that screen. The help is the Markdown in [`assets/help`](assets/help/what-it-does.md), shown in the app's own Help window with contents and search, and readable here on GitHub too.

The pictures in `assets/help/images` are taken from the real window on synthetic files:

```powershell
python tools\make_help_screenshots.py          # all of them; --list shows the groups
```

Run it after changing a screen. `tests\test_help.py` fails when a topic links to something missing, shows a picture that isn't there, names a button in **bold** that the interface no longer has, or leaves out a button or a command-line option.

## Profile settings and where the config lives

"Profile settings…" on the drop screen edits profiles, fields, validation rules, lists and the date format, with a live preview and test boxes. Save writes the config back safely and keeps the previous version as `config.json.bak`; Import and Export move a whole config between machines.

The config is looked for in this order:

1. `config.json` next to the app (from source: the repo root, git-ignored). Copy the app folder and this file travels with it.
2. `%APPDATA%\DocumentStamper\config.json`.
3. The built-in `config\default.json` (never written to; saving it creates one of the files above).

`--config` on the command line and "Open another config…" in the window override the lookup. A config file you can't write to opens read-only. The last profile and the last folder you opened a file from are remembered in `%APPDATA%\DocumentStamper\state.json`; field values never are.

General → "Add to the Send to menu" puts Document Stamper in Explorer's right-click Send to menu.

## Test build

Builds use hash-locked dependencies (`requirements\*.lock`), so the exe contains exactly the packages that were tested. With Python 3.14 and the dev venv active:

```powershell
python -m pip install pip-tools==7.6.2 pip-audit==2.10.1   # first time only
.\tools\lock.ps1                                           # after changing requirements\*.in
python -m pip install --require-hashes -r requirements\dev.lock
.\packaging\build.ps1
```

`build.ps1` checks the Python version, runs `pip-audit` on `requirements\build.lock` (it needs the network), installs only from the lock into a fresh `.venv-build`, and writes `dist\SHA256SUMS.txt`. `pip-audit` sees advisories filed against the PyPI packages, not CVEs in the MuPDF library inside PyMuPDF; when PyMuPDF changes, check its MuPDF version against NVD or OSV by hand.

This makes two unsigned test builds:

- `dist\DocumentStamper.exe`: one file that runs by itself from any folder or drive. Each launch unpacks its support files to a temporary folder first, so it takes a few seconds to open. Put `config.json` next to it to carry your profiles with it.
- `dist\DocumentStamper-folder\`: the folder build (fallback if antivirus objects to the one-file exe). Copy the whole folder; its `DocumentStamper.exe` is a small launcher that needs `_internal\` beside it. It also holds `stamper-cli.exe` for scripts.

Run the build from a normal (not administrator) terminal.

## Command line

`stamper-cli` always runs the command line. `python -m stamper` does too whenever an option is given (add `--` before a lone file name to force it).

```powershell
python -m stamper "D:\Returns\provider return.pdf" --set identifier=99-2026-999901 --set "doc_type=Report"
python -m stamper --help
```

Without `--config`, the command line uses the same lookup as the window (see above).

Fields are set with `--set NAME=VALUE` (repeat for each field; quote the whole argument when the value has spaces). Without `--out`, the copy goes next to the original under the suggested filename. Signed files and files with owner restrictions stop with a message naming the flag that confirms them (`--accept-signature-loss`, `--accept-restrictions`). Passwords are read with `--password-env VAR`, `--password-stdin` or a hidden prompt, never from the command line itself. With `--combine` or `--batch`, give each file's password with `--password-env-for FILE VAR` (repeat for each file) or `--passwords-stdin` (one line per file, in the order the files are given; an empty line for a file without one).

Exit codes: 0 saved, 1 unexpected error, 2 bad arguments, 3 config problem, 4 field values invalid, 5 file can't be stamped, 6 password missing or wrong, 7 confirmation flag needed, 8 output refused, 9 stamp doesn't fit, 10 a batch saved some files but not all.

## License

GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later), as required by PyMuPDF. See `LICENSE`. If you distribute a modified version, you must make your source available under the same license.

The bundled Atkinson Hyperlegible Mono and Atkinson Hyperlegible Next fonts are under the SIL Open Font License 1.1; see `assets/fonts/OFL.txt`, `assets/fonts/OFL-AtkinsonHyperlegibleNext.txt` and `assets/fonts/SOURCE.txt`.
