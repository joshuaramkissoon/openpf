"""Keep the test suite away from real data.

Some tests import the app (and its default engine) directly. Point the default
database and Archie's runtime folder at a throwaway directory before any app
module is imported, so running pytest in a real checkout can never open the
user's database, artifacts or memory.
"""

import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="openpf-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/test.db"
os.environ["OPENPF_RUNTIME_DIR"] = os.path.join(_TMP, "runtime")
os.environ.setdefault("RECIPES_DATA_DIR", os.path.join(_TMP, "recipes"))
os.environ.setdefault("VIEWS_DATA_DIR", os.path.join(_TMP, "views"))
os.environ["INPROC_SCHEDULER_ENABLED"] = "false"
