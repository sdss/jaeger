#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-11
# @Filename: maskbits.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-09-12 23:34:22

import enum


__ALL__ = ['Maskbit', 'BootloaderStatus', 'CommandStatus' 'ResponseCode',
           'RobotStatus']


class Maskbit(enum.Flag):
    """A maskbit enumeration. Intended for subclassing."""

    @property
    def active_bits(self):
        """Returns a list of flags that match the value."""

        return [bit for bit in self.__class__ if bit.value & self.value]


class BootloaderStatus(Maskbit):
    """Maskbit for the status of the bootloader."""

    BOOTLOADER_INIT = 2**0
    BOOTLOADER_TIMEOUT = 2**1
    BSETTINGS_CHANGED = 2**9
    RECEIVING_NEW_FIRMWARE = 2**16
    NEW_FIRMWARE_RECEIVED = 2**24
    NEW_FIRMWARE_CHECK_OK = 2**25
    NEW_FIRMWARE_CHECK_BAD = 2**26


class CommandStatus(Maskbit):
    """Maskbits for command status."""

    DONE = enum.auto()
    CANCELLED = enum.auto()
    FAILED = enum.auto()
    READY = enum.auto()
    RUNNING = enum.auto()

    ACTIVE_STATES = RUNNING
    FAILED_STATES = CANCELLED | FAILED
    DONE_STATES = DONE | FAILED_STATES
    ALL_STATES = READY | ACTIVE_STATES | DONE_STATES

    @property
    def is_done(self):
        """Returns True if the command is done (completed or failed)."""

        return True if self in CommandStatus.DONE_STATES else False

    @property
    def is_running(self):
        """Returns True if the command is running."""

        return True if self == CommandStatus.RUNNING else False

    @property
    def failed(self):
        """Returns True if the command failed (or got cancelled)."""

        return True if self in CommandStatus.FAILED_STATES else False


class PositionerStatus(Maskbit):
    """Maskbits for positioner status."""

    OK = enum.auto()
    RESET = enum.auto()
    MOVING = enum.auto()
    REACHED = enum.auto()
    UNKNOWN = enum.auto()
    COLLIDED = enum.auto()


class ResponseCode(enum.Flag):
    """Maskbit for the status of the bootloader."""

    COMMAND_ACCEPTED = 0
    VALUE_OUT_OF_RANGE = 1
    INVALID_TRAJECTORY = 2
    ALREADY_IN_MOTION = 3
    NOT_INITIALIZED = 4
    INVALID_BROADCAST_COMMAND = 10
    INVALID_BOOTLOADER_COMMAND = 11
    INVALID_COMMAND = 12
    UNKNOWN_COMMAND = 13
