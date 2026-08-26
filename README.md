# Print Cost Calculator (PyQt6 version)

Estimates the printing cost of a PDF or Word (.docx) file based on how much
of each ink color (Cyan, Magenta, Yellow, Black) the document actually uses
— so an all-yellow page costs less than an all-green page, since green needs
more ink channels mixed together. White space costs nothing — it converts
to 0% on every channel automatically, it's never counted and then filtered
out after the fact.

## What's in this version

- **PyQt6 interface** — real native widgets and styling (QSS), not Tkinter.
- **First-page preview only** — shows a thumbnail of just the first page
  when you pick a file (PDF always; .docx too if LibreOffice is installed).
- **One-way scanning animation** — a line sweeps downward across the preview
  while analyzing, and stops the instant the result is ready — for a quick
  file it may only get partway (e.g. ~1/4 down) before snapping to done.
- **Five separate cost percentages**: Labor, Maintenance, Electricity,
  Spoilage, Profit — each applied independently to the ink+paper subtotal
  and shown as its own line in the results.
- **Per-page color usage table** — every page's Cyan/Magenta/Yellow/Black
  percentage and ink cost, listed individually, with white already excluded
  by the math (not filtered afterward).
- **Native system tray** — closing the window minimizes it to the tray
  instead of quitting; click the tray icon to bring it back instantly.
- **Right-click "Calculate Print Cost"** context menu integration (optional,
  see `add_context_menu.reg`).

## Project files

```
print_cost_calculator/
  main.py            <- app entry point, window shell, system tray
  calculate_tab.py    <- file picker, preview, options, results table
  settings_tab.py      <- paper sizes, ink prices, default percentages
  preview_widget.py     <- first-page thumbnail + scanning animation
  worker.py               <- background thread that runs the analysis
  style.py                  <- shared QSS stylesheet / color palette
  analyzer.py                 <- CMYK conversion, PDF/DOCX rendering, cost math
  pricing.py                    <- settings persistence (settings.json)
```

## Setup

```bash
pip install -r requirements.txt
python main.py
```

For accurate `.docx` analysis (and preview), also install **LibreOffice**
(free) so the app can render the document exactly as it would print:
- Windows/Mac: https://www.libreoffice.org/download/
- Auto-detected (`soffice`/`libreoffice` on PATH). Without it, `.docx`
  falls back to a rough estimate (embedded images analyzed properly, body
  text assumed a flat ~5% black coverage, no preview thumbnail).

`.pdf` files are always analyzed and previewed precisely — no LibreOffice
needed.

## Turning it into a real double-clickable app

See `BUILD_EXE.md` — packages this into a single `.exe` with PyInstaller.

## Right-click "Calculate Print Cost" on any file

See `add_context_menu.reg` — requires building the `.exe` first and editing
the file path inside the `.reg` file before running it.

## How the cost math works

```
page_ink_cost = (%Cyan × Cyan price) + (%Magenta × Magenta price)
              + (%Yellow × Yellow price) + (%Black × Black price)

subtotal = ink cost (all pages) + paper cost (price/sheet × pages)

labor_amount        = subtotal × (labor% / 100)
maintenance_amount  = subtotal × (maintenance% / 100)
electricity_amount  = subtotal × (electricity% / 100)
spoilage_amount     = subtotal × (spoilage% / 100)
profit_amount       = subtotal × (profit% / 100)

total = subtotal + labor_amount + maintenance_amount
                  + electricity_amount + spoilage_amount + profit_amount
```

## Known limitations (still the "simple version")

- Ink prices are per full page, flat regardless of paper size — only paper
  cost scales with the sheet you pick.
- `.docx` without LibreOffice uses a rough text-coverage estimate and shows
  no preview thumbnail.
- No Word/WPS add-in integration — standalone app, pick the file manually.
- Renders at 150 DPI for speed; large/many-page PDFs may take a few seconds
  — the scanning animation covers this wait.
