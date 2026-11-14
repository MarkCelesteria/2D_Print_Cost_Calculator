"""One-time tidy-up: moves the flat project into folders and rewrites the imports.

Put this file in the project folder (next to main.py), then run:   python restructure.py
It makes a zip backup first and refuses to run twice. Delete it when you are done.

    main.py              <- tiny entry point (python main.py still works)
    printcost/app.py     <- the window shell (the old main.py)
    printcost/core/      <- logic and services: analyzer, pricing, poster, pdf_editor, updater, autostart, worker
    printcost/ui/        <- windows, dialogs, widgets, styling, printing, receipt
    tools/               <- build.py, build.bat
    docs/                <- BUILD_EXE.md, add_context_menu.reg
"""
import os
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)

CORE = ["analyzer", "pricing", "poster", "layout", "pdf_editor", "updater", "autostart", "worker"]
UI = ["calculate_tab", "settings_tab", "full_preview_dialog", "poster_dialog", "preview_widget",
      "notes", "icons", "style", "print_helper", "receipt"]
PKG = {**{m: "printcost.core" for m in CORE}, **{m: "printcost.ui" for m in UI}}
TOOLS = ["build.py", "build.bat"]
DOCS = ["BUILD_EXE.md", "add_context_menu.reg"]

GITIGNORE = ["__pycache__/", "*.pyc", "build/", "dist/", "*.spec", "settings.json", "splash.png",
             "_backup_before_restructure.zip", ".venv/", "venv/"]

TREE = """```
print_cost_calculator/
  main.py                  <- start here (python main.py)
  requirements.txt
  printcost/
    app.py                 <- window shell, system tray, splash
    core/                  <- logic and services
      analyzer.py          <- CMYK conversion, PDF/DOCX rendering, cost math
      pricing.py           <- settings persistence (settings.json)
      poster.py            <- poster slicing, preview image, poster pricing
      pdf_editor.py        <- editing pages and images in a PDF
      updater.py           <- checks GitHub and updates the exe
      autostart.py         <- start with Windows
      worker.py            <- background threads for loading and analysis
    ui/                    <- everything the user sees
      calculate_tab.py     <- file picker, preview, options, results
      settings_tab.py      <- paper sizes, ink prices, percentages
      full_preview_dialog.py <- full preview and the editor
      poster_dialog.py     <- the Poster window
      preview_widget.py    <- first-page thumbnail and spinner
      print_helper.py      <- printing
      receipt.py           <- receipt image / PDF
      notes.py, icons.py, style.py
  tools/                   <- build.py, build.bat
  docs/                    <- BUILD_EXE.md, add_context_menu.reg
```"""


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write(path, text):
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def move(src, dst):
    if os.path.isdir(".git") and shutil.which("git"):
        if subprocess.run(["git", "mv", src, dst], capture_output=True).returncode == 0:
            return
    shutil.move(src, dst)


def rewrite_imports(text):
    """'import pricing' -> 'from printcost.core import pricing'; 'from style import X' -> 'from printcost.ui.style import X'."""
    names = "|".join(PKG)

    def plain(m):
        indent, mod, alias, tail = m.group(1), m.group(2), m.group(3) or "", m.group(4) or ""
        return f"{indent}from {PKG[mod]} import {mod}{alias}{tail}"

    text = re.sub(rf"^([ \t]*)import ({names})([ \t]+as[ \t]+\w+)?([ \t]*#[^\r\n]*)?[ \t]*(?=\r?$)", plain, text, flags=re.M)
    text = re.sub(rf"^([ \t]*)from ({names}) import",
                  lambda m: f"{m.group(1)}from {PKG[m.group(2)]}.{m.group(2)} import", text, flags=re.M)
    return text


def patch(path, pairs):
    """Exact-text replacements; reports instead of failing if something was already changed."""
    text = read(path)
    crlf = "\r\n" in text
    text = text.replace("\r\n", "\n")
    for old, new in pairs:
        if text.count(old) != 1:
            print(f"  ! could not patch {path}: {old.strip()[:60]!r} (fix by hand)")
            continue
        text = text.replace(old, new)
    write(path, text.replace("\n", "\r\n") if crlf else text)


