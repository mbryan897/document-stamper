# Security

## Reporting a vulnerability

Please report a security problem privately, not in a public issue:

- Use GitHub's private vulnerability reporting: the **Security** tab of this repository, then **Report a vulnerability**. Only the maintainer sees the report.
- If that isn't available to you, open a public issue that says only that you have a security report and asks for a private way to send it. Don't describe the problem in the issue.

Say which version you used (Help › About Document Stamper › Copy details gives the version numbers and nothing about your documents), what you did, what happened, and what you expected. A small made-up PDF or settings file that shows the problem helps most.

**Never attach real case material.** No real documents, case numbers, names, or settings files with an agency's real rules, and no screenshots that show any of them. Make a sample with made-up values instead (for example, case number `99-2026-999901`). A report that contains real case material will be deleted unread.

You should get an answer within two weeks. Please give a fix a reasonable time before you publish anything about the problem.

## Which versions get fixes

Only the newest released version gets security fixes. A fix comes out as a new version; older versions are not patched. Check the version you have under Help › About Document Stamper, and compare the checksum of a copy you were given with the `SHA256SUMS.txt` published with that version.

## What is in scope

Document Stamper runs on one computer, opens no network connection and keeps no log. The things worth reporting include: a PDF, file name or settings file that makes the program change the original, overwrite a file, write outside the places listed under Help › About Document Stamper › Privacy, run something, or hide part of a stamp; a stamped copy that isn't what the screen showed; and any way the program could be made to connect to a network.

## What is stored, and what the computer must provide

This section is for whoever reviews the program before it's used. Help › About Document Stamper › Privacy gives users the same list in fewer words.

### What the program writes

- **The stamped copy**, where the user saves it. While it's being written it is a `~stamper-….tmp` file in the same folder, renamed when it's complete and deleted if the save fails. If the program is stopped part-way, that file stays; the next time the window starts, it names the file and offers to delete it. The command line names it and deletes nothing.
- **A note of each save in progress**, one small file in `%LOCALAPPDATA%\DocumentStamper\pending`, written before the copy and removed once the temporary file is gone. It holds the temporary file's full path (so the folder being saved to), the program's process id, the time, and the original's file name without its folder. For a combined file, the name is the working copy's, which is named after the first file combined. An older note that holds a full path is shown as a name only.
- **The combine working copy.** Combining builds one unencrypted PDF in a new `~stamper-combine-…` folder in `%LOCALAPPDATA%\DocumentStamper\work`, with a `.stamper-work` file holding the process id, the start time and the program's version. The folder is removed when the combined file is saved, on cancel, on failure and when the window closes. A folder left by a stopped program is removed the next time the window or the command line starts, or the next time files are combined. If the work folder can't be created, is on a network drive or is short of space, nothing is combined and no working copy is written anywhere else.
- **`state.json`** in `%APPDATA%\DocumentStamper`, written by the window only. It can hold the name of the last profile used and the full path of the settings file found at the last start. With `settings.remember_folders` on, which is the default, it can also hold three folders (the one files were last opened from, the one a copy was last saved to, and the one a batch last saved to) and whether the last batch saved in one folder. With `settings.remember_folders` off, those four are not written, and any already in the file are removed when the settings file is loaded. Nothing typed into a field is ever written.
- **The settings file**, `config.json`, when Profile settings is saved, with `config.json.bak` and up to five dated backups (`config.json.YYYYMMDD-HHMMSS.bak`) beside it; and a copy wherever the user chooses with Export.
- **The Send to shortcut**, `Document Stamper.lnk` in `%APPDATA%\Microsoft\Windows\SendTo`, only when the user adds it.
- **Nothing lasting in Qt's file-window settings.** Qt, the toolkit the window is built with, saves the folder an Open or Save As window was last in under `HKCU\Software\QtProject\OrganizationDefaults\FileDialog` (`lastVisited` and `history`), whatever `settings.remember_folders` says, and has no setting to stop it. The program reads what is under that key before it shows such a window and puts it back straight after the window closes, so the folder isn't left there and what another Qt program stored is unchanged. A folder that version 1.0.0 or another Qt program already stored there is left as it is; delete the `lastVisited` value by hand to remove it. The command line shows no such window.
- **Short-lived files.** A `~stamper-….tmp` file while `config.json` or `state.json` is saved, and a `~stamper-check-….tmp` file to find out whether a folder can be written to; both are removed at once. While Profile settings is open, a made-up sample page in `%TEMP%\stamper-sample-…`, removed when it closes. The single-file program unpacks itself into `%TEMP%\_MEI…` each time it starts; that folder is removed on a normal close and can be left behind if the program is stopped by force. It holds the program, never a document.

Passwords are held in memory only, for as long as they're needed: the ones typed to open a file and the ones set for a saved copy. They are never written to any of the files above, and the command line refuses a password given as an argument.

### Encryption at rest

- **A protected original.** In the window, the user chooses what the stamped copy gets: "Keep the original's password and permissions", "No password" or "Set a password". When the original needed a password to open, nothing is chosen in advance. On the command line the copy keeps the original's password and permissions unless `--remove-encryption` is given. A copy saved with no password carries this note: "The copy isn't password-protected, unlike the original. To keep the same level of protection, encrypt this file or keep it only on encrypted drives and shares."
- **A combined file** has no password unless the user sets one ("No password" or "Set a password" in the window, `--out-password-env` or `--out-password-stdin` on the command line). When files that needed a password are combined without one, the note says how many and ends with the same sentence.
- **A password the user sets** encrypts the saved copy with AES-256, using PyMuPDF. "The password can have up to 40 characters: unaccented letters, digits, spaces and the symbols on a US keyboard." No claim is made here about any validation or certification of that encryption.
- **The combine working copy is never encrypted**, even when the originals were or a password is set for the combined file. The temporary `~stamper-….tmp` file beside a stamped copy has the same protection the finished copy will have.

Document Stamper is not a substitute for disk encryption. It expects the computers it runs on, and the drives and shares its copies are saved to, to have full-disk or volume encryption. That includes the user's profile, where the work folder, `state.json` and the notes of saves in progress are kept.

### The white box is not redaction

The white box behind the stamp covers what is under it and removes nothing. Covered text is still in the file and can be selected, copied, searched for or extracted, and the box can be removed with PDF editing software. It must not be used to redact. When the box covers text on a page that has a text layer, the program says which pages and that the box is not redaction. On a scanned page with no text layer it can't tell, and says nothing.

### What Windows keeps

Windows keeps its own records of recently used files and folders, which the program doesn't control and doesn't clear. That includes the folders its Open and Save As windows were used in, kept under `HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\ComDlg32`, even with `settings.remember_folders` off.
