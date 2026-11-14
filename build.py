"""Builds PrintCostCalculator.exe.  Double-click build.bat (or run: python build.py)."""
import hashlib
import os
import re
import shutil
import subprocess
import sys

os.chdir(os.path.dirname(os.path.abspath(__file__)))
APP_NAME = "PrintCostCalculator"
EXE_NAME = APP_NAME + (".exe" if os.name == "nt" else "")


def read_version():
    with open("updater.py", encoding="utf-8") as f:
        match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', f.read())
    return match.group(1) if match else None


def make_splash_image(path="splash.png"):
    """The picture shown while the exe unpacks itself (before Python starts). Same look as the in-app splash."""
    from PIL import Image, ImageDraw, ImageFont
    w, h = 420, 230
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    colors = ["#00aeef", "#ec008c", "#fff200", "#231f20"]
    for i, c in enumerate(colors):
        draw.rectangle([i * w // 4, 0, (i + 1) * w // 4, 5], fill=c)
    draw.rectangle([0, 0, w - 1, h - 1], outline="#dde3ec")
    try:
        font = ImageFont.truetype("segoeuib.ttf", 26)
    except OSError:
        font = ImageFont.load_default()
    draw.text((w / 2, h / 2 - 10), "Print Cost Calculator", fill="#0f1b2d", font=font, anchor="mm")
    draw.text((w / 2, h - 28), "Starting...", fill="#64748b", anchor="mm")
    img.save(path)
    return path


def run(cmd):
    print("\n> " + " ".join(cmd))
    if subprocess.call(cmd) != 0:
        print("\nThat step failed (see the messages above), so the exe was NOT built.")
        sys.exit(1)


def main():
    version = read_version()
    if not version:
        print('Could not find APP_VERSION = "..." in updater.py.')
        sys.exit(1)

    print("=" * 50)
    print(f"  Building {APP_NAME} v{version}")
    print("=" * 50)
    print(f"This must match the GitHub release tag: v{version}")
    if "--yes" not in sys.argv:
        answer = input("Is that the right version? [Y/n] ").strip().lower()
        if answer in ("n", "no"):
            print("Change APP_VERSION in updater.py, then run this again.")
            return

    if os.name == "nt":  # a copy still running in the tray locks the exe and blocks the build
        print("\nClosing any running copy of the app...")
        subprocess.run(["taskkill", "/F", "/IM", EXE_NAME], capture_output=True)

    run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", "requirements.txt"])
    run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "pyinstaller"])

    for folder in ("build", "dist"):
        shutil.rmtree(folder, ignore_errors=True)  # start clean so an old build can't sneak in
    splash = make_splash_image()
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--windowed",
         "--splash", splash, "--name", APP_NAME, "main.py"])

    exe = os.path.join("dist", EXE_NAME)
    if not os.path.exists(exe):
        print(f"\nThe build finished but {exe} is missing. Check the messages above.")
        sys.exit(1)

    with open(exe, "rb") as f:
        sha256 = hashlib.sha256(f.read()).hexdigest()
    print("\n" + "=" * 50)
    print("  DONE")
    print("=" * 50)
    print(f"File:    {os.path.abspath(exe)}")
    print(f"Size:    {os.path.getsize(exe) / (1024 * 1024):.1f} MB")
    print(f"SHA-256: {sha256}")
    print("\nTo release it on GitHub:")
    print(f"  1. Releases -> Draft a new release -> tag v{version}")
    print(f"  2. Upload {EXE_NAME} (keep this exact file name)")
    print("  3. Publish release")

    if os.name == "nt":
        os.startfile(os.path.abspath("dist"))  # open the folder with the exe


if __name__ == "__main__":
    main()