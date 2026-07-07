"""Smoke test: the package imports and the toolchain runs."""

import src


def test_package_imports() -> None:
    assert src is not None
