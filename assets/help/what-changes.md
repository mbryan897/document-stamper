# What changes in the stamped copy

This topic says exactly what Document Stamper does to a document, for anyone who has to account for a stamped copy. The original is never changed. The copy differs from it in the ways below, and in no others.

## The original

- The original is opened for reading only and never written to.
- Just before the copy is written, the app checks that the original's size, last-modified time and file identity are the same as when it was opened. If anything changed, nothing is saved.
- The app doesn't calculate or record hashes of either file, and keeps no log. If your procedures need a hash of the original or the copy, make it with your usual tool.

## The pages

- The stamp is added to each page as text, drawn on top of everything already on the page. Nothing already on the page is removed, moved or redrawn.
- The stamp is real text: it can be selected, copied and found with a viewer's search.
- The stamp font (Atkinson Hyperlegible Mono) is added to the file, so the stamp looks the same on any computer.
- Page sizes, page rotation and the visible area of each page are unchanged.

### The white box covers, it doesn't remove

The white box behind the stamp is a white rectangle drawn over that part of the page. It hides what is under it when the page is viewed or printed, but what is under it is still in the file: text there can still be selected, copied or found by searching, and the box can be taken off with PDF editing software.

**Never use the white box, or the stamp, to hide anything.** If something under the stamp must not be disclosed, it needs proper redaction, which this tool doesn't do. Choose a position for the stamp that doesn't cover content that matters.

## The file

- The copy is a complete new save of the document, not an edit added to the end of the original. That's why a digital signature on the original doesn't validate on the copy.
- If the original was edited after it was first saved, it holds earlier versions of itself; the copy keeps only the latest version, as a viewer shows it. (A note says when this applies.) Objects the document no longer uses may still be in the copy, as they are in the original.
- Pages, form fields and their values, comments, links, bookmarks, attached files, layers and any JavaScript or actions are kept unchanged. Field calculations and scripts are never run.
- *Password and permissions* are kept exactly as they are: the copy opens with the same password and has the same restrictions. The window never removes them. The command line removes them only with `--remove-encryption`, and then the copy says so in a note.
- The copy's bytes differ from the original's, so any hash of the two will differ.

## Document properties

Document properties are the information a PDF carries about itself, such as Title and Author, shown under File › Properties in most viewers.

- The original's properties are all kept.
- The properties set in the profile are added. The built-in profile adds `Identifier` (the stamped identifier), `Source` (the original's file name, without `.pdf`) and `StampedOn` (the stamping date), so the copy records which original it came from and when it was stamped. If the original already had a property of that name with a different value, it's replaced, and a note gives the old and new values.
- The modification date (`ModDate`) is set to the time of stamping.
- XMP metadata, a second, separate set of properties some PDFs carry, is left exactly as it was. It isn't updated, so it may still show the original's details; a note says when the original has XMP.

The saved screen lists the properties the copy carries, with their values: the standard ones present (Title, Author, Subject, Keywords, Created, Last changed, Made with, PDF made by), any custom ones, and whether there's XMP. Properties such as Author or Creator, and any custom ones, go wherever the copy goes. If they must be removed before the copy is disclosed, that's a separate step with another tool.

## Combined files

A combined file is a new document built from the originals' pages, then stamped as above.

- Each original's pages are copied in the order shown, with their content, rotation, page sizes, links, comments and form fields.
- Bookmarks are rebuilt: one for each original file, with that file's own bookmarks under it. The list of layers is rebuilt, and hidden layers stay hidden. Attached files are kept, renamed after the file each came from.
- Form fields with the same name in two files are renamed so they stay separate. Field scripts that refer to another field by name aren't changed, so calculated fields may not work as before; a note says when.
- Not kept: page labels (such as "i, ii, iii"), accessibility tags, the originals' document properties, their XMP metadata, their earlier versions and unused objects.
- The combined file has *no password or restrictions*, whatever the originals had.
- A `StamperSources` property records the original file names (names only, never folders) and the pages each one became, for example `report_part1.pdf|1-3`. Viewers show it with the custom properties.
- A property that uses `{original_name}`, such as `Source`, names only the first original file; `StamperSources` lists them all.

The temporary combined file is made in a private folder in your user profile and deleted when you save, cancel or close the window. See [Stamping the combined file](several-files.md#stamping-the-combined-file).
