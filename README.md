# Print Cost Calculator (PyQt6)

Estimates the printing cost of a PDF, Word (.docx) file or image from how much
of each ink color (Cyan, Magenta, Yellow, Black) the document actually uses,
so an all-yellow page costs less than an all-green page. White space costs
nothing: it converts to 0% on every channel, so it is never counted.

Current version: see `APP_VERSION` in `printcost/core/updater.py`.

## Features

- **Per-page color usage** — every page's Cyan / Magenta / Yellow / Black percentage and ink cost, listed individually.
- **Paper sizes and types** — Short, Long, Legal, A4/A5/A6, photo sizes (3R to 10R); plain, matte, glossy, semi-gloss, cardstock and sticker paper. Each paper type has its own price factor and ink factor.
- **Print quality** — draft / standard / high multipliers, and a black-mix setting for high quality.
- **Pricing rules** — six percentages (labor, maintenance, electricity, spoilage, printer, profit), minimum price per page (separate minimum for color pages), maximum price per page, a color threshold, discounts and page-count (bulk) discounts.
- **Page ranges** — price only the pages you select.
- **Full preview and editor** — zoomable preview of every page; in edit mode you can move, resize, crop, copy/paste and delete images (see below).
- **Poster mode** — slice one picture or PDF page into a grid of sheets.
- **Double-sided and booklet layouts** — back-to-back and booklet page ordering, blank covers, per-printer flip settings, and an alignment test page.
- **ID photo sheets** — pack photos of several sizes (1×1, 2×2, passport, stamp, wallet, custom) onto one sheet, with cut lines, optional signature strips, and a zoomable sheet preview.
- **Photo editor** — remove or replace the background, touch up the cutout edges with brushes, and fix the lighting, all on your PC.
- **Receipt and printing** — receipt image / PDF with your shop name (and permit/TIN if set), and direct printing.
- **System tray** — closing the window hides it to the tray; click the tray icon to bring it back.
- **Single instance** — opening a second copy (or a file) hands over to the running one. If the running copy is older, you are asked whether to replace it.
- **Start with Windows** — optional; starts minimized in the tray.
- **Automatic updates** — checks GitHub releases daily, verifies the download's checksum, and the installed Windows app can update itself.
- **Right-click "Calculate Print Cost"** — optional, see `docs/add_context_menu.reg`.

## Full preview editor

Click **Edit** in the full preview to edit images in the PDF. Images that are rotated or inline can't be edited and show no selection border.

**Selecting**

- Click an image to select it.
- Ctrl+click toggles an image, Shift+click adds one.
- Drag on empty space to draw a selection box; while you drag, everything that can be picked gets a soft dotted border.
- Selection works within one page. Copy, Crop and Position act on one image at a time.

**Shortcuts** (edit mode)

