# !usr/bin/env python
# -*- coding: utf-8 -*-
#
# Licensed under a 3-clause BSD license.
#
# @Author: Brian Cherinka
# @Date:   2017-12-05 12:01:21
# @Last modified by: José Sánchez-Gallego
# @Last Modified time: 2017-12-05 12:19:32

from __future__ import annotations

import inspect

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from jaeger.commands.trajectory import Trajectory


class JaegerError(Exception):
    """A custom core Jaeger exception"""

    def __init__(self, message=None):
        message = "There has been an error" if not message else message
        super(JaegerError, self).__init__(message)


class FPSLockedError(JaegerError):
    """The FPS is locked."""


class JaegerNotImplemented(JaegerError):
    """A custom exception for not yet implemented features."""

    def __init__(self, message=None):
        message = "This feature is not implemented yet." if not message else message
        super(JaegerNotImplemented, self).__init__(message)


class JaegerCANError(JaegerError):
    """Exception class for CAN-related errors."""

    def __init__(self, message=None, serial_reply=None):

        if message is None:
            message = ""

        if serial_reply is not None:
            message = message.strip() + " " + serial_reply

        super(JaegerCANError, self).__init__(message)


class PositionerError(JaegerError):
    """Exception class for positioner-related errors."""

    def __init__(self, message=None, positioner=None):

        if message is None:
            message = ""

        if positioner is not None:
            pid = positioner.positioner_id
        else:
            stack = inspect.stack()
            f_locals = stack[1][0].f_locals

            if "self" in f_locals:
                pid = f_locals["self"].positioner_id
            else:
                pid = "UNKNOWN"

        message = f"Positioner {pid}: {message}"

        super(PositionerError, self).__init__(message)


class CommandError(JaegerError):
    """Exception class for command-related errors."""

    def __init__(self, message=None, command=None):

        if message is None:
            message = ""

        if command is None:
            stack = inspect.stack()
            f_locals = stack[1][0].f_locals

            if "self" in f_locals:
                command = f_locals["self"]

        if command:
            c_name = command.name
            c_uid = command.command_uid
            message = f"({c_name}, {c_uid!s}): {message}"

        super(CommandError, self).__init__(message)


class JaegerMissingDependency(JaegerError):
    """A custom exception for missing dependencies."""

    pass


class TrajectoryError(JaegerError):
    """A trajectory error."""

    def __init__(self, message=None, trajectory: Trajectory | None = None):
        if message and isinstance(message, str) and message[-1] == ".":
            message = message[:-1]

        super().__init__(message)

        self.trajectory = trajectory
        if self.trajectory:
            self.trajectory.failed = True


class FVCError(JaegerError):
    """An error handling the FVC or the FVC loop."""

    pass


class JaegerWarning(Warning):
    """Base warning for Jaeger."""

    pass


class JaegerUserWarning(UserWarning, JaegerWarning):
    """The primary warning class."""

    pass


class JaegerSkippedTestWarning(JaegerUserWarning):
    """A warning for when a test is skipped."""

    pass


class JaegerDeprecationWarning(JaegerUserWarning):
    """A warning for deprecated features."""

    pass
