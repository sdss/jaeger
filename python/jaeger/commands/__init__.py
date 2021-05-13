#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: __init__.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import enum

from typing import Type, Union


class TypesEnumMeta(enum.EnumMeta):
    """Metaclass to allow initialising an Enum from a string."""

    def __call__(cls: Type[enum.Enum], value: Union[str, int]):  # type: ignore

        if isinstance(value, str):
            for flag in cls:
                if flag.name.lower() == value.lower():
                    return cls(flag.value)

        return super().__call__(value)


class CommandID(enum.IntEnum, metaclass=TypesEnumMeta):
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
    COLLISION_DETECTED = 18
    GO_TO_DATUMS = 20
    GO_TO_DATUM_ALPHA = 21
    GO_TO_DATUM_BETA = 22
    START_DATUM_CALIBRATION = 23
    START_MOTOR_CALIBRATION = 26
    GO_TO_ABSOLUTE_POSITION = 30
    GO_TO_RELATIVE_POSITION = 31
    GET_ACTUAL_POSITION = 32
    SET_ACTUAL_POSITION = 33
    GET_OFFSETS = 34
    SET_OFFSETS = 35
    SET_SPEED = 40
    SET_CURRENT = 41
    START_COGGING_CALIBRATION = 47
    SAVE_INTERNAL_CALIBRATION = 53
    GET_CURRENT = 56
    SET_HOLDING_CURRENT = 112
    GET_HOLDING_CURRENT = 113
    HALL_ON = 116
    HALL_OFF = 117
    ALPHA_CLOSED_LOOP_COLLISION_DETECTION = 118
    ALPHA_CLOSED_LOOP_WITHOUT_COLLISION_DETECTION = 119
    ALPHA_OPEN_LOOP_COLLISION_DETECTION = 120
    ALPHA_OPEN_LOOP_WITHOUT_COLLISION_DETECTION = 121
    BETA_CLOSED_LOOP_COLLISION_DETECTION = 122
    BETA_CLOSED_LOOP_WITHOUT_COLLISION_DETECTION = 123
    BETA_OPEN_LOOP_COLLISION_DETECTION = 124
    BETA_OPEN_LOOP_WITHOUT_COLLISION_DETECTION = 125
    GET_RAW_TEMPERATURE = 132
    START_FIRMWARE_UPGRADE = 200
    SEND_FIRMWARE_DATA = 201

    def get_command_class(self):
        """Returns the class associated with this command."""

        if self in COMMAND_LIST:
            return COMMAND_LIST[self]


from .base import *
from .base import Command
from .bootloader import *
from .calibration import *
from .goto import *
from .status import *
from .trajectory import *


def all_subclasses(cls):
    """Recursive subclasses."""

    return set(cls.__subclasses__()).union(
        [s for c in cls.__subclasses__() for s in all_subclasses(c)]
    )


# Generate a dictionary of commands
COMMAND_LIST = {
    cclass.command_id: cclass
    for cclass in all_subclasses(Command)
    if cclass.command_id in CommandID
}

# Dynamically generate command classes for those commands for which we
# didn't write a class. These classes are identical to a generic Command
# but with a custom name.
for cid in list(CommandID):
    if cid not in COMMAND_LIST:
        CommandClass = type(
            cid.name.title().replace("_", ""),
            (Command,),
            {"command_id": cid, "broadcastable": False},
        )
        COMMAND_LIST[cid] = CommandClass
