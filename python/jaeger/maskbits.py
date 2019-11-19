#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-11
# @Filename: maskbits.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

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

    SYSTEM_INITIALIZED = 0x0000000000000001
    CONFIG_CHANGED = 0x0000000000000002
    BSETTINGS_CHANGED = 0x0000000000000004
    DATA_STREAMING = 0x0000000000000008
    RECEIVING_TRAJECTORY = 0x0000000000000010
    TRAJECTORY_ALPHA_RECEIVED = 0x0000000000000020
    TRAJECTORY_BETA_RECEIVED = 0x0000000000000040
    LOW_POWER_AFTER_MOVE = 0x0000000000000080
    DISPLACEMENT_COMPLETED = 0x0000000000000100
    DISPLACEMENT_COMPLETED_ALPHA = 0x0000000000000200
    DISPLACEMENT_COMPLETED_BETA = 0x0000000000000400
    COLLISION_ALPHA = 0x0000000000000800
    COLLISION_BETA = 0x0000000000001000
    CLOSED_LOOP_ALPHA = 0x0000000000002000
    CLOSED_LOOP_BETA = 0x0000000000004000
    PRECISE_POSITIONING_ALPHA = 0x0000000000008000
    PRECISE_POSITIONING_BETA = 0x0000000000010000
    COLLISION_DETECT_ALPHA_DISABLE = 0x0000000000020000
    COLLISION_DETECT_BETA_DISABLE = 0x0000000000040000
    MOTOR_CALIBRATION = 0x0000000000080000
    MOTOR_ALPHA_CALIBRATED = 0x0000000000100000
    MOTOR_BETA_CALIBRATED = 0x0000000000200000
    DATUM_CALIBRATION = 0x0000000000400000
    DATUM_ALPHA_CALIBRATED = 0x0000000000800000
    DATUM_BETA_CALIBRATED = 0x0000000001000000
    DATUM_INITIALIZATION = 0x0000000002000000
    DATUM_ALPHA_INITIALIZED = 0x0000000004000000
    DATUM_BETA_INITIALIZED = 0x0000000008000000
    HALL_ALPHA_DISABLE = 0x0000000010000000
    HALL_BETA_DISABLE = 0x0000000020000000
    COGGING_CALIBRATION = 0x0000000040000000
    COGGING_ALPHA_CALIBRATED = 0x0000000080000000
    COGGING_BETA_CALIBRATED = 0x0000000100000000
    ESTIMATED_POSITION = 0x0000000200000000
    POSITION_RESTORED = 0x0000000400000000
    SWITCH_OFF_AFTER_MOVE = 0x0000000800000000
    CALIBRATION_SAVED = 0x0000001000000000
    UNKNOWN = 0x0000010000000000

    @property
    def collision(self):
        """Returns `True` if the positioner is collided."""

        return True if (PositionerStatus.COLLISION_ALPHA & self or
                        PositionerStatus.COLLISION_BETA & self) else False


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
