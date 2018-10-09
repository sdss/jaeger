#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: __init__.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-10-09 00:12:12

# flake8: noqa
# isort:skip_file

import enum


# Number of motor steps
MOTOR_STEPS = 2**30

# Time resolution
TIME_STEP = 5e-4


class CommandID(enum.IntEnum):
    """IDs associated with commands."""

    GET_ID = 1
    GET_FIRMWARE_VERSION = 2
    GET_STATUS = 3
    SEND_NEW_TRAJECTORY = 10
    SEND_TRAJECTORY_DATA = 11
    TRAJECTORY_DATA_END = 12
    TRAJECTORY_TRANSMISSION_ABORT = 13
    START_TRAJECTORY = 14
    STOP_TRAJECTORY = 15
    INITIALIZE_DATUMS = 20
    GO_TO_ABSOLUTE_POSITION = 30
    GO_TO_RELATIVE_POSITION = 31
    GET_ACTUAL_POSITION = 32
    SET_ACTUAL_POSITION = 33
    SET_SPEED = 40
    START_FIRMWARE_UPGRADE = 200
    SEND_FIRMWARE_DATA = 201

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
from .goto import *
from .status import *
from .trajectory import *


# Generate a dictionary of commands

_tmp_command_list = []

for item in vars().copy().values():
    if hasattr(item, '__bases__') and Command in item.__bases__:
        _tmp_command_list.append((item.command_id, item))

COMMAND_LIST = dict(sorted(_tmp_command_list))
