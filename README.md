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
```

## Running tests

```powershell
python tools\make_corpus.py   # optional; pytest builds the corpus if it is missing
pytest
```

The test corpus is synthetic and generated on demand into `tests/corpus/generated/` (not committed). See `tests/corpus/README.md`. Never add real case material to this repository.

## License

GNU Affero General Public License v3.0 or later (AGPL-3.0-or-later), as required by PyMuPDF. See `LICENSE`. If you distribute a modified version, you must make your source available under the same license.
