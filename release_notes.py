# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validate a plugin's history and print user-facing notes for a release.

Adapted from SlideMage's packaging/release_notes.py and version.parse_history.
    python release_notes.py v1.2.0 > notes.md
    python release_notes.py v1.2.0 --previous v1.0.0 > notes.md
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parent
HEADER = re.compile(r"^v(\d+\.\d+\.\d+) \(([^\n()]*)\):\s*(\S.*)$")
CANDIDATE = re.compile(r"^v\d")


@dataclass
class Entry:
    version: str
    paragraphs: list[str]
    summary: str
    news: list[str]


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(map(int, version.removeprefix("v").split(".")))


def parse_history(text: str) -> tuple[list[Entry], list[str]]:
    """Parse entries, preserving line breaks in NEW paragraphs for bullets."""
    entries = []
    errors = []
    current = None
    body = []

    def finish():
        if current is None:
            return
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", "\n".join(body)) if p.strip()]
        news_paragraphs = [p for p in paragraphs if p.startswith("NEW:")]
        summary_lines = []
        bullets = []
        if len(news_paragraphs) != 1:
            errors.append(f"{current}: expected one NEW: paragraph with at least one '- ' bullet")
        else:
            for line in news_paragraphs[0][len("NEW:"):].splitlines():
                line = line.strip()
                if line == "-":
                    bullets.append("")
                elif line.startswith("- "):
                    bullets.append(line[2:].strip())
                elif line:
                    if bullets:
                        bullets[-1] += " " + line
                    else:
                        summary_lines.append(line)
            if not bullets or any(not bullet for bullet in bullets):
                errors.append(f"{current}: NEW: paragraph needs at least one nonempty '- ' bullet")
        entries.append(Entry(current, paragraphs, " ".join(summary_lines), bullets))

    for lineno, line in enumerate(text.splitlines(), 1):
        match = HEADER.match(line)
        if match:
            finish()
            current = "v" + match.group(1)
            meta = match.group(2).split(",", 1)
            if len(meta) != 2 or not all(part.strip() for part in meta):
                errors.append(f"CHANGELOG.md:{lineno}: {current} needs a date and reason separated by a comma")
            body = [match.group(3)]
        elif CANDIDATE.match(line):
            finish()
            current, body = None, []
            errors.append(f"CHANGELOG.md:{lineno}: invalid entry; expected 'vX.Y.Z (date, reason): description'")
        elif current is not None:
            body.append(line)
    finish()
    return entries, errors


def check_history(text: str, manifest_version: str) -> tuple[list[Entry], list[str]]:
    entries, errors = parse_history(text)
    if not entries:
        errors.append("CHANGELOG.md has no entries")
        return entries, errors
    for old, new in zip(entries, entries[1:]):
        old_version, new_version = version_tuple(old.version), version_tuple(new.version)
        if new_version <= old_version:
            errors.append(f"CHANGELOG.md: {new.version} must be newer than {old.version} (entries oldest first)")
        if new_version[0] > old_version[0] and not any(
            p.startswith("BREAKING:") and p[len("BREAKING:"):].strip() for p in new.paragraphs
        ):
            errors.append(f"{new.version} raises the major version after {old.version} but has no nonempty BREAKING: paragraph")
    if entries[-1].version != f"v{manifest_version}":
        errors.append(f"plugin.toml version {manifest_version!r} does not match newest CHANGELOG.md entry {entries[-1].version}")
    return entries, errors


def previous_tag(tag: str) -> str | None:
    try:
        result = subprocess.run(["git", "describe", "--tags", "--abbrev=0", f"{tag}^"],
                                cwd=ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def render(entries: list[Entry], tag: str, previous: str | None = None, repo: str | None = None) -> str:
    """Render NEW bullets and BREAKING paragraphs, newest first, as in the app."""
    selected = [e for e in entries if e.version == tag or (
        previous is not None and version_tuple(previous) < version_tuple(e.version) <= version_tuple(tag))]
    selected.sort(key=lambda e: version_tuple(e.version), reverse=True)
    parts = []
    if selected[0].summary:
        parts.append(selected[0].summary)
    if len(selected) > 1:
        span = (f"{selected[-1].version} and {selected[0].version}" if len(selected) == 2
                else f"{selected[-1].version} to {selected[0].version}")
        parts.append(f"*This release covers {span}.*")
    parts.append("\n".join(f"- {bullet}" for entry in selected for bullet in entry.news))
    breaking = [(e.version, " ".join(p[len('BREAKING:'):].split()))
                for e in selected for p in e.paragraphs if p.startswith("BREAKING:")]
    if breaking:
        lines = [f"- **{v}:** {body}" if len(selected) > 1 else f"- {body}" for v, body in breaking]
        parts.append("## Breaking changes\n\n" + "\n".join(lines))
    if previous and repo:
        parts.append(f"## Full changelog\n\nhttps://github.com/{repo}/compare/{previous}...{tag}")
    return "\n\n".join(parts) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tag", help="release tag, v<version>")
    parser.add_argument("--previous", help="previous release tag (default: from git)")
    parser.add_argument("--repo", help="GitHub owner/repository for a compare link")
    args = parser.parse_args(argv)
    try:
        meta = tomllib.loads((ROOT / "plugin.toml").read_text(encoding="utf-8"))
        entries, errors = check_history((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), meta.get("version"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not isinstance(meta.get("version"), str) or not re.fullmatch(r"\d+\.\d+\.\d+", meta["version"]):
        errors.append("plugin.toml version must be MAJOR.MINOR.PATCH")
    if args.tag != f"v{meta.get('version')}":
        errors.append(f"tag {args.tag!r} does not match plugin.toml version {meta.get('version')!r}")
    previous = args.previous if args.previous is not None else previous_tag(args.tag)
    if previous and (not re.fullmatch(r"v\d+\.\d+\.\d+", previous) or
                     (entries and version_tuple(previous) >= version_tuple(entries[-1].version))):
        errors.append(f"previous tag {previous!r} must be a version older than {args.tag}")
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(render(entries, args.tag, previous, args.repo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
