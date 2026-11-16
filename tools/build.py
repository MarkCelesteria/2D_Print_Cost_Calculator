"""Builds the installer. Double-click tools\\build.bat (or run: python tools\\build.py)."""
import hashlib
import os
import re
import shutil
import subprocess
import sys

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP_NAME = "PrintCostCalculator"
EXE_NAME = APP_NAME + (".exe" if os.name == "nt" else "")
SETUP_NAME = APP_NAME + "-Setup.exe"
ICON_PATH = os.path.join("assets", "app.ico")
VERSION_FILE = os.path.join("assets", "version_info.txt")
ISS_PATH = os.path.join("tools", "installer.iss")
MODEL_PATH = os.path.join("assets", "modnet.onnx")
MODEL_URL = "https://huggingface.co/Xenova/modnet/resolve/main/onnx/model.onnx"


def read_version():
    with open(os.path.join("printcost", "core", "updater.py"), encoding="utf-8") as f:
        match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', f.read())
    return match.group(1) if match else None


def make_icon(path=ICON_PATH):
    from PIL import Image, ImageDraw
    size = 1024
    k = size / 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([2 * k, 2 * k, 62 * k, 62 * k], radius=14 * k, fill="#0f1b2d")
    for x, y, color in ((12, 12, "#00aeef"), (34, 12, "#ec008c"), (12, 34, "#fff200"), (34, 34, "#ffffff")):
        draw.ellipse([x * k, y * k, (x + 18) * k, (y + 18) * k], fill=color)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.resize((256, 256), Image.LANCZOS).save(
        path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def make_version_file(version, path=VERSION_FILE):
    nums = ", ".join((version.split(".") + ["0"] * 4)[:4])
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({nums}),
    prodvers=({nums}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable("040904B0", [
        StringStruct("CompanyName", "MarkCelesteria"),
        StringStruct("FileDescription", "Print Cost Calculator"),
        StringStruct("FileVersion", "{version}"),
        StringStruct("InternalName", "{APP_NAME}"),
        StringStruct("OriginalFilename", "{EXE_NAME}"),
        StringStruct("ProductName", "Print Cost Calculator"),
        StringStruct("ProductVersion", "{version}")
      ])
    ]),
    VarFileInfo([VarStruct("Translation", [1033, 1200])])
  ]
)
"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def ensure_model():
    if os.path.exists(MODEL_PATH) and os.path.getsize(MODEL_PATH) > 20 * 1024 * 1024:
        return
    import urllib.request
    print("\nDownloading the background remover model (one time)...")
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    urllib.request.urlretrieve(MODEL_URL, MODEL_PATH + ".part")
    os.replace(MODEL_PATH + ".part", MODEL_PATH)


def find_iscc():
    found = shutil.which("iscc")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base:
            for ver in ("7", "6"):
                path = os.path.join(base, f"Inno Setup {ver}", "ISCC.exe")
                if os.path.exists(path):
                    return path
    return None


def run(cmd):
    print("\n> " + " ".join(cmd))
    if subprocess.call(cmd) != 0:
        print("\nThat step failed (see the messages above), so the installer was NOT built.")
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

    iscc = find_iscc()
    if not iscc:
        print("Inno Setup 6 was not found. Install it from https://jrsoftware.org/isdl.php and run this again.")
        sys.exit(1)

    if os.name == "nt":
        print("\nClosing any running copy of the app...")
        subprocess.run(["taskkill", "/F", "/IM", EXE_NAME], capture_output=True)

    run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "-r", "requirements.txt"])
    run([sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "pyinstaller"])

    for folder in ("build", "dist"):
        shutil.rmtree(folder, ignore_errors=True)
    make_icon()
    make_version_file(version)
    ensure_model()
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed",
         "--icon", ICON_PATH, "--version-file", VERSION_FILE, "--name", APP_NAME,
         "--add-data", f"{MODEL_PATH}{os.pathsep}models", "--collect-binaries", "onnxruntime", "main.py"])

    exe = os.path.join("dist", APP_NAME, EXE_NAME)
    if not os.path.exists(exe):
        print(f"\nThe build finished but {exe} is missing. Check the messages above.")
        sys.exit(1)

    run([iscc, f"/DAppVersion={version}", ISS_PATH])

    setup = os.path.join("dist", SETUP_NAME)
    if not os.path.exists(setup):
        print(f"\nThe installer step finished but {setup} is missing. Check the messages above.")
        sys.exit(1)

    with open(setup, "rb") as f:
        sha256 = hashlib.sha256(f.read()).hexdigest()
    print("\n" + "=" * 50)
    print("  DONE")
    print("=" * 50)
    print(f"File:    {os.path.abspath(setup)}")
    print(f"Size:    {os.path.getsize(setup) / (1024 * 1024):.1f} MB")
    print(f"SHA-256: {sha256}")
    print("\nTo release it on GitHub:")
    print(f"  1. Releases -> Draft a new release -> tag v{version}")
    print(f"  2. Upload {SETUP_NAME} (keep this exact file name)")
    print("  3. Publish release")

    if os.name == "nt":
        os.startfile(os.path.abspath("dist"))


if __name__ == "__main__":
    main()