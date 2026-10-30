# Exam Forge

Exam Forge is a SlideMage plugin for creating and managing exam questions for a deck. It is under development: the **Exam Forge…** command in the Deck menu currently shows a placeholder message. It requires SlideMage 9.5 or later.

## Development

The plugin uses API 1 and imports only `slidemage.plugin_api`, the standard library, the supported PySide6 modules, and its own modules via relative imports. Register contributions in `activate(host)` without network access.

Run `python -m pytest -q` with SlideMage's plugin API installed. Run `python build.py --check` to validate the manifest, package, imports and changelog, or `python build.py` to create the `.smplugin` archive and SHA-256 in `dist/`. Set `SLIDEMAGE_PLUGIN_PATH` to this checkout to load it in SlideMage.

The release workflow builds an archive from a version tag and derives release notes from `CHANGELOG.md` using `release_notes.py`.
