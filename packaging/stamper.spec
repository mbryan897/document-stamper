# PyInstaller spec: one onedir folder with two programs (D-087).
#
#   DocumentStamper.exe  windowed; the drop screen, drag onto it, Send to
#   stamper-cli.exe      console; the command line for scripts
#
# Both share _internal\ (Python, Qt, PyMuPDF, assets\fonts, config\default.json).
# config.json is looked for next to the exes, so the folder is portable.
# Build: .\packaging\build.ps1  (or: pyinstaller --noconfirm packaging\stamper.spec)
# Test build only: no signing, icon or antivirus work yet (Phase 5).

from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 - SPECPATH is set by PyInstaller

datas = [
    (str(ROOT / "assets" / "fonts" / "*.ttf"), "assets/fonts"),
    (str(ROOT / "assets" / "fonts" / "*.txt"), "assets/fonts"),
    (str(ROOT / "config" / "default.json"), "config"),
    (str(ROOT / "LICENSE"), "."),
    *copy_metadata("document-stamper"),  # so --version reports the real version
]
excludes = ["tkinter", "pytest", "pytestqt", "PyInstaller", "setuptools", "pip"]


def analysis(script):
    return Analysis(  # noqa: F821 - provided by PyInstaller
        [str(ROOT / "packaging" / script)],
        pathex=[str(ROOT)],
        datas=datas,
        excludes=excludes,
        noarchive=False,
    )


window = analysis("launch_window.py")
cli = analysis("launch_cli.py")

window_exe = EXE(  # noqa: F821
    PYZ(window.pure),  # noqa: F821
    window.scripts,
    [],
    exclude_binaries=True,
    name="DocumentStamper",
    console=False,
    upx=False,
)
cli_exe = EXE(  # noqa: F821
    PYZ(cli.pure),  # noqa: F821
    cli.scripts,
    [],
    exclude_binaries=True,
    name="stamper-cli",
    console=True,
    upx=False,
)
COLLECT(  # noqa: F821
    window_exe,
    window.binaries,
    window.datas,
    cli_exe,
    cli.binaries,
    cli.datas,
    upx=False,
    name="DocumentStamper",
)
