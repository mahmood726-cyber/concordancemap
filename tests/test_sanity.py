"""Sanity check: the project package is importable."""


def test_pipeline_package_importable():
    import pipeline
    assert pipeline is not None


def test_python_version_ok():
    import sys
    assert sys.version_info >= (3, 11), "Python 3.11+ required per portfolio rule"
    assert sys.version_info < (3, 13), "Python 3.13 has WMI deadlock risk on Windows"
