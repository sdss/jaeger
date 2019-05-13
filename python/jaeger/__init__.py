# encoding: utf-8

# flake8: noqa
# isort:skip_file

import os

from .core import get_config, get_logger


NAME = 'jaeger'

__version__ = '0.2.0dev'


config = get_config(NAME, allow_user=True)


log = get_logger('jaeger')
log_dir = config.get('log_dir', None) or '~/.jaeger'

can_log = get_logger('jaeger_can', capture_warnings=False)

log.start_file_logger(os.path.join(log_dir, 'jaeger.log'))
can_log.start_file_logger(os.path.join(log_dir, 'can.log'))


from .actor import *
from .can import *
from .core.exceptions import *
from .fps import *
from .maskbits import *
from .positioner import *
