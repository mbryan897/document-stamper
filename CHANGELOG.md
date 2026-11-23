# What's new in Document Stamper

The newest changes are first. Each released version lists what changed since the one before.

## 1.1.0

Usability fixes from a review of the whole program, and the things a released program is expected to have.

### Stamping

- Leaving the stamp screen with values typed in asks first, and the window's title names the file.
- "Stamp another with the same values" on the saved screen carries the values and placement to the next file, with a note on each carried field until it's checked. The profile can be switched on the stamp screen without losing what's typed. The first screen lists the copies stamped since the program started.
- Dates can be typed with `/`, `.` or `-` and one-digit months and days, in the order the settings file sets. Each field shows the format it expects before anything is typed, and error messages describe a rule in words instead of showing the rule itself.
- When the white box behind the stamp would cover text already on the page, a note says which pages, and that the covered text is still in the file: the box is not redaction.
- A protected original no longer decides for you what its stamped copy gets. You choose: keep the original's password and permissions, no password, or a new password. Nothing is chosen in advance when the original needed a password to open. A new password is never saved anywhere.
- Save As suggests a name that's free, asks again instead of refusing after the window has closed, and starts in the folder used last. A batch remembers its output folder.
- The saved screen shows long paths whole: they wrap onto as many lines as they need instead of being cut off. Right-click a path to copy it.
- The copy's modification date carries the right time-zone offset all year. In winter it was written with the summer offset, so the time read an hour off in other time zones.
- The stamped copy can record which original it came from and when it was stamped, through the profile's document properties. The note about a second set of properties (XMP) says what the receiver will see.

### Several files

- Notes reach the reader: the saved screen puts "Check the copy" notes first and can show all of them, and batch results show notes for files that saved.
- Re-running an interrupted batch says which files already have a stamped copy, instead of calling them files that can't be stamped. "Show in folder" works when copies went to several folders.
- The notice about a copy left unfinished after a crash names the original. The note the program keeps for this holds the original's file name only, never its folder.
- A combined file can have a password of its own. When any of the files needed a password to open, you're asked: no password, or set one. A combined file saved without one says how many of its files were protected.
- Stamping several files separately asks the same question once for all the protected files in the set.
- Combining builds its unprotected working copy only in your private work folder. If that folder can't be used, the files aren't combined, instead of the working copy going in a hidden folder next to them. A working folder left by a crash is removed the next time the program starts, from the window or the command line.

### Profile settings

- Removing a profile, field, list or rule, and changing a field's type, can be undone. Delete buttons are set apart.
- Save changes lists what changes and asks first. The last five versions are kept as dated backups, and "Restore a previous version…" loads one.
- "Paste choices" adds a list's choices from the clipboard.
- A settings file made by a newer version of the program says so in one sentence instead of a list of problems.
- "Remember the folders files are opened from and saved to", under General, can be turned off when folder names may contain case details. No folder is then kept in `state.json`, and any already there are removed. A settings file with it turned off can't be read by version 1.0.0: put it in place only after everyone has this version.
- The folder an Open or Save window was last in is no longer left in the Windows registry by Qt, the part the window is built with.
- "Remove my settings from this computer…" removes what the program keeps about you (never a settings file).
- The built-in settings are simpler: the stamp is the identifier and the page number. Document type is optional and, when filled in, goes in the file name after the identifier. The only document property the built-in profile writes is `Identifier`.

### Getting around

- Bigger stamp-position buttons, stronger borders, access keys, arrow keys in the position grid, Ctrl+O to choose files, and keys to reorder and remove files.
- The first screen has one "Organization setup…" menu for whoever looks after the tool, a link to add the Send to shortcut while it's missing, and a Help menu: Help, Keyboard shortcuts, What's new, Open settings folder, and About Document Stamper.
- About shows the version numbers, the license, the third-party notices and what the program writes to the computer, with "Copy details" for a support request.
- `SECURITY.md` lists everything the program writes, how saved copies are protected, and what it expects of the computer: disk encryption, and that the white box is never used to redact.
- An unexpected error now shows a plain message and closes the program cleanly, instead of the window vanishing. The originals are never changed.

## 1.0.0

The first complete version, used for the agency trial (19 October 2026).

- **Stamps and settings.** Every agency rule lives in one settings file: profiles, fields with validation rules written in a simple pattern syntax, lists to choose from, the date format and how saved copies are named. A field's value can be filled in from the file name.
- **PDF stamping.** The stamp goes on every page in one of nine positions, along the top or the right edge, in the bundled Atkinson Hyperlegible Mono font, with an optional white box behind it and optional page numbers. It's placed correctly on rotated and cropped pages, and shrinks on a page where it wouldn't fit. Signed, password-protected and restricted PDFs are detected and explained. The original is never changed and an existing file is never overwritten.
- **The window.** Drop or choose a PDF, fill in the fields with a live preview, then save. Profile settings edits the settings file, with backups, import and export, and adds Document Stamper to Explorer's Send to menu.
- **Several files at once.** Combine several PDFs into one stamped file, or stamp each one separately with the same values.
- **Safety.** Passwords never go on the command line, hidden characters in names are shown, a source file that changed after it was opened is refused, partial copies left by a crash are found and offered for deletion, and risky rules are refused.
- **Help.** Help on every screen and F1, with contents and search.
- **Command line.** `stamper-cli` stamps, combines and batch-stamps from scripts, with exit codes.
- **Release.** One portable `DocumentStamper.exe` with its own icon, built from hash-locked dependencies and checked after the build. `SHA256SUMS.txt`, written by the build beside the program, holds the SHA-256 checksum of every program file built (`DocumentStamper.exe`, and the folder build's two programs when it's built); compare it with `Get-FileHash` before you run a copy you were sent. It is a checksum of the program, never of a document.
