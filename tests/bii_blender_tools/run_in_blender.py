"""Run the BII Tools tests in Blender's Python.

    blender -b --factory-startup --python-exit-code 1 --python tests/bii_blender_tools/run_in_blender.py
"""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    sys.path.insert(0, os.path.join(HERE, "..", "..", "plugins"))
    suite = unittest.defaultTestLoader.discover(HERE, pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


main()