| Keys | Action |
|---|---|
| Arrows / Shift+arrows | Nudge the selection 1 px / 10 px (Ctrl+arrows also work). With nothing selected, the arrows scroll. |
| Ctrl+D | Duplicate |
| Ctrl+A | Select all images on the current page |
| Ctrl+C / Ctrl+X / Ctrl+V | Copy / cut / paste (one image at a time for copy and cut) |
| Delete / Backspace | Delete the selection |
| Ctrl+Z / Ctrl+Y (or Ctrl+Shift+Z) | Undo / redo |
| Ctrl+] / Ctrl+[ | Bring to front / send to back |
| Ctrl+O | Add images |
| Esc | Deselect (or cancel a crop) |

**Anywhere in the preview**

| Keys | Action |
|---|---|
| Ctrl+S | Export the PDF |
| Ctrl++ / Ctrl+- / Ctrl+0 | Zoom in / out / reset |
| Ctrl+Home / Ctrl+End | First / last page |

Moving, duplicating, deleting or reordering a group of images is one edit and one undo step.

## Project files

```
print_cost_calculator/
  main.py                  <- start here (python main.py)
  requirements.txt
  printcost/
    app.py                 <- window shell, system tray, splash, updates
    core/                  <- logic and services
      analyzer.py          <- CMYK conversion, PDF/DOCX rendering, cost math
      pricing.py           <- settings (settings.json) and discount tiers
      poster.py            <- poster slicing, preview image, poster pricing
      layout.py            <- double-sided and booklet page ordering
      idphoto.py           <- ID photo sheet packing
      photofx.py           <- photo editing: lighting, cutout edges, background compositing
      matting.py           <- background removal models (download, run, merge)
      pdf_editor.py        <- editing pages and images in a PDF
      updater.py           <- checks GitHub and updates the exe
      single_instance.py   <- one running copy, hand-over of files
      autostart.py         <- start with Windows
      worker.py            <- background threads for loading and analysis
    ui/                    <- everything the user sees
      calculate_tab.py     <- file picker, preview, options, results
      settings_tab.py      <- paper sizes, ink prices, percentages, discounts
      full_preview_dialog.py <- full preview and the editor
      poster_dialog.py     <- the Poster window
      layout_preview_dialog.py <- double-sided / booklet preview
      idphoto_dialog.py    <- the ID photo window and sheet preview
      idphoto_editor.py    <- arranging ID photos by hand
      photo_edit_dialog.py <- the photo editor (background, brushes, lighting)
      preview_widget.py    <- first-page thumbnail and spinner
      print_helper.py      <- printing
      receipt.py           <- receipt image / PDF
      notes.py, icons.py, style.py, help.py
  tools/                   <- build.py, build.bat
  docs/                    <- BUILD_EXE.md, add_context_menu.reg
```

Settings are saved in `settings.json` next to `main.py` when run from source, and in
`%APPDATA%\PrintCostCalculator` for the packaged exe.

## Setup

```bash
pip install -r requirements.txt
python main.py
```

Command line: `python main.py [file]` opens a file and calculates it; `--minimized` starts in the
tray; `--quit` closes the running copy.

For accurate `.docx` analysis (and preview), also install **LibreOffice** (free) so the app can render
the document exactly as it would print:

- Windows/Mac: https://www.libreoffice.org/download/
- Auto-detected (`soffice` / `libreoffice` on PATH). Without it, `.docx` falls back to a rough estimate
  (embedded images analyzed properly, body text assumed a flat ~5% black coverage, no preview thumbnail).

`.pdf` files are always analyzed and previewed precisely, no LibreOffice needed.

## Poster (tiled printing)

Press **Poster** next to *Browse files*. It uses the image / PDF already loaded, or asks you to pick one.

- **Grid**: presets (2×2, 3×2, 3×3, 4×3) or any columns × rows; *Suggest* picks the grid closest to the picture's shape.
  The window shows the final size, e.g. `4×3 pages of Short = 34 × 33 in`.
- **Sheets**: portrait or landscape. **Fit** keeps the proportions (blank bands); **Fill** crops the edges.
- **Printer margin**: the white border your printer can't print on. `0` = borderless (default). Each sheet's
  picture is cut to the printable area, so nothing is lost under the margin.
- **Overlap** (e.g. 0.25 in) gives a strip to trim and glue. **Crop / alignment marks** and **sheet labels**
  (A1, A2, B1 ...) are optional and print on the page.
- **Preview**: the whole poster with the grid and print-order numbers; click a tile to select it.
- **Pricing**: calculated per sheet (default), a flat poster fee, or a fixed price per sheet. A flat fee applies
  when all sheets are printed; reprinting one sheet uses the calculated price.
- The result is an ordinary multi-page PDF (row by row), so Calculate, the per-page table, Full Preview,
  Print and the receipt all work on it. The *Print* box under the file name picks one sheet to reprint.
- Poster sheets print at 100% (not scaled to the printer's page). Turn off "Fit to page" in the printer's
  Preferences.

## Double-sided and booklet printing

- **Back-to-back** orders the pages for two-sided printing; **Booklet** puts two pages on each landscape side in folding order.
- Optional blank covers keep the back of the front cover and the inside of the back cover empty.
- For printers without automatic duplex, the app prints the front pass, then the back pass, and needs to know how you
  reload the stack (flip over the long or short edge, and whether sheets come out face up or face down).
  Print the **alignment test** page once to check, and the choice is remembered per printer.
- Double-sided pricing can add an extra charge per sheet and/or a discount (Settings).

## ID photos

Pick a photo, add one or more sizes with a quantity for each, and the app packs them onto the chosen paper,
choosing portrait or landscape to fit the most. Gap, margin and cut lines are adjustable. Pricing is calculated
from the sheets used, or a flat fee. Photos that would print below about 150 DPI are flagged.

- **Signature strip**: add a signature picture and a printed name under each photo that has it turned on.
- **Sheet preview**: shown to scale. Scroll to zoom (up to 600%), drag to move, double-click to zoom in or out.
  Past 150% a sharp print-resolution version of the sheet is drawn in the background, so you can check the
  signature and name up close while you change sizes, gap or text.
- **Arrange by hand**: drag photos to your own layout. Changing sizes, paper or spacing clears it.
- **Sets and export**: save a group of sizes as a set, and export single photos or whole sheets as images.

### Photo editor

Press **Edit photo** in the ID photo window. Everything runs on your PC, and the photo never leaves it.

- **Background**: *Remove background* makes the cutout (*Redo cutout* once there is one, *Clear* goes back to the original).
  The background can be White, Blue, Red, a custom color, transparent, or an imported picture (scaled to cover, never stretched).
  *Feather*, *Shrink edge* and *Edge sharpness* shape the cutout edge.
- **High quality**: a slower, bigger model that finds hats, headwear, veils and other accessories. It shows
  its progress, and closing the progress window cancels it. *Accessory detail* sets how much of the accessory
  shapes is added everywhere.
- **Brushes**: Restore, Erase, Accessory (needs a High quality cutout), Lighten, Darken, Sharpen edge, Smooth edge,
  and two Clear brushes that remove earlier light or edge strokes. Brush size, Strength and Undo (Ctrl+Z) apply.
- **Lighting**: Exposure, Shadows, Highlights, Contrast, Warmth, Left / right, Even out, plus Auto, White point and Reset.
- Controls that can't be used yet are greyed out: the edge sliders and most brushes need a cutout, and Accessory detail
  and the Accessory brush need a High quality cutout.

The background remover needs the `onnxruntime` package (the app tells you if it is missing). On first use it asks to
download a small model (about 26 MB). High quality needs an extra model (about 200 MB), which can be downloaded
in the app or imported from a file. Models are saved in `%APPDATA%\PrintCostCalculator\models` and work offline afterwards.

## Turning it into a real double-clickable app

See `docs/BUILD_EXE.md`: packages this into a single `.exe` with PyInstaller. The installer is released on GitHub
as `PrintCostCalculator-Setup.exe`, which is what the updater downloads.

## Right-click "Calculate Print Cost" on any file

See `docs/add_context_menu.reg`. Requires building the `.exe` first and editing the file path inside the `.reg`
file before running it.

## How the cost math works

```
page_ink_cost = (%Cyan × Cyan price) + (%Magenta × Magenta price)
              + (%Yellow × Yellow price) + (%Black × Black price)
                (scaled by the paper type's ink factor and the print quality)

subtotal = ink cost (all pages) + paper cost (sheet price × paper type factor × sheets)

each of labor, maintenance, electricity, spoilage, printer and profit
  = subtotal × (its % / 100)

total = subtotal + labor + maintenance + electricity + spoilage + printer + profit
```

Then the minimum / maximum price per page, the color threshold (pages below it count as black and white),
and any discounts are applied. The exact rules are in `compute_cost` in `printcost/core/analyzer.py`.

## Known limitations

- Ink prices are per full page, flat regardless of paper size; only paper cost scales with the sheet you pick.
- `.docx` without LibreOffice uses a rough text-coverage estimate and shows no preview thumbnail.
- No Word/WPS add-in integration: standalone app, pick the file manually.
- Renders at 150 DPI for speed; large or many-page PDFs may take a few seconds, and the scanning animation covers this wait.
- The image editor only edits normal (unrotated, non-inline) images, and a selection stays on one page.
- Background removal runs on the CPU. High quality can take up to a minute, and cancelling it stops the window at once
  but the CPU stays busy until the model finishes its current step.