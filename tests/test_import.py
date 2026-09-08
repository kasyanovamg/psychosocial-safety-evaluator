"""Smoke test for the installed package."""

import importlib


def test_package_imports():
    assert importlib.import_module("psych_eval") is not None
