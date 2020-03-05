# encoding: utf-8

# flake8: noqa
# isort:skip_file

import os
import warnings

from sdsstools import (get_package_version, get_config, get_logger,
                       merge_config, read_yaml_file)


NAME = 'jaeger'

__version__ = get_package_version(path='./', package_name=NAME)


log = get_logger('jaeger')
can_log = get_logger('jaeger_can', capture_warnings=False)


config = get_config(NAME, allow_user=False)

sdsscore_path = os.path.expandvars('$SDSSCORE_DIR/configuration/actors/jaeger.yaml')
user_path = os.path.expanduser('~/.config/jaeger/jaeger.yml')

if os.path.exists(sdsscore_path):
    config = merge_config(read_yaml_file(sdsscore_path), config)
elif os.path.exists(user_path):
    config = merge_config(read_yaml_file(user_path), config)


if 'files' in config and 'log_dir' in config['files']:
    log_dir = config['files']['log_dir']
else:
    log_dir = '~/.jaeger'

log.start_file_logger(os.path.join(log_dir, 'jaeger.log'))
can_log.start_file_logger(os.path.join(log_dir, 'can.log'))


from .can import *
from .exceptions import *
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
