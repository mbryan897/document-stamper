"""No program is started by name, and Show in folder starts no process (D-116)."""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest

from stamper.gui.services import show_in_folder
from stamper.winshell import select_in_explorer

PACKAGE = Path(__file__).resolve().parent.parent / "stamper"

# Calls that start a process. Each needs a deliberate entry in ALLOWED.
_LAUNCHERS = {
    ("os", "system"), ("os", "popen"), ("os", "startfile"),
    *(("os", n) for n in ("execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe",
                          "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv", "spawnve", "spawnvp",
                          "spawnvpe", "posix_spawn", "posix_spawnp")),
}
_BANNED_MODULES = {"subprocess", "pty", "multiprocessing"}
_BANNED_NAMES = {"QProcess", "ShellExecuteW", "ShellExecuteA", "ShellExecuteExW", "WinExec", "CreateProcessW"}

# (file relative to stamper/, call) -> why it's safe
ALLOWED: dict[tuple[str, str], str] = {
    ("gui/services.py", "os.startfile"):
        "ShellExecute on a PDF the app saved, or a folder: file association, no search path",
}


def _findings(path: Path) -> list[str]:
    rel = path.relative_to(PACKAGE).as_posix()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _BANNED_MODULES:
                    found.append(f"{rel}:{node.lineno} imports {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in _BANNED_MODULES:
                found.append(f"{rel}:{node.lineno} imports from {node.module}")
            for alias in node.names:
                if alias.name in _BANNED_NAMES:
                    found.append(f"{rel}:{node.lineno} imports {alias.name}")
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and (node.value.id, node.attr) in _LAUNCHERS:
                call = f"{node.value.id}.{node.attr}"
                if (rel, call) not in ALLOWED:
                    found.append(f"{rel}:{node.lineno} uses {call}")
            if node.attr in _BANNED_NAMES:
                found.append(f"{rel}:{node.lineno} uses {node.attr}")
        elif isinstance(node, ast.Name) and node.id in _BANNED_NAMES:
            found.append(f"{rel}:{node.lineno} uses {node.id}")
        elif isinstance(node, ast.keyword) and node.arg == "shell":
            found.append(f"{rel}:{node.lineno} passes shell=")
    return found


def test_no_module_starts_a_program_by_name() -> None:
    findings = [f for path in sorted(PACKAGE.rglob("*.py")) for f in _findings(path)]
    assert findings == [], "Process launches need a deliberate exemption in ALLOWED:\n" + "\n".join(findings)


def test_guard_catches_the_old_show_in_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tests.test_no_bare_launch as me

    fake = tmp_path / "stamper"
    (fake / "gui").mkdir(parents=True)
    bad = fake / "gui" / "old.py"
    bad.write_text("import subprocess\nimport os\n"
                   "subprocess.Popen('explorer /select,\"x\"', shell=True)\nos.system('x')\n")
    monkeypatch.setattr(me, "PACKAGE", fake)
    found = me._findings(bad)
    assert any("imports subprocess" in f for f in found)
    assert any("shell=" in f for f in found)
    assert any("os.system" in f for f in found)


def test_show_in_folder_asks_the_shell_first(tmp_path: Path) -> None:
    target = tmp_path / "copy.pdf"
    selected: list[Path] = []
    opened: list[Path] = []
    show_in_folder(target, windows=True, select=selected.append, open_folder=opened.append)
    assert selected == [target] and opened == []


def test_show_in_folder_falls_back_to_opening_the_folder(tmp_path: Path) -> None:
    target = tmp_path / "a, b \"c\" d.pdf"
    opened: list[Path] = []

    def fails(_p: Path) -> None:
        raise OSError("no shell")

    show_in_folder(target, windows=True, select=fails, open_folder=opened.append)
    assert opened == [tmp_path]


@pytest.mark.skipif(os.name == "nt", reason="the off-Windows refusal")
def test_select_in_explorer_refuses_off_windows(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        select_in_explorer(tmp_path)


@pytest.mark.skipif(os.name != "nt", reason="Windows shell only")
def test_select_in_explorer_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        select_in_explorer(tmp_path / "not here.pdf")