def main():
    if os.path.isdir("printcost"):
        sys.exit("A 'printcost' folder already exists. Looks like this was already done, so nothing was changed.")
    for need in ("main.py", "updater.py", "pricing.py", "calculate_tab.py"):
        if not os.path.exists(need):
            sys.exit(f"Run this from the project folder: {need} was not found here.")

    # 1. backup
    backup = "_backup_before_restructure.zip"
    with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as z:
        for name in os.listdir("."):
            if os.path.isfile(name) and name != backup:
                z.write(name)
    print(f"Backup saved: {backup}")

    # 2. folders
    for folder in ("printcost", "printcost/core", "printcost/ui", "tools", "docs"):
        os.makedirs(folder, exist_ok=True)
    for pkg in ("printcost", "printcost/core", "printcost/ui"):
        write(os.path.join(pkg, "__init__.py"), "")

    # 3. move files
    moved = []
    for mod in CORE:
        if os.path.exists(f"{mod}.py"):
            move(f"{mod}.py", f"printcost/core/{mod}.py")
            moved.append(f"printcost/core/{mod}.py")
    for mod in UI:
        if os.path.exists(f"{mod}.py"):
            move(f"{mod}.py", f"printcost/ui/{mod}.py")
            moved.append(f"printcost/ui/{mod}.py")
    move("main.py", "printcost/app.py")
    moved.append("printcost/app.py")
    for name in TOOLS:
        if os.path.exists(name):
            move(name, f"tools/{name}")
    for name in DOCS:
        if os.path.exists(name):
            move(name, f"docs/{name}")

    # 4. imports
    for path in moved:
        write(path, rewrite_imports(read(path)))

    # 5. special cases
    patch("printcost/core/pricing.py", [(
        "    return os.path.dirname(os.path.abspath(__file__))",
        "    here = os.path.dirname(os.path.abspath(__file__))   # printcost/core -> the project folder\n"
        "    return os.path.dirname(os.path.dirname(here))        # settings.json stays next to main.py",
    )])

    # app.py: the startup block becomes main(); its imports stay module-wide, as before
    app = read("printcost/app.py")
    crlf = "\r\n" in app
    app = app.replace("\r\n", "\n")
    marker = 'if __name__ == "__main__":\n'
    if app.count(marker) == 1:
        head, body = app.split(marker)
        bound = []
        for line in body.splitlines():
            m = re.match(r"\s+from [\w.]+ import (.+?)\s*$", line)
            if m:
                bound += [part.split(" as ")[-1].strip() for part in m.group(1).split(",")]
        decl = f"    global {', '.join(bound)}\n" if bound else ""
        app = head + "def main():\n" + decl + body.rstrip("\n") + '\n\n\nif __name__ == "__main__":\n    main()\n'
    else:
        print("  ! could not find the startup block in app.py (fix by hand)")
    write("printcost/app.py", app.replace("\n", "\r\n") if crlf else app)

    write("main.py", '"""Print Cost Calculator: start here (python main.py). The app lives in the printcost/ folder."""\n'
                     "from printcost.app import main\n\n\nif __name__ == \"__main__\":\n    main()\n")

    if os.path.exists("tools/build.py"):
        patch("tools/build.py", [
            ("os.chdir(os.path.dirname(os.path.abspath(__file__)))",
             "os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # the project folder"),
            ('open("updater.py"', 'open(os.path.join("printcost", "core", "updater.py")'),
        ])
    if os.path.exists("tools/build.bat"):
        write("tools/build.bat", '@echo off\r\ncd /d "%~dp0.."\r\npython tools\\build.py\r\npause\r\n')

    # 6. .gitignore and README
    have = read(".gitignore").splitlines() if os.path.exists(".gitignore") else []
    add = [line for line in GITIGNORE if line not in have]
    if add:
        write(".gitignore", "\n".join(have + add) + "\n")
    if os.path.exists("README.md"):
        readme = read("README.md")
        new = re.sub(r"(## Project files\s*\n+)```.*?```", lambda m: m.group(1) + TREE, readme, count=1, flags=re.S)
        write("README.md", new)

    # 7. check
    bad = []
    for path in moved + ["main.py"] + (["tools/build.py"] if os.path.exists("tools/build.py") else []):
        try:
            compile(read(path), path, "exec")
        except SyntaxError as e:
            bad.append(f"{path}: {e}")
    leftovers = [f for f in os.listdir(".") if f.endswith(".py") and f not in ("main.py", "restructure.py")]
    print("\nDone.")
    if leftovers:
        print("Left in the main folder (not part of the plan): " + ", ".join(leftovers))
        if "page_setup.py" in leftovers:
            print("  page_setup.py is no longer used, so you can delete it.")
    for line in bad:
        print("  SYNTAX PROBLEM " + line)
    print("\nNext: run `python main.py`, then `git status`, and commit the move on its own.")


if __name__ == "__main__":
    main()