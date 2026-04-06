"""Root conftest.py — ensures the repo root is on sys.path so that
'import backend.*' works from the tests/ directory."""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
