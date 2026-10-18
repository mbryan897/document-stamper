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
.\tools\fetch_font.ps1          # bundled stamp font, hash-checked
```

## Running tests

```powershell
python tools\make_corpus.py   # optional; pytest builds the corpus if it is missing
pytest
```

The test corpus is synthetic and generated on demand into `tests/corpus/generated/` (not committed). See `tests/corpus/README.md`. Never add real case material to this repository.

## Command line

```powershell
python -m stamper "D:\Returns\provider return.pdf" --set identifier=99-2026-999901 --set "doc_type=Report"
python -m stamper --help
```

Fields are set with `--set NAME=VALUE` (repeat for each field; quote the whole argument when the value has spaces). Without `--out`, the copy goes next to the original under the suggested filename. Signed files and files with owner restrictions stop with a message naming the flag that confirms them (`--accept-signature-loss`, `--accept-restrictions`). Passwords are read with `--password-env VAR`, `--password-stdin` or a hidden prompt, never from the command line itself.

Exit codes: 0 saved, 1 unexpected error, 2 bad arguments, 3 config problem, 4 field values invalid, 5 file can't be stamped, 6 password missing or wrong, 7 confirmation flag needed, 8 output refused, 9 stamp doesn't fit.

## License

GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later), as required by PyMuPDF. See `LICENSE`. If you distribute a modified version, you must make your source available under the same license.

The bundled Atkinson Hyperlegible Mono font is under the SIL Open Font License 1.1; see `assets/fonts/OFL.txt` and `assets/fonts/SOURCE.txt`.
