# conftest.py — pytest/unittest discovery helper
# This file ensures the tests/ directory is recognized as a test package.
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
