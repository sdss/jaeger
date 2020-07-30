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


# Start by loading the internal configuration file.
config = get_config(NAME, allow_user=False)
config.__CONFIG_FILE__ = None

# Ranked possible paths for user configuration.
config_paths = []
if 'OBSERVATORY' in os.environ:
    observatory = os.environ['OBSERVATORY'].lower()
    config_paths.append(f'$SDSSCORE_DIR/configuration/{observatory}/actors/jaeger')
config_paths.append('~/.config/sdss/jaeger')

for config_path in config_paths:
    for ext in ['yaml', 'yml']:
        fpath = os.path.expanduser(os.path.expandvars(config_path + '.' + ext))
        if os.path.exists(fpath):
            config.load(fpath)
            config.__CONFIG_FILE__ = fpath
            break
    if config.__CONFIG_FILE__:
        break


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

try:
    from .actor import *
except ImportError as ee:
    if 'No module named \'clu\'' in str(ee):
        warnings.warn('clu not in PYTHONPATH. Cannot import JaegerActor.', JaegerUserWarning)
        JaegerActor = None
    else:
        raise
