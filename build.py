# SPDX-FileCopyrightText: 2026 Stefan Palan
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check the plugin against the catalogue's conventions, then build dist/<id>-<version>.smplugin and its .sha256.

    python build.py              check, then build
    python build.py --check      check only
    python build.py --tag v1.2.0 also check that the tag matches the version (the release workflow does this)

The package is the folder named after the id, with hyphens as underscores: id "my-plugin", package my_plugin/.
"""
import argparse
import ast
import hashlib
import re
import sys
import tomllib
import zipfile

import release_notes
from pathlib import Path

ROOT = Path(__file__).parent
FIXED_TIME = (1980, 1, 1, 0, 0, 0)  # same bytes for the same files, so a rebuild gives the same hash

ID_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
RANGE_RE = re.compile(r"^(>=|<=|==|<|>)\s*\d+(\.\d+){0,2}(\s*,\s*(>=|<=|==|<|>)\s*\d+(\.\d+){0,2})*$")
COMPILED = (".pyc", ".pyo", ".so", ".pyd", ".dll", ".dylib", ".exe")
ALLOWED = set(sys.stdlib_module_names)
QT_MODULES = {"QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtSvg"}


def package_dir(meta):
    return ROOT / str(meta.get("id", "")).replace("-", "_")


def files(meta):
    yield ROOT / "plugin.toml"
    yield ROOT / "LICENSE"
    yield ROOT / "CHANGELOG.md"
    for path in sorted(package_dir(meta).rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            yield path


def check_manifest(meta):
    problems = []

    def text(key, longest):
        value = meta.get(key)
        if not isinstance(value, str) or not 1 <= len(value.strip()) <= longest:
            problems.append(f"plugin.toml: {key} must be text of 1 to {longest} characters")

    if not isinstance(meta.get("id"), str) or not ID_RE.match(meta["id"]):
        problems.append("plugin.toml: id must be 2-32 lowercase letters, digits or hyphens, starting with a letter")
    if not isinstance(meta.get("version"), str) or not VERSION_RE.match(meta["version"]):
        problems.append("plugin.toml: version must be MAJOR.MINOR.PATCH")
    text("name", 40)
    text("summary", 120)
    text("author", 200)
    text("license", 200)
    api = meta.get("api_version")
    if not isinstance(api, int) or isinstance(api, bool) or api < 1:
        problems.append("plugin.toml: api_version must be an integer of at least 1")
    if not isinstance(meta.get("requires_slidemage"), str) or not RANGE_RE.match(meta["requires_slidemage"]):
        problems.append('plugin.toml: requires_slidemage must be a version range such as ">=9.0, <10"')
    if not isinstance(meta.get("homepage"), str) or not meta["homepage"].startswith("https://"):
        problems.append("plugin.toml: homepage must be an https URL")
    for key in ("needs", "network"):
        lines = meta.get(key)
        if not isinstance(lines, list) or not all(isinstance(s, str) and 1 <= len(s) <= 120 for s in lines):
            problems.append(f"plugin.toml: {key} must be a list of lines of 1 to 120 characters")
    return problems


def check_imports(path, package):
    """Reject imports outside the allowed modules and relative imports above the plugin's package root."""
    tree = ast.parse(path.read_bytes(), filename=str(path))
    package_depth = len(path.parent.relative_to(ROOT / package).parts) + 1
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if node.level > package_depth:
                    source = "." * node.level + (node.module or "")
                    problems.append(
                        f"{path.relative_to(ROOT).as_posix()}:{node.lineno}: "
                        f"relative import {source} climbs above package root {package}")
                continue
            if node.module == "PySide6":
                names = [f"PySide6.{alias.name}" for alias in node.names]
            elif node.module == "slidemage":
                names = [f"slidemage.{alias.name}" for alias in node.names]
            else:
                names = [node.module]
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            if top in ALLOWED:
                continue
            if top == "PySide6" and name in {f"PySide6.{module}" for module in QT_MODULES}:
                continue
            if name == "slidemage.plugin_api" or name.startswith("slidemage.plugin_api."):
                continue
            problems.append(f"{path.relative_to(ROOT).as_posix()}:{node.lineno}: imports {name}")
    return problems


def check(meta, tag=None):
    problems = check_manifest(meta)
    if tag is not None and tag != f"v{meta.get('version')}":
        problems.append(f"the tag {tag} doesn't match version {meta.get('version')!r} in plugin.toml")
    package = package_dir(meta)
    init = package / "__init__.py"
    if not init.is_file():
        problems.append(f"no package: {init.relative_to(ROOT).as_posix()} is missing")
    else:
        defined = {node.name for node in ast.parse(init.read_bytes()).body if isinstance(node, ast.FunctionDef)}
        if "activate" not in defined:
            problems.append(f"{init.relative_to(ROOT).as_posix()} defines no activate(host)")
        for path in sorted(package.rglob("*")):
            if "__pycache__" in path.parts or not path.is_file():
                continue
            if path.suffix.lower() in COMPILED:
                problems.append(f"compiled file in the package: {path.relative_to(ROOT).as_posix()}")
            elif path.suffix == ".py":
                problems += check_imports(path, package.name)
    for required in ("LICENSE", "README.md", "CHANGELOG.md"):
        if not (ROOT / required).is_file():
            problems.append(f"{required} is missing")
    if (ROOT / "CHANGELOG.md").is_file():
        try:
            _, history_problems = release_notes.check_history(
                (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), meta.get("version"))
            problems += history_problems
        except (OSError, UnicodeError) as exc:
            problems.append(f"CHANGELOG.md: {exc}")
    return problems


def build(meta):
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    asset = dist / f"{meta['id']}-{meta['version']}.smplugin"
    with zipfile.ZipFile(asset, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files(meta):
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix(), FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    (dist / f"{asset.name}.sha256").write_text(f"{digest}  {asset.name}\n", encoding="utf-8", newline="\n")
    print(f"{asset.name}  {digest}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="check only, build nothing")
    parser.add_argument("--tag", help="the release tag, which must be v<version>")
    args = parser.parse_args(argv)
    try:
        meta = tomllib.loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"error: plugin.toml: {exc}", file=sys.stderr)
        return 1
    problems = check(meta, args.tag)
    if problems:
        print("\n".join(f"error: {problem}" for problem in problems), file=sys.stderr)
        return 1
    if not args.check:
        build(meta)
    return 0


if __name__ == "__main__":
    sys.exit(main())
