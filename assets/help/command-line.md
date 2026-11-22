# Command line

Everything the window does with a profile can also be done from a command line or a script: the same profiles, rules, checks and refusals. It never asks a yes/no question, so a script can't hang waiting for an answer.

## Which program to run

- `stamper-cli.exe`, in the folder version of the app (`DocumentStamper-folder`), is for command lines and scripts. Output appears in the console, and PowerShell waits for it to finish.
- `DocumentStamper.exe` opens the window when it's given only file names. Given any option (anything starting with `-`), it runs the command line too, but as a windowed program it shows no output and PowerShell doesn't wait for it. Use it from a script only with `Start-Process -Wait -PassThru` and read the exit code. `--` before the file names forces the command line.
- From the source code: `python -m stamper` (the window, or the command line when there are options) and `stamper-cli`.

The examples below use `stamper-cli`.

## Stamping one file

```powershell
stamper-cli "D:\Records\Incoming\return.pdf" --set identifier=99-2026-999901 --set doc_type=Report
```

- `--set NAME=VALUE` gives a field's value. `NAME` is the field's token (`identifier`, `doc_type` …), not its label; an unknown name is refused with the list of fields. Repeat it for each field.
- Quote the whole argument when the value has spaces: `--set "doc_title=Second supplement"`.
- Values are cleaned up and checked exactly as in the window. Every problem is listed at once, and nothing is saved.
- A field left out is filled in from the file name when the window would do so; a line says what was taken.
- The copy is saved next to the original with the suggested name, and its path is printed: `Saved: …`.

Placement and the page-number position come from the profile; the command line doesn't change them.

## Several files

```powershell
stamper-cli --combine part1.pdf part2.pdf part3.pdf --out "D:\Records\Stamped\return_combined.pdf" --set identifier=99-2026-999901 --set doc_type=Return
stamper-cli --batch a.pdf b.pdf c.pdf --out-dir "D:\Records\Stamped" --set identifier=99-2026-999901 --set doc_type=Report
```

- `--combine` makes one stamped file, pages in the order the files are given. Without `--out` it's saved next to the first file, with the suggested name.
- `--batch` stamps each file separately. Copies go next to their originals, or into `--out-dir`. `--out` can't be used with `--batch`.
- Several files without either is refused, naming both.
- A value is filled in from the file names only when every name that has one agrees; a note says when they disagree.
- Every file is checked before anything is saved. A batch prints one line per file and saves the ones it can.

## Options

| Option | What it does |
| --- | --- |
| `--set NAME=VALUE` | A field's value. Repeat for each field. |
| `--profile NAME` | The profile to use. Default: the config's default profile. |
| `--config PATH` | The config file to use. Default: next to the app, then `%APPDATA%\DocumentStamper`, then the built-in one. |
| `--out PATH` | The full path of the stamped copy (one file, or `--combine`). |
| `--out-dir FOLDER` | The folder for the copy or copies, with the suggested name. |
| `--name-field FIELD` | A field to put in the suggested name, in order; repeat for each. `date` adds today's date. Default: the profile's choice. |
| `--combine` | Combine several files into one stamped file. |
| `--batch` | Stamp each of several files separately. |
| `--password-env VAR` | Read the PDF's password from an environment variable. With several files, it's tried for every file. |
| `--password-stdin` | Read the password from the first line of standard input. |
| `--password-env-for FILE VAR` | With several files: one file's password, from an environment variable. Repeat for each file. |
| `--passwords-stdin` | With several files: one password per line of standard input, in the order the files are given; an empty line for a file with none. |
| `--out-password-env VAR` | With `--combine`: protect the combined file with the password in this environment variable. |
| `--out-password-stdin` | With `--combine`: read the combined file's password from the first line of standard input. It can't be used with `--password-stdin` or `--passwords-stdin`. |
| `--no-box` | Don't draw the white box behind the stamp. |
| `--accept-signature-loss` | Go ahead with digitally signed files; the copy's signature won't validate. |
| `--accept-restrictions` | Go ahead with files whose owner restrictions don't allow changes. |
| `--remove-encryption` | Save the copy without the original's password and permissions. The window never does this. |
| `--overwrite` | Replace a stamped copy that already exists. It can never replace an original. |
| `--dry-run` | Check everything and print the name the copy would get, and which confirmation flags are missing, without saving. |
| `--quiet` | Print only errors. |
| `--version` | Print the version. |
| `-h`, `--help` | Print the options and exit codes. |

## Passwords

Passwords are never taken on the command line itself, because PowerShell history, the process list and Windows' event logs can all record command lines. (The old `--password-for` option is refused with a message saying what to use instead.)

- Run in a console with no password given, the command asks for it, without showing what you type.
- In a script, put it in an environment variable for that session only:

```powershell
$env:STAMPER_PW = Read-Host -MaskInput "Password"   # PowerShell 7
stamper-cli "locked_return.pdf" --set identifier=99-2026-999901 --set doc_type=Return --password-env STAMPER_PW
Remove-Item Env:STAMPER_PW
```

A file that needs a password and didn't get one stops with exit code 6 and names the options.

### A password for the combined file

A combined file is saved without a password unless you give one with `--out-password-env VAR` or `--out-password-stdin`. It is then saved with AES-256 encryption: the password is needed to open it, and nothing else is restricted. It can have up to 40 characters: unaccented letters, digits, spaces and the symbols on a US keyboard; anything else stops with exit code 6 before anything is combined. The command never asks whether you want one. When some of the files needed a password and none was given for the combined file, a note says so.

## Signed and restricted files

A signed or restricted file stops with exit code 7 and names the flag that confirms it (`--accept-signature-loss` or `--accept-restrictions`). With several files, the message names every file concerned.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Saved (for a batch: every file saved). With `--dry-run`: everything checked. |
| 1 | An unexpected error. The message says what; nothing was saved over the original. |
| 2 | The command was written wrongly (an unknown option, a field that isn't in the profile, several files without `--combine` or `--batch`). |
| 3 | The config file can't be used. |
| 4 | Field values are missing or don't match their rules. |
| 5 | A file can't be stamped (not a PDF, missing, damaged, an XFA form), or the original changed after it was opened. |
| 6 | A password is missing or wrong. |
| 7 | A confirmation flag is needed. |
| 8 | The output was refused (it already exists, it's an original, or the folder doesn't exist). |
| 9 | The stamp doesn't fit on a page, or a character can't be printed in the stamp font. |
| 10 | A batch saved some files but not all. |
| 130 | Stopped with Ctrl+C. Nothing was saved. |

When every file in a batch fails for the same reason, the code is that reason's; for different reasons, 1.

In PowerShell, read the code from `$LASTEXITCODE`:

```powershell
Get-ChildItem "D:\Records\Incoming\*.pdf" | ForEach-Object {
    stamper-cli $_.FullName --set identifier=99-2026-999901 --set doc_type=Report --out-dir "D:\Records\Stamped" --quiet
    if ($LASTEXITCODE -ne 0) { Write-Warning "$($_.Name): exit code $LASTEXITCODE" }
}
```

## Notes the command line prints

The same notes as the window's saved screen are printed as `Note:` lines, including the document properties in the copy. Partial copies left by a stopped run are mentioned at the start, but only the window offers to delete them.
