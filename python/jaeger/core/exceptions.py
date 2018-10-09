# !usr/bin/env python
# -*- coding: utf-8 -*-
#
# Licensed under a 3-clause BSD license.
#
# @Author: Brian Cherinka
# @Date:   2017-12-05 12:01:21
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last Modified time: 2017-12-05 12:19:32


class JaegerError(Exception):
    """A custom core Jaeger exception"""

    def __init__(self, message=None):

        message = 'There has been an error' \
            if not message else message

        super(JaegerError, self).__init__(message)


class JaegerNotImplemented(JaegerError):
    """A custom exception for not yet implemented features."""

    def __init__(self, message=None):

        message = 'This feature is not implemented yet.' \
            if not message else message

        super(JaegerNotImplemented, self).__init__(message)


class JaegerCANError(JaegerError):
    """Exception class for CAN-related errors."""

    def __init__(self, message=None, serial_reply=None):

        if message is None:
            message = ''

        if serial_reply is not None:
            message = message.strip() + ' ' + serial_reply

        super(JaegerCANError, self).__init__(message)


class JaegerMissingDependency(JaegerError):
    """A custom exception for missing dependencies."""
    pass


class JaegerWarning(Warning):
    """Base warning for Jaeger."""


class JaegerUserWarning(UserWarning, JaegerWarning):
    """The primary warning class."""
    pass


class JaegerSkippedTestWarning(JaegerUserWarning):
    """A warning for when a test is skipped."""
    pass


class JaegerDeprecationWarning(JaegerUserWarning):
    """A warning for deprecated features."""
    pass
