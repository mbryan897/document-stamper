# Messages and what to do

Messages are written to say what happened and what to do next. This topic collects them in one place. Notes in amber are worth reading; none of them means the original was changed.

## Partial copies left behind

If Document Stamper was stopped while writing a copy (closed from Task Manager, a power cut, a crash), the unfinished copy stays in the folder you were saving to, named like `~stamper-….tmp`. Next time the app starts, the drop screen lists it: "A partial copy was left behind when Document Stamper stopped unexpectedly: … It's an unfinished stamped copy, not the original." It also names the file that was being stamped ("The stamped copy of … wasn't finished. Stamp it again."), or, for files being combined, the first of them ("a combined file starting with …"). Copies left by older versions don't carry that name.

- **Delete** (or **Delete all**) removes it. Each file is checked again first, and anything that is no longer the file that was left behind is kept.
- **Show in folder** opens its folder in Explorer (when only one is listed).
- **Keep** (or **Keep all**) leaves it where it is and stops asking.

Nothing is deleted unless you choose to. The app only lists copies it recorded itself; it never searches your folders.

## A different config from last time

"This is a different config from last time. Now: … Before: … Check the profile and fields before you stamp." The app found a different `config.json` from the one it used last time, for example because a copy of the app now has its own config next to it. The rules, lists and stamp may differ. See [Where the config lives](where-the-config-lives.md).

## The config can't be used

"config.json can't be used:" followed by a list of problems. The file is broken or has a setting that doesn't make sense, such as a stamp using a field the profile doesn't have. Each problem names where it is in the file. Fix it in a text editor, or use **Open another config…** to load a good one for now. A config larger than 1 MB is refused without being read, because a real one is far smaller.

## Notes before saving and on the saved screen

These appear under "Before you save" in the stamp dialog, and again as "Note:" lines on the saved screen. None of them stops you saving.

| Note | What it means | What to do |
| --- | --- | --- |
| "On page 3 a form field covers part of the stamp and is drawn on top of it." | A form field or comment on the page sits where the stamp goes. Viewers draw it on top, so it may hide part of the stamp. | Choose another position under **Placement**. |
| "Page 2, stamp 1: font reduced from 9 pt to 7.5 pt to fit." | The page is too small for the stamp at its usual size. | Check that page in the preview. |
| "… was damaged and was repaired when opened." | The file had errors that were fixed in reading it. | Look through the stamped copy carefully. |
| "Pages … are under 1 inch on a side." | Very small pages. | Check the stamp there carefully. |
| "Pages … are over 100 inches on a side, so the preview is drawn at a lower resolution." | Very large pages, such as plans. | The stamp itself is drawn at full quality. |
| "This file has … pages" or "This file is … MB; checking and saving take longer." | A very large file. | Give it time. |
| "This file contains JavaScript and 2 attached files. The stamped copy keeps them unchanged." | The PDF carries things that can act by themselves in some viewers: scripts, actions that open other files or programs, forms that send data, attached files, or embedded media. | Nothing; this app never runs them. Treat the copy as you would the original. |
| "This file was edited after it was first saved (3 saved versions). The stamped copy keeps only the latest version, as a viewer shows it." | The PDF holds earlier versions of itself. The copy keeps what a viewer shows; the earlier versions stay only in the original. | Keep the original if earlier versions may matter. |
| "The original has XMP metadata, which wasn't updated; only the document properties were." | The PDF has a second, separate set of properties that this app leaves alone. | See [Document properties](what-changes.md#document-properties). |
| "Document property … was "…" in the original; the copy has "…"." | The profile writes a property the original already had, with a different value. | Check it's right. |
| "Document properties in the copy: …" | What the copy's document properties are. | See [Document properties](what-changes.md#document-properties). |
| "The original is digitally signed; the stamped copy's signature won't validate." | You confirmed a signed file. | Keep the signed original. |
| "The original's owner restrictions don't allow changes; it was stamped after confirmation." | You confirmed a restricted file. | |

### After combining

| Note | What it means |
| --- | --- |
| "The combined file is saved without a password or restrictions. Protected files in it: …" | The combined file has no protection, even though some originals did. Store it accordingly. |
| "Your private work folder couldn't be used (…), so the combined working copy is in … until you close this window." | The temporary combined file had to go next to your files instead of your private work folder; it's deleted when you save, cancel or close. If any originals had a password, that working copy has none until then. |
| "Bookmarks: one for each of the N files, with each file's own bookmarks under it." | How the combined file's bookmarks are arranged. |
| "Form fields in … were renamed so they stay separate: …" | Two files had form fields with the same name. |
| "Form-field scripts in … may still refer to the old field names; check calculated fields in the combined file." | Calculated fields may show wrong values in the combined file. |
| "Accessibility tags in … weren't kept in the combined file." | The combined file is less usable with a screen reader. |
| "Page labels from the originals weren't kept; pages are numbered across the whole file." | Labels like "i, ii, iii" are gone. |
| "N attached files from the originals were kept, named after the file each came from." | |
| "The file names suggest different values for …" | Shown under a field when the files' names disagree. Check the files belong together, then type the value. |

## Problems that stop saving

| Message | What to do |
| --- | --- |
| "The stamp font has no character for '…' (U+…) in …. Remove or replace that character." | The stamp font can't draw that character. It's shown under the field it's in. |
| "The stamp text contains a line break or tab …; stamps are a single line." | Remove the line break or tab from the value. |
| "Page 4 (… in), stamp 1 "…": needs … in along the top edge at 6 pt; … in available. …" | The stamp doesn't fit on that page even at 6 pt. Shorten it, run it along the other edge, or choose another position. |
| "Page 2 (…): stamps 1 and 2 overlap. Move one to another position." | Two stamps would print on top of each other. |
| "The original, …, changed after you opened it. Open it again so the copy matches what you checked." | Someone or something changed the file while it was open. Open it again and check it. |
| "Stamping was cancelled. Nothing was saved." | You cancelled. |
| "Couldn't …" or "Something went wrong (…)" | The message names the problem, for example a folder you can't write to. Nothing was saved. |

## The window stops responding

A very unusual or deliberately malformed PDF can take a long time to read or draw, and the window stops responding while it does. Nothing is being written at that point, so nothing is lost. If it doesn't recover, end Document Stamper in Task Manager and start it again. Tell whoever looks after the tool what kind of file it was (not the file itself).
