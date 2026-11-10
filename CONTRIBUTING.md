# Contributing

Thank you for helping Sandling leave fewer abandoned clones behind.

## Local setup

Use Python 3.11 or later, then install the development extras:

```bash
python -m pip install -e '.[dev]'
python -m pytest
python -m ruff check src tests
python -m ruff format --check src tests
python -m build
```

Keep changes small, preserve the safety invariants, and add focused tests for behavioral changes.
Do not add automatic deletion, remote-branch deletion, hidden shell execution, or project-specific
defaults.

## Releases

Maintainers release annotated version tags after CI and clean-install verification. PyPI publishing
uses GitHub Trusted Publishing; do not add a long-lived package token to repository secrets.
