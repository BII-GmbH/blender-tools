"""Run the plan import tests in Blender's Python.

    blender -b --factory-startup --python-exit-code 1 --python tests/bii_blender_import/run_in_blender.py

Pillow and pypdfium2 come from the extension's wheels, which
scripts/build_extension.py downloads into plugins/bii_blender_import/wheels.
They are unpacked into a temporary folder for the run.
"""

import glob
import os
import platform
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
WHEELS = os.path.join(HERE, "..", "..", "plugins", "bii_blender_import", "wheels")


def _platform_matches(tag):
    machine = platform.machine().lower()
    if sys.platform == "win32":
        return tag == "win_amd64"
    if sys.platform == "darwin":
        return tag.startswith("macosx") and tag.endswith("arm64" if machine == "arm64" else "x86_64")
    return "linux" in tag and tag.endswith("x86_64")


def unpack_wheels(target):
    python_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
    for wheel in glob.glob(os.path.join(WHEELS, "*.whl")):
        name, version, py, abi, plat = os.path.basename(wheel)[:-4].split("-")
        if name.startswith("cadquery_ocp"):
            continue
        if py not in (python_tag, "py3") or not any(_platform_matches(t) for t in plat.split(".")):
            continue
        with zipfile.ZipFile(wheel) as z:
            z.extractall(target)
    sys.path.insert(0, target)


def main():
    # Windows keeps the loaded .pyd files locked, so they may stay behind.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as site:
        unpack_wheels(site)
        sys.path.insert(0, os.path.join(HERE, "..", "..", "plugins"))
        suite = unittest.defaultTestLoader.discover(HERE, pattern="test_*.py")
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


main()
