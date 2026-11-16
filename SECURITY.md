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
