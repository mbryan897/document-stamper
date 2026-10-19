"""The lock inputs agree with pyproject.toml, and the locks pin everything with hashes (D-021, D-117)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REQ = ROOT / "requirements"
_PIN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)")


def _pins(path: Path) -> dict[str, str]:
    """Exact pins in a requirements file, following ``-r`` includes, names lowercased."""
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line.startswith("-r "):
            pins.update(_pins(path.parent / line[3:].strip()))
            continue
        m = _PIN.match(line)
        if m:
            pins[m.group(1).lower().replace("_", "-")] = m.group(2)
        elif line and not line.startswith("-"):
            raise AssertionError(f"{path.name}: not an exact pin: {line}")
    return pins


def _project() -> dict[str, object]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _as_pins(specs: list[str]) -> dict[str, str]:
    out = {}
    for spec in specs:
        name, _, version = spec.partition("==")
        assert version, f"pyproject.toml: {spec} isn't an exact pin (D-021)"
        out[name.strip().lower().replace("_", "-")] = version.strip()
    return out


def test_runtime_pins_match_pyproject() -> None:
    assert _pins(REQ / "runtime.in") == _as_pins(_project()["dependencies"])  # type: ignore[arg-type]


def test_dev_pins_match_pyproject() -> None:
    dev = _as_pins(_project()["optional-dependencies"]["dev"])  # type: ignore[index]
    pins = _pins(REQ / "dev.in")
    for name, version in dev.items():
        assert pins.get(name) == version, f"{name}: pyproject.toml has {version}, dev.in has {pins.get(name)}"


def test_build_input_has_the_app_pyinstaller_and_backend() -> None:
    pins = _pins(REQ / "build.in")
    assert set(_as_pins(_project()["dependencies"])) <= set(pins)  # type: ignore[arg-type]
    assert pins["pyinstaller"] == _as_pins(_project()["optional-dependencies"]["dev"])["pyinstaller"]  # type: ignore[index]
    assert {"setuptools", "wheel"} <= set(pins)


@pytest.mark.parametrize("name", ["runtime", "build", "dev"])
def test_lock_pins_everything_with_hashes(name: str) -> None:
    lock = REQ / f"{name}.lock"
    if not lock.is_file():
        pytest.skip(f"{lock.name} not generated yet (run tools\\lock.ps1 on Windows)")
    text = lock.read_text(encoding="utf-8")
    entries = re.split(r"\n(?=[A-Za-z0-9_.-]+==)", text)
    packages = [e for e in entries if _PIN.match(e.strip())]
    assert packages, "no packages in the lock"
    for entry in packages:
        assert "--hash=sha256:" in entry, f"{lock.name}: {entry.splitlines()[0]} has no hash"
    locked = {m.group(1).lower().replace("_", "-"): m.group(2) for e in packages if (m := _PIN.match(e.strip()))}
    for pkg, version in _pins(REQ / f"{name}.in").items():
        assert locked.get(pkg) == version, f"{lock.name}: {pkg} is {locked.get(pkg)}, {name}.in pins {version}"
