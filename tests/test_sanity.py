"""Sanity check: the project package is importable."""


def test_pipeline_package_importable():
    import pipeline
    assert pipeline is not None


def test_python_version_ok():
    import sys
    assert sys.version_info >= (3, 11), "Python 3.11+ required per portfolio rule"
    # Note: Python 3.13 is permitted but requires the scipy WMI-deadlock monkey-patch
    # (see C:\Users\user\.claude\rules\lessons.md). That workaround lives in the
    # orchestrator, not here — the runtime guard belongs with scipy import, not with
    # the version check.
