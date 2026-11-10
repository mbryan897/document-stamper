# PyInstaller spec: the DocumentStamper-folder build (D-087), kept as a fallback (D-092).
#
# Output: dist\DocumentStamper-folder\. Its DocumentStamper.exe is only a small
# launcher for _internal\ and does NOT run on its own; for a single file that
# runs by itself use dist\DocumentStamper.exe (stamper-onefile.spec).
#
#   DocumentStamper.exe  windowed; the drop screen, drag onto it, Send to
#   stamper-cli.exe      console; the command line for scripts
#
# Both share _internal\ (Python, Qt, PyMuPDF, assets\fonts, config\default.json),
# so the whole folder must be copied together. Nothing is unpacked at start-up.
# config.json is looked for next to the exes.
# Build: .\packaging\build.ps1
# Built only with .\packaging\build.ps1 -Fallback, into dist\fallback\.
# Icon and version resource in place (Phase 5).

import sys
from pathlib import Path

sys.path.insert(0, str(Path(SPECPATH)))  # noqa: F821 - SPECPATH is set by PyInstaller
import spec_common  # noqa: E402

spec_common.Analysis = Analysis  # noqa: F821 - give the helper PyInstaller's class

window = spec_common.analysis("launch_window.py")
cli = spec_common.analysis("launch_cli.py")

window_exe = EXE(  # noqa: F821
    PYZ(window.pure),  # noqa: F821
    window.scripts,
    [],
    exclude_binaries=True,
    name="DocumentStamper",
    console=False,
    icon=spec_common.ICON,
    version=spec_common.VERSION_FILE,
    upx=False,
)
cli_exe = EXE(  # noqa: F821
    PYZ(cli.pure),  # noqa: F821
    cli.scripts,
    [],
    exclude_binaries=True,
    name="stamper-cli",
    console=True,
    icon=spec_common.ICON,
    version=spec_common.VERSION_FILE,
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
    name="DocumentStamper-folder",
)
