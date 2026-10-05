# Tools

## Notes tracker

A small page for tracking features and bugs to work on. Each note has a title,
a description, a type (feature or bug), a priority (high, medium or low), a
date, and a done flag.

**Start it** from the repo root:

```bash
node tools/notes-server.mjs
```

Or use **Notes (tools :4321)** in VS Code's Run and Debug panel. Then open
**http://localhost:4321**. It needs Node.js only; there are no packages to
install.

**Where notes are saved:** every change is written straight to
**`tools/notes.json`**, so the notes survive clearing the browser, and can be
backed up or committed with the code.
- The header shows when the last save happened.
- If the server stops, the page shows a warning, and keeps your changes in the
  browser until the next save after it's back.
- The file is written to a temporary file first and then swapped in, so a
  crash mid-save can't corrupt it.

**Opening `notes.html` directly** (double-clicking it) still works, but notes
are then kept only in that browser, and a banner says so. The first time you
open it through the server, any notes already in the browser are copied into
`notes.json`.

**Export / Import:** Export downloads a copy of the notes as JSON. Import
merges a copy back in, replacing notes with the same ID.

The server only listens on `localhost`, so nothing outside this machine can
read or change the notes. Set `NOTES_PORT` to use a port other than 4321.
