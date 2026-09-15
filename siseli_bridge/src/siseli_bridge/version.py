"""Single source of truth for the add-on version.

Keep in sync with siseli_bridge/config.yaml, which Supervisor reads directly and
which cannot import Python. tests/test_packaging.py enforces that they agree.
"""

__version__ = "2.6.24"
