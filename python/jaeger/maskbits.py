#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-11
# @Filename: maskbits.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2019-07-10 13:28:10

import enum


__ALL__ = ['Maskbit', 'PositionerStatus', 'CommandStatus' 'ResponseCode',
           'RobotStatus', 'BootloaderStatus']


class Maskbit(enum.IntFlag):
    """A maskbit enumeration. Intended for subclassing."""

    @property
    def active_bits(self):
        """Returns a list of flags that match the value."""

        return [bit for bit in self.__class__ if bit.value & self.value]

    @property
    def name(self):
        """The name of the bit or bits active."""

        names = []
        for bit in self.active_bits:
            names.append(super(Maskbit, bit).name)

        return '|'.join(names)


class CommandStatus(Maskbit):
    """Maskbits for command status."""

    DONE = enum.auto()
    CANCELLED = enum.auto()
    FAILED = enum.auto()
    READY = enum.auto()
    RUNNING = enum.auto()
    TIMEDOUT = enum.auto()

    @property
    def is_done(self):
        """Returns True if the command is done (completed or failed)."""

        return True if (self in [self.DONE, self.TIMEDOUT] or self.failed) else False

    @property
    def is_running(self):
        """Returns True if the command is running."""

        return True if self == CommandStatus.RUNNING else False

    @property
    def failed(self):
        """Returns True if the command failed (or got cancelled)."""

        failed_states = self.CANCELLED | self.FAILED
        return True if self in failed_states else False

    @property
    def timed_out(self):
        """Returns True if the command timed out."""

        return True if self.TIMEDOUT else False


class PositionerStatus(Maskbit):
    """Maskbits for positioner status."""

    SYSTEM_INITIALIZATION = 0x00000001
    RECEIVING_TRAJECTORY = 0x00000100
    TRAJECTORY_ALPHA_RECEIVED = 0x00000200
    TRAJECTORY_BETA_RECEIVED = 0x00000400
    DATUM_INITIALIZATION = 0x00200000
    DATUM_ALPHA_INITIALIZED = 0x00400000
    DATUM_BETA_INITIALIZED = 0x00800000
    DISPLACEMENT_COMPLETED = 0x01000000
    ALPHA_DISPLACEMENT_COMPLETED = 0x02000000
    BETA_DISPLACEMENT_COMPLETED = 0x04000000
    ALPHA_COLLISION = 0x08000000
    BETA_COLLISION = 0x10000000
    DATUM_INITIALIZED = 0x20000000
    ESTIMATED_POSITION = 0x40000000
    POSITION_RESTORED = 0x80000000
    UNKNOWN = 0x100000000

    # The following bits are internal and not set by the firmware.
    DISABLED_UNKNWON_POSITION = 0x00000020
    DISABLED_KNWON_POSITION = 0x00000040

    DISABLED = DISABLED_KNWON_POSITION | DISABLED_UNKNWON_POSITION

    @property
    def collision(self):
        """Returns `True` if the positioner is collided."""

        return True if (PositionerStatus.ALPHA_COLLISION & self or
                        PositionerStatus.BETA_COLLISION & self) else False

    @property
    def disabled(self):
        """Returns `True` if the positioner is disabled."""

        return bool(self & self.DISABLED)


class BootloaderStatus(Maskbit):
    """Maskbits for positioner status when in bootloader mode."""

    BOOTLOADER_INIT = 0x00000001
    BOOTLOADER_TIMEOUT = 0x00000002
    BSETTINGS_CHANGED = 0x00000200
    RECEIVING_NEW_FIRMWARE = 0x00010000
    NEW_FIRMWARE_RECEIVED = 0x01000000
    NEW_FIRMWARE_CHECK_OK = 0x02000000
    NEW_FIRMWARE_CHECK_BAD = 0x04000000
    UNKNOWN = 0x40000000


class ResponseCode(enum.IntFlag):
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
