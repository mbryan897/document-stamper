"""Release files (step 13): changelog, security policy, notices, and what the build bundles and checks.

The packaging scripts are read as text and syntax trees, not run: PyInstaller
and pefile are build-time tools.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGING = REPO / "packaging"


def _assigned(path: Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value
    raise AssertionError(f"{name} not found in {path.name}")


def _check_exe_bundled() -> list[tuple[str, str]]:
    return [tuple(ast.literal_eval(e)) for e in _assigned(PACKAGING / "check_exe.py", "BUNDLED").elts]  # type: ignore[attr-defined]


def _spec_datas() -> list[str]:
    """The repo-relative source of each spec_common.DATAS entry, as written."""
    out: list[str] = []
    for e in _assigned(PACKAGING / "spec_common.py", "DATAS").elts:  # type: ignore[attr-defined]
        if isinstance(e, ast.Tuple):
            out.append(ast.unparse(e.elts[0]))
    return out


def test_check_exe_compares_the_notices_changelog_license_and_font_licenses() -> None:
    bundled = _check_exe_bundled()
    for entry in ((".", "THIRD-PARTY-NOTICES"), (".", "CHANGELOG.md"), (".", "LICENSE"), ("assets/fonts", "*.txt")):
        assert entry in bundled, entry
    for folder, pattern in bundled:
        assert list((REPO / folder).glob(pattern)), (folder, pattern)  # each matches a real repo file


def test_the_spec_bundles_what_check_exe_compares() -> None:
    datas = _spec_datas()
    for needed in ('str(ROOT / "THIRD-PARTY-NOTICES")', 'str(ROOT / "CHANGELOG.md")', 'str(ROOT / "LICENSE")',
                   "str(ROOT / 'assets' / 'fonts' / '*.txt')"):
        assert needed.replace("'", '"') in [d.replace("'", '"') for d in datas], needed


def test_changelog_lists_this_version_first() -> None:
    """Newest first: an optional "Unreleased", then the version in pyproject.toml, down to 1.0.0."""
    text = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert text.startswith("# What's new in Document Stamper\n")
    heads = re.findall(r"^## (.+)$", text, flags=re.M)
    version = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    released = heads[1:] if heads[0] == "Unreleased" else heads
    assert released[0] == version and released[-1] == "1.0.0", heads
    assert len(set(heads)) == len(heads) and "Unreleased" not in released
    assert "SHA256SUMS.txt" in text and "never of a document" in text
    assert not re.search(r"\b[DOU]-\d{3}\b|UX-\d{3}", text)  # plain language: no planning references


def test_build_writes_checksums_for_every_exe_it_builds() -> None:
    ps = (PACKAGING / "build.ps1").read_text(encoding="utf-8")
    assert '$hashTargets = @($Exe)' in ps
    assert 'DocumentStamper-folder\\DocumentStamper.exe' in ps and 'DocumentStamper-folder\\stamper-cli.exe' in ps
    assert 'Set-Content -Path "dist\\SHA256SUMS.txt"' in ps
    # written after signing, so the sums are of the files handed out
    assert ps.index("# 6. Sign") < ps.index('Set-Content -Path "dist\\SHA256SUMS.txt"')


def test_security_policy_says_how_and_warns_about_case_material() -> None:
    text = (REPO / "SECURITY.md").read_text(encoding="utf-8")
    for needed in ("Report a vulnerability", "privately", "Never attach real case material", "newest released version",
                   "99-2026-999901"):
        assert needed in text, needed


def test_security_policy_lists_what_is_stored_and_quotes_the_notes_as_worded() -> None:
    """The section a reviewer reads: every place written, and the warnings exactly as the program words them."""
    from stamper.handlers.pdf import KEEP_PROTECTION, OUTPUT_PASSWORD_RULE, UNPROTECTED_NOTE

    text = (REPO / "SECURITY.md").read_text(encoding="utf-8")
    section = text.split("## What is stored, and what the computer must provide", 1)[1]
    for needed in ("~stamper-", r"DocumentStamper\pending", r"DocumentStamper\work", "~stamper-combine-", "state.json",
                   "settings.remember_folders", "config.json.bak", r"Windows\SendTo", "stamper-sample-", "_MEI",
                   "held in memory only", "AES-256", "never encrypted", "not a substitute for disk encryption",
                   "must not be used to redact", r"QtProject\OrganizationDefaults\FileDialog", r"Explorer\ComDlg32",
                   UNPROTECTED_NOTE, KEEP_PROTECTION, OUTPUT_PASSWORD_RULE):
        assert needed in section, needed
    assert not re.search(r"\b[DOUF]-\d{2,3}\b|UX-\d{3}", section)  # plain language: no planning references
