# Test corpus

Synthetic PDFs that each break a stamper in a different way.

**Rule:** nothing from a real case goes in this folder or anywhere in the repo. No real PDFs, case numbers, names, addresses, provider returns or screenshots of them, even redacted.

## Generated files (`generated/`, not committed)

Built by `tools/make_corpus.py` with PyMuPDF. `pytest` builds any that are missing; `python tools\make_corpus.py --force` rebuilds all of them.

| File | Tests |
| --- | --- |
| `letter_portrait.pdf` | Baseline placement |
| `letter_landscape.pdf` | Width and height swapped |
| `rotate_90.pdf`, `rotate_180.pdf`, `rotate_270.pdf` | Every page at that `/Rotate` |
| `rotate_mixed.pdf` | Pages at 0, 90, 180, 270 in one file |
| `cropbox_offset.pdf` | CropBox (36, 54, 540, 720) inside a Letter MediaBox; MediaBox edge outlined in red |
| `mixed_sizes.pdf` | Letter, Legal, A4 and a 3.125 x 8 in receipt |
| `scanned_image_only.pdf` | Raster page, no text layer |
| `text_top_right.pdf` | Text right up to the top and right edges (background box) |
| `encrypted_user_password.pdf` | Password to open (`USER_PASSWORD` in the generator) |
| `owner_restrictions.pdf` | Opens freely; print, copy and modify restricted |
| `acroform_filled.pdf` | Filled text fields, one in the top-right corner |
| `large_520_pages.pdf` | Page count and responsiveness (small on disk; see below) |
| `provider_return_99-2026-999901.pdf` | Filename pre-fill, `##-YYYY-######` |
| `SO261017-999902_provider_return.pdf` | Filename pre-fill, `SOYYMMDD-######` |

Case numbers in filenames are fabricated (9999xx suffixes) and only need to match the format.

## Built inside the tests (Phase 2)

`tests/test_pdf_handler.py` derives three more files from the corpus in a temp folder on every run, so detection is always tested even without hand-made samples:

- **Signature-shaped field:** the AcroForm file with one field turned into a filled `/Sig` field and `SigFlags 3`. Tests detection and the confirmation flow, not cryptographic validity.
- **XFA:** the AcroForm file with an `/XFA` stream added to the AcroForm dictionary.
- **Damaged:** `letter_portrait.pdf` with its `startxref` offset broken, which MuPDF repairs on open.

## Files to add by hand

These can't be produced well with PyMuPDF alone. Put them in `tests/corpus/manual/` once made, and only commit them if they contain nothing but placeholder text. `test_manual_samples` uses them when present and skips with a reason when they aren't.

### Digitally signed PDF (`signed.pdf`)
- Make a placeholder PDF with the generator, then sign it with a **self-signed test certificate**, never an agency or personal certificate.
- Options: `pyHanko` (`pyhanko sign addsig --field Sig1 pemder --key test.key --cert test.crt in.pdf signed.pdf` with a cert made by `openssl req -x509 -newkey rsa:2048 -nodes -keyout test.key -out test.crt -subj "/CN=Stamper Test"`), or Adobe Acrobat's Fill & Sign / Certificates with a self-signed Digital ID.
- Keep pyHanko out of the project dependencies; install it in a throwaway venv.
- Expected: signature detected, warning shown, copy saved, original byte-identical.

### XFA form (`xfa_form.pdf`)
- Needs Adobe LiveCycle / AEM Forms Designer to author. Easier: a publicly posted blank government XFA form (many IRS and older state forms were XFA). Use a **blank** form only, never one filled with real data, and check its redistribution terms before committing; otherwise keep it local and untracked.
- Expected: clear error naming XFA as unsupported.

### Damaged file (`damaged_xref.pdf`)
- Derive from a generated file: copy `letter_portrait.pdf`, then corrupt the cross-reference data, e.g. overwrite the `startxref` offset with a wrong number, or truncate the last few hundred bytes so the trailer is gone.
- Expected: repaired by PyMuPDF and stamped, or a clear error.

### Large file by size (200+ MB)
- The generated large file covers page count, not size. For size, build pages from high-resolution random-noise images (noise doesn't compress), locally only; never commit it.
