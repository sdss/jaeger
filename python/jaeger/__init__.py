# encoding: utf-8

# flake8: noqa
# isort:skip_file

import logging
import os

from ruamel.yaml import YAML

# Inits the logging system. Only shell logging, and exception and warning catching.
# File logging can be started by calling log.start_file_logger(name).
from .core import log


log.start_file_logger('~/.jaeger/jaeger.log')


def merge(user, default):
    """Merges a user configuration with the default one."""

    if isinstance(user, dict) and isinstance(default, dict):
        for kk, vv in default.items():
            if kk not in user:
                user[kk] = vv
            else:
                user[kk] = merge(user[kk], vv)

    return user


NAME = 'jaeger'

# Loads config
yaml = YAML(typ='safe')
config = yaml.load(open(os.path.dirname(__file__) + '/etc/{0}.yml'.format(NAME)))

# If there is a custom configuration file, updates the defaults using it.
custom_config_fn = os.path.expanduser('~/.{0}/{0}.yml'.format(NAME))
if os.path.exists(custom_config_fn):
    config = merge(yaml.load(open(custom_config_fn)), config)


__version__ = '0.1.0'


try:
    __IPYTHON__
except NameError:
    __IPYTHON__ = False
else:
    __IPYTHON__ = True


# Add a logger for the CAN interface
can_log = logging.getLogger('jaeger_can')
can_log._set_defaults()
can_log.start_file_logger('~/.jaeger/can.log')


from . import extern
from .can import *
from .fps import *
