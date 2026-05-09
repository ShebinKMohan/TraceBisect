# Contributing

## Local Checks

Run the full validation suite before opening a pull request:

```bash
pytest
ruff check .
ruff format --check .
mypy src/tracebisect
python -m build
twine check dist/*
```

## Release Process

Releases are manual and tag-driven.

1. Bump the version in both `src/tracebisect/version.py` and `pyproject.toml`.
2. Commit the version bump.
3. Tag the commit with `v<version>`, for example `v0.1.0`.
4. Push the tag.

```bash
git tag v0.1.0
git push origin v0.1.0
```

The GitHub Actions release workflow builds the distributions and publishes to
PyPI via trusted publishing. No PyPI API token is stored in repository secrets.

The version is duplicated intentionally: `pyproject.toml` supplies package
metadata for build tools, while `src/tracebisect/version.py` provides lightweight
runtime access without importing package metadata.

