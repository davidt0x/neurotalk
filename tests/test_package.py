from __future__ import annotations

import importlib.metadata

import neurotalk as m


def test_version():
    assert importlib.metadata.version("neurotalk") == m.__version__
