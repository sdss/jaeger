# encoding: utf-8

# flake8: noqa
# isort:skip_file

import os
import warnings

from .core import get_config, get_logger

import pkg_resources


try:
    __version__ = pkg_resources.get_distribution('jaeger').version
except pkg_resources.DistributionNotFound:
    try:
        import toml
        poetry_config = toml.load(open(os.path.join(os.path.dirname(__file__),
                                                    '../../pyproject.toml')))
        __version__ = poetry_config['tool']['poetry']['version']
    except Exception:
        warnings.warn('cannot find jaeger version. Using 0.0.0.', UserWarning)
        __version__ = '0.0.0'


NAME = 'jaeger'


config = get_config(NAME, allow_user=True)


log = get_logger('jaeger')

if 'files' in config and 'log_dir' in config['files']:
    log_dir = config['files']['log_dir']
else:
    log_dir = '~/.jaeger'

can_log = get_logger('jaeger_can', capture_warnings=False)

log.start_file_logger(os.path.join(log_dir, 'jaeger.log'))
can_log.start_file_logger(os.path.join(log_dir, 'can.log'))


from .can import *
from .core.exceptions import *
from .fps import *
from .maskbits import *
from .positioner import *

try:
    from .actor import *
except ImportError as ee:
    if 'No module named \'clu\'' in str(ee):
        warnings.warn('clu not in PYTHONPATH. Cannot import JaegerActor.', JaegerUserWarning)
        JaegerActor = None
    else:
        raise
