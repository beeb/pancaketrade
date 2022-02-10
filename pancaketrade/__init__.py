try:
    from importlib.metadata import metadata, version  # type: ignore
except ModuleNotFoundError:
    from importlib_metadata import metadata, version  # type: ignore

__version__ = version("pancaketrade")
__doc__ = metadata("pancaketrade")["Summary"]
__author__ = metadata("pancaketrade")["Author"]

from .trade import *
