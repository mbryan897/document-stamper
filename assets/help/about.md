# About Document Stamper

Version @VERSION@

Document Stamper adds a configurable stamp to every page of a document and saves a new copy, leaving the original unchanged. It runs on Windows, needs no installing, and keeps all of an agency's rules in one config file.

## License

Document Stamper is free software under the GNU Affero General Public License, version 3 or later (AGPL-3.0). You may use, copy, change and share it under that license's terms. The full license is in the `LICENSE` file with the source code, and at `https://www.gnu.org/licenses/agpl-3.0.html`.

If you give the app, or a changed version of it, to anyone else, the AGPL requires that its source code be available to them too.

## Source code

The source code, its history and the decisions behind it are published at:

`https://github.com/MBryan897/document-stamper`

The help doesn't open web pages; copy the address into your browser.

## Built with

| Part | License |
| --- | --- |
| PyMuPDF and MuPDF, for reading and writing PDFs | AGPL-3.0 |
| Qt for Python (PySide6), for the window | LGPL-3.0 |
| pydantic, for checking config files | MIT |
| Python | PSF License |

## Fonts

- Atkinson Hyperlegible Mono, used for the stamp and for identifiers in the window. It's embedded in every stamped copy.
- Atkinson Hyperlegible Next, used for the window.

Both are copyright the Atkinson Hyperlegible Mono and Next Project Authors, designed for the Braille Institute of America, and are under the SIL Open Font License 1.1. The license texts are in `assets/fonts` with the source code.

## No warranty

The app is provided as it is, without any warranty; see the license. Check every stamped copy before you rely on it.
