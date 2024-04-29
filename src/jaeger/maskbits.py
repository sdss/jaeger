#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-11
# @Filename: maskbits.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

import enum


__all__ = [
    "Maskbit",
    "PositionerStatus",
    "CommandStatus",
    "ResponseCode",
    "BootloaderStatus",
    "FPSStatus",
]


class Maskbit(enum.IntFlag):
    """A maskbit enumeration. Intended for subclassing."""

    __version__ = None

    def __str__(self):
        members, _ = enum._decompose(self.__class__, self._value_)  # type: ignore
        return "|".join([str(m._name_ or m._value_) for m in members])

    @property
    def version(self):
        """The version of the flags."""

        return self.__version__

    @property
    def active_bits(self):
        """Returns a list of flags that match the value."""

        return [bit for bit in self.__class__ if bit.value & self.value]  # type: ignore


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

        return True if self.TIMEDOUT in self else False


class PositionerStatusV4_1(Maskbit):
    """Maskbits for positioner status (firmware >=04.01.00)."""

    __version__ = "4.1"

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
    PRECISE_MOVE_IN_OPEN_LOOP_ALPHA = 0x0000002000000000
    PRECISE_MOVE_IN_OPEN_LOOP_BETA = 0x0000004000000000
    SWITCH_OFF_HALL_AFTER_MOVE = 0x0000008000000000
    UNKNOWN = 0x0000010000000000

    @property
    def collision(self):
        """Returns `True` if the positioner is collided."""

        return (
            True
            if (
                PositionerStatusV4_1.COLLISION_ALPHA & self
                or PositionerStatusV4_1.COLLISION_BETA & self
            )
            else False
        )

    @property
    def initialised(self):
        """Returns `True` if the positioner is initialised."""

        return True if PositionerStatusV4_1.SYSTEM_INITIALIZED & self else False


class PositionerStatusV4_0(Maskbit):
    """Maskbits for positioner status (firmware <=04.00.04)."""

    __version__ = "4.0"

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

    @property
    def initialised(self):
        """Returns `True` if the positioner is initialised."""

        return True if PositionerStatusV4_0.SYSTEM_INITIALIZATION & self else False

    @property
    def collision(self):
        """Returns `True` if the positioner is collided."""

        return (
            True
            if (
                PositionerStatusV4_0.ALPHA_COLLISION & self
                or PositionerStatusV4_0.BETA_COLLISION & self
            )
            else False
        )


#: Alias to the default positioner status flags.
PositionerStatus = PositionerStatusV4_1


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
    """Maskbit for the status of the bootloader.

    - 0: All OK
    - 1: Received parameter is out of range
    - 2: Trajectory not accepted
    - 3: Already in motion, cannot accept command
    - 4: Datum not initialized
    - 5: Incorrect amount of data in packet
    - 6: One of the calibration modes is active: ``MOTOR_CALIBRATION``,
      ``COGGING_CALIBRATION``, ``DATUM_CALIBRATION``, ``DATUM _INITIALIZATION``
    - 7: The motors are not calibrated and therefore the hall sensors can't be used
    - 8: A collision is detected, the flag has to be first cleared with stop trajectory
    - 9: Hall sensors are disabled and can therefore not be used
    - 10: Broadcast command not valid
    - 11: Command not supported by bootloader
    - 12: Invalid command
    - 13: Unknown command
    - 14: Datum not calibrated
    - 15: Halls sensors have been disabled

    """

    COMMAND_ACCEPTED = 0
    VALUE_OUT_OF_RANGE = 1
    INVALID_TRAJECTORY = 2
    ALREADY_IN_MOTION = 3
    DATUM_NOT_INITIALIZED = 4
    INCORRECT_AMOUNT_OF_DATA = 5
    CALIBRATION_MODE_ACTIVE = 6
    MOTOR_NOT_CALIBRATED = 7
    COLLISION_DETECTED = 8
    HALL_SENSOR_DISABLED = 9
    INVALID_BROADCAST_COMMAND = 10
    INVALID_BOOTLOADER_COMMAND = 11
    INVALID_COMMAND = 12
    UNKNOWN_COMMAND = 13
    DATUM_NOT_CALIBRATED = 14
    HALL_SENSORS_DISABLED = 15


class FPSStatus(enum.Flag):
    """Status of the FPS."""

    IDLE = 0x01
    MOVING = 0x02
    COLLIDED = 0x04
    ERRORED = 0x08
    TEMPERATURE_NORMAL = 0x10
    TEMPERATURE_COLD = 0x20
    TEMPERATURE_VERY_COLD = 0x40
    TEMPERATURE_UNKNOWN = 0x80

    TEMPERATURE_BITS = (
        TEMPERATURE_NORMAL
        | TEMPERATURE_COLD
        | TEMPERATURE_VERY_COLD
        | TEMPERATURE_UNKNOWN
    )
    STATUS_BITS = IDLE | ERRORED | MOVING | COLLIDED
