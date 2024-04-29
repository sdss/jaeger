# encoding: utf-8
# isort: skip_file

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING
import warnings

from sdsstools import get_config, get_logger, get_package_version
from sdsstools.configuration import __ENVVARS__

from .exceptions import JaegerUserWarning

if TYPE_CHECKING:
    from .actor import JaegerActor


NAME = "jaeger"

__version__ = get_package_version(path=__file__, package_name=NAME)


log = get_logger("jaeger", log_level=logging.WARNING)
can_log = get_logger("jaeger_can", log_level=logging.ERROR, capture_warnings=False)


# Start by loading the internal configuration file.
__ENVVARS__["OBSERVATORY"] = "?"  # Fallback

observatory = os.environ.get("OBSERVATORY", None)
if observatory is None:
    config = get_config(NAME)
else:
    config = get_config(NAME + f"_{observatory}")

# If we are not in debug mode, remove some possible warnings.
if config["debug"] is False:
    warnings.filterwarnings(
        "ignore",
        message=".+was never awaited.+",
        category=RuntimeWarning,
    )


warnings.simplefilter("always", category=JaegerUserWarning)


def start_file_loggers(start_log=True, start_can=True):
    if "files" in config and "log_dir" in config["files"]:
        log_dir = config["files"]["log_dir"]
    else:
        log_dir = "~/.jaeger"

    if start_log and log.fh is None:
        log.start_file_logger(os.path.join(log_dir, "jaeger.log"))

    if start_can and can_log.fh is None:
        can_log.start_file_logger(os.path.join(log_dir, "can.log"))


actor_instance: JaegerActor | None = None


from .can import *
from .exceptions import *
from .fps import *
from .fvc import *
from .ieb import *
from .maskbits import *
from .positioner import *
from .actor import *
