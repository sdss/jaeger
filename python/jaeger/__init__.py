# encoding: utf-8

# flake8: noqa
# isort:skip_file

import logging
import os

from ruamel.yaml import YAML

from .core import get_logger


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


__version__ = '0.2.0dev'


try:
    __IPYTHON__
except NameError:
    __IPYTHON__ = False
else:
    __IPYTHON__ = True


log = get_logger('jaeger')
log_dir = config.get('log_dir', None) or '~/.jaeger'

can_log = get_logger('jaeger_can', capture_warnings=False)

log.start_file_logger(os.path.join(log_dir, 'jaeger.log'))
can_log.start_file_logger(os.path.join(log_dir, 'can.log'))


from .can import *
from .fps import *
