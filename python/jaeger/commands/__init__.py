#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: __init__.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-09-16 15:42:12

# flake8: noqa
# isort:skip_file

import enum


class CommandID(enum.IntEnum):
    """IDs associated with commands."""

    GET_ID = 1
    GET_STATUS = 3

    def get_command(self):
        """Returns the class associated with this command."""

        return COMMAND_LIST[self]


def CommandID__new__(cls, value):
    """Allows to instantiate based on the flag string.

    We cannot override __new__ directly on the subclass. We need
    to add it after the class has been defined. See http://bit.ly/2CStmNm.

    """

    if isinstance(value, str):
        for flag in cls:
            if flag.name.lower() == value.lower():
                return CommandID(flag.value)

    return super(CommandID, cls).__new__(cls, value)


CommandID.__new__ = CommandID__new__


from .base import *
from .bootloader import *
from .status import *


# Generate a dictionary of commands

_tmp_command_list = []

for item in vars().copy().values():
    if hasattr(item, '__bases__') and Command in item.__bases__:
        _tmp_command_list.append((item.command_id, item))

COMMAND_LIST = dict(sorted(_tmp_command_list))
