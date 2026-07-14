"""Unit test package for the backend.

Ensures the backend/ directory (which holds flat, non-packaged modules like
parsers.py, config.py, etc.) is importable as top-level modules regardless
of the working directory the test runner is invoked from.
"""
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)
