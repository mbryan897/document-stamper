# PyInstaller spec: the single DocumentStamper.exe (D-092).
#
# One windowed exe that runs by itself from any folder or drive. On each
# launch it unpacks its support files to a temporary folder (%TEMP%\_MEI…),
# runs, and removes them when it closes. config.json is looked for next to
# the exe, so the exe plus its config.json is the portable copy.
# Build: .\packaging\build.ps1
# Test build only: no signing, icon or antivirus work yet (Phase 5).

import sys
from pathlib import Path

sys.path.insert(0, str(Path(SPECPATH)))  # noqa: F821 - SPECPATH is set by PyInstaller
import spec_common  # noqa: E402

spec_common.Analysis = Analysis  # noqa: F821 - give the helper PyInstaller's class

a = spec_common.analysis("launch_window.py")
EXE(  # noqa: F821
    PYZ(a.pure),  # noqa: F821
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="DocumentStamper",
    console=False,
    upx=False,
    runtime_tmpdir=None,
)
