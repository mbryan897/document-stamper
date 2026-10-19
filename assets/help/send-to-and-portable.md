# Send to menu and running from a USB drive

## The Send to menu

With Document Stamper in Explorer's *Send to* menu, you can right-click one or more PDFs and choose *Send to › Document Stamper*. One file opens its stamp dialog; several open the files screen.

To add it, open **Profile settings…**, choose **General**, and under **Send to menu** click **Add to the Send to menu**. It adds a shortcut for your Windows account only (in `%APPDATA%\Microsoft\Windows\SendTo`); nothing is installed and no administrator rights are needed.

The line above the buttons says where things stand:

- "Document Stamper isn't in the Send to menu." Click **Add to the Send to menu**.
- "Document Stamper is in the Send to menu." Nothing to do. **Remove** takes it out.
- "The Send to shortcut points at another copy of the app (…). Re-create it to use this copy." The shortcut was made by a copy of the app somewhere else, for example on a USB drive that now has a different letter. Click **Re-create**.
- "The Send to shortcut is there but can't be read." Click **Re-create**.

On some managed computers, policies stop shortcuts in the Send to menu from working. If the menu item doesn't appear or doesn't start the app, ask whoever manages your computers.

## Dragging files onto the app

Without the Send to menu, you can drop PDFs onto `DocumentStamper.exe` itself, or onto a shortcut to it on the desktop or taskbar, and the app starts with those files open.

## Running from a USB drive or a shared folder

Document Stamper is a single program file, `DocumentStamper.exe`, and needs no installing. Copy it anywhere and run it.

- Put `config.json` next to it, and the settings travel with it. See [Where the config lives](where-the-config-lives.md).
- It unpacks itself each time it starts, so starting takes a few seconds.
- What it remembers (the last profile and folder) is kept on each computer, in your user folder, not on the drive.
- If the drive gets a different letter on another computer, re-create the Send to shortcut there.

To give a team the same settings, put the app and a read-only `config.json` in a shared folder. Everyone uses the same profiles and rules, and nobody can change them by accident.

Some agencies only allow programs from approved places. If Windows or antivirus software won't let it run from a drive or share, ask whoever manages your computers.
