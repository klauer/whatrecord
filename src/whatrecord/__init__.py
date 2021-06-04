import ctypes

import epicscorelibs.path

from ._version import get_versions

__version__ = get_versions()['version']
del get_versions

# Necessary unless [DY]LD_LIBRARY_PATH is set for epicscorelibs
ctypes.CDLL(epicscorelibs.path.get_lib("Com"))
ctypes.CDLL(epicscorelibs.path.get_lib("dbCore"))

del epicscorelibs.path
del ctypes

from .macro import MacroContext  # isort: skip  # noqa
from .iocsh import IOCShellLineParser  # isort: skip  # noqa
from .db import Database, load_database_file  # isort: skip  # noqa

__all__ = ["MacroContext", "IOCShellLineParser", "Database", "load_database_file"]
