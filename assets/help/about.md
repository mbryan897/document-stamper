# About Document Stamper

Version @VERSION@

Document Stamper adds a configurable stamp to every page of a document and saves a new copy, leaving the original unchanged. It runs on Windows, needs no installing, and keeps all of an agency's rules in one config file.

## License

Document Stamper is free software under the GNU Affero General Public License, version 3 or later (AGPL-3.0). You may use, copy, change and share it under that license's terms. The full license is in the `LICENSE` file with the source code, and at `https://www.gnu.org/licenses/agpl-3.0.html`.

If you give the app, or a changed version of it, to anyone else, the AGPL requires that its source code be available to them too.

## Source code

The source code, its history and the decisions behind it are published at:

`https://github.com/mbryan897/document-stamper`

The help doesn't open web pages; copy the address into your browser.

## The About window

**Help** › **About Document Stamper** on the first screen shows this page with the version numbers of the program and of the parts below as they are in use. **License…** shows the full license, and **Third-party notices…** the licenses of the parts it's built with (the `THIRD-PARTY-NOTICES` file with the source code).

**Copy details** puts a few lines on the clipboard to paste into a support request: the version numbers, the Windows version, whether the program is the packaged one, and which settings file is in use, where it is and whether it can be changed. Nothing about your documents, folders, profiles or fields.

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

## Privacy

Document Stamper never connects to the internet or to any network, sends nothing anywhere and keeps no log. It writes only these:

- The stamped copy, where you save it. While saving, it writes a temporary `~stamper-….tmp` file beside it and renames it when it's complete. If the program stops part-way, the next start offers to delete what's left.
- A small note of each save in progress, in `%LOCALAPPDATA%\DocumentStamper\pending`, with where the temporary file is, the time, and the original's name but not its folder, removed when the save finishes. It lets the next start find a copy that wasn't finished.
- When combining files, a working copy in a `~stamper-combine-…` folder in `%LOCALAPPDATA%\DocumentStamper\work`, removed when you're done, or at the next start if the program stopped. If that folder can't be used, the files aren't combined and nothing is written.
- The settings file, `config.json`, when you save Profile settings, with `config.json.bak` and up to five dated backups beside it; and a copy where you choose when you use **Export…**.
- `state.json` in `%APPDATA%\DocumentStamper`: the last profile, the last folders used (unless the settings file turns that off) and the last settings file found. Never anything typed into a field.
- The Send to shortcut, `Document Stamper.lnk` in `%APPDATA%\Microsoft\Windows\SendTo`, only when you add it.
- The last folder used in an Open or Save window, in the Windows registry under `HKCU\Software\QtProject\OrganizationDefaults\FileDialog`. Qt, the part the window is built with, keeps this itself, whether or not the settings file turns folder memory off, and **Remove my settings from this computer…** doesn't remove it.
- While Profile settings is open, a made-up one-page sample PDF for the preview, in a temporary folder (`%TEMP%\stamper-sample-…`), removed when you leave Profile settings.
- Each time the single-file program starts, it unpacks itself into a temporary folder (`%TEMP%\_MEI…`). The folder is removed when the program closes normally, but can be left behind if it's stopped by force.
- To check that a folder can be written to, and when it saves `config.json` or `state.json`, a short-lived `~stamper-….tmp` file that is removed straight away.

Passwords are never written anywhere. They're kept in memory only for as long as they're needed.

Windows itself remembers the folders you use in its Open and Save windows; that's Windows, not Document Stamper. To remove what the program keeps about you, see [Removing your settings from this computer](where-the-config-lives.md#removing-your-settings-from-this-computer).

## No warranty

The app is provided as it is, without any warranty; see the license. Check every stamped copy before you rely on it.
