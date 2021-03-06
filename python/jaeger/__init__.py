# encoding: utf-8

# isort:skip_file

import logging
import os
import pathlib

from sdsstools import (get_package_version, get_config, get_logger,
                       merge_config, read_yaml_file)


NAME = 'jaeger'

__version__ = get_package_version(path='./', package_name=NAME)


log = get_logger('jaeger', log_level=logging.WARNING)
can_log = get_logger('jaeger_can', log_level=logging.ERROR,
                     capture_warnings=False)


# Start by loading the internal configuration file.
config = get_config(NAME)


def start_file_loggers(start_log=True, start_can=True):

    if 'files' in config and 'log_dir' in config['files']:
        log_dir = config['files']['log_dir']
    else:
        log_dir = '~/.jaeger'

    if start_log and log.fh is None:
        log.start_file_logger(os.path.join(log_dir, 'jaeger.log'))

    if start_can and can_log.fh is None:
        can_log.start_file_logger(os.path.join(log_dir, 'can.log'))


from .can import *
from .exceptions import *
from .fps import *
from .maskbits import *
from .positioner import *
from .actor import *
