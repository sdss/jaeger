#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-11
# @Filename: utils.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import os

from typing import Optional, Tuple

import numpy
from astropy.time import Time

from jaeger import config
from jaeger.exceptions import JaegerError
from jaeger.maskbits import ResponseCode


__all__ = [
    "get_dtype_str",
    "int_to_bytes",
    "bytes_to_int",
    "get_identifier",
    "parse_identifier",
    "convert_kaiju_trajectory",
    "motor_steps_to_angle",
    "get_goto_move_time",
    "get_sjd",
]


MOTOR_STEPS = config["positioner"]["motor_steps"]


def get_dtype_str(dtype, byteorder="little"):
    """Parses dtype and byte order to return a type string code.

    Parameters
    ----------
    dtype : `numpy.dtype` or `str`
        Either a dtype (e.g., ``numpy.uint32``) or a string with the type code
        (``'>u4'``). If a string type code and the first character indicates
        the byte order (``'>'`` for big, ``'<'`` for little endian),
        ``byteorder`` will be ignored. The type code refers to bytes, while the
        dtype classes refer to bits, i.e., ``'u2'`` is equivalent to
    byteorder : str
        Either ``'big'`` for big endian representation or ``'little'`` for
        little end. ``'>'`` and ``'<'`` are also accepted, respectively.

    Returns
    -------
    type_code : `str`
        The type code for the input dtype and byte order.

    Examples
    --------
    ::

        >>> get_dtype_str(numpy.uint32, byteorder='big')
        '>u4'
        >>> get_dtype_str('u2', byteorder='>')
        '>u2'
        >>> get_dtype_str('<u2', byteorder='big')
        '<u2'

    """

    if byteorder == "big":
        byteorder = ">"
    elif byteorder == "little":
        byteorder = "<"
    elif byteorder in [">", "<"]:
        pass
    else:
        raise ValueError(f"invalid byteorder {byteorder}")

    if isinstance(dtype, str):
        if dtype[0] in [">", "<"]:
            return dtype
        elif dtype[0] == "=":
            raise ValueError("invalid byte order =. Please, use a specific endianess.")
        else:
            return byteorder + dtype

    dtype_str = dtype().dtype.str

    return byteorder + dtype_str[1:]


def int_to_bytes(value, dtype="u4", byteorder="little"):
    r"""Returns a bytearray with the representation of an integer.

    Parameters
    ----------
    value : int
        The integer to convert to bytes.
    dtype : `numpy.dtype` or `str`
        The `numpy.dtype` of the byte representation for the integer, or a
        type code that can include the endianess. See `.get_dtype_str` to
        understand how ``dtype`` and ``byteorder`` will be parsed.
    byteorder : str
        Either ``'big'`` for big endian representation or ``'little'`` for
        little end. ``'>'`` and ``'<'`` are also accepted, respectively.

    Returns
    -------
    bytes : `bytearray`
        A `bytearray` with the representation for the input integer.

    Examples
    --------
    ::

        >>> int_to_bytes(5, dtype=numpy.uint16, byteorder='big')
        bytearray(b'\x00\x05')

    """

    type_code = get_dtype_str(dtype, byteorder=byteorder)

    np_value = numpy.array(value, dtype=type_code)

    return bytearray(np_value.tobytes())


def bytes_to_int(bytes, dtype="u4", byteorder="little"):
    r"""Returns the integer from a bytearray representation.

    Parameters
    ----------
    bytes : `bytearray`
        The bytearray representing the integer.
    dtype : `numpy.dtype` or `str`
        The `numpy.dtype` of the byte representation for the integer, or a
        type code that can include the endianess. See `.get_dtype_str` to
        understand how ``dtype`` and ``byteorder`` will be parsed.
    byteorder : str
        Either ``'big'`` for big endian representation or ``'little'`` for
        little end. ``'>'`` and ``'<'`` are also accepted, respectively.

    Returns
    -------
    integer : int
        A integer represented by ``bytes``.

    Examples
    --------
    ::

        >>> bytes_to_int(b'\x00\x05', dtype=numpy.uint16, byteorder='big')
        5

    """

    type_code = get_dtype_str(dtype, byteorder=byteorder)

    np_buffer = numpy.frombuffer(bytes, dtype=type_code)

    return np_buffer[0]


def get_identifier(positioner_id, command_id, uid=0, response_code=0):
    """Returns a 29 bits identifier with the correct format.

    The CAN identifier format for the positioners uses an extended frame with
    29-bit encoding so that the 11 higher bits correspond to the positioner
    ID, the 8 middle bits are the command number, the following 6 bits are the
    unique identifier, and the 4 lower bits are the response code.

    Parameters
    ----------
    positioner_id : int
        The Id of the positioner to command, or zero for broadcast.
    command_id : int
        The ID of the command to send.
    uid : int
        The unique identifier
    response_code : int
        The response code.

    Returns
    -------
    identifier : `int`
        The decimal integer corresponding to the 29-bit identifier.

    Examples
    --------
    ::

        >>> get_identifier(5, 17, uid=5)
        1328128
        >>> bin(1328128)
        '0b101000100010000000000'

    """

    posid_bin = format(positioner_id, "011b")
    cid_bin = format(command_id, "08b")
    cuid_bin = format(uid, "06b")
    response_bin = format(int(response_code), "04b")

    identifier = posid_bin + cid_bin + cuid_bin + response_bin

    assert len(identifier) == 29

    return int(identifier, 2)


def parse_identifier(identifier: int) -> Tuple[int, int, int, ResponseCode]:
    """Parses an extended frame identifier and returns its components.

    The 29-bit extended frame identifier is composed of a positioner id,
    a command id, and a response code. This function parses an identifier
    and returns the value of each element.

    Parameters
    ----------
    identifier
        The identifier returned by the CAN bus.

    Returns
    -------
    components
        A tuple with the components of the identifier. The first element is
        the positioner id, the second the command id, the third is the command
        UID, and the last one is the response flag as an instance of
        `~jaeger.maskbits.ResponseCode`.

    Examples
    --------
    ::

        >>> parse_identifier(1315072)
        (5, 17, <ResponseCode.COMMAND_ACCEPTED: 0>)
        >>> parse_identifier(1315074)
        (5, 17, <ResponseCode.INVALID_TRAJECTORY: 2>)

    """

    def last(k, n):
        return (k) & ((1 << (n)) - 1)

    def mid(k, m, n):
        return last((k) >> (m), ((n) - (m)))

    positioner_id = mid(identifier, 18, 29)
    command_id = mid(identifier, 10, 18)
    command_uid = mid(identifier, 4, 10)
    response_code = mid(identifier, 0, 4)

    response_flag = ResponseCode(response_code)

    return positioner_id, command_id, command_uid, response_flag


def motor_steps_to_angle(alpha, beta, motor_steps=None, inverse=False):
    """Converts motor steps to angles or vice-versa.

    Parameters
    ----------
    alpha : float
        The alpha position.
    beta : float
        The beta position.
    motor_steps : int
        The number of steps in the motor. Defaults to
        the configuration value ``positioner.moter_steps``.
    inverse : bool
        If `True`, converts from angles to motor steps.

    Returns
    -------
    angles : `tuple`
        A tuple with the alpha and beta angles associated to the input
        motor steps. If ``inverse=True``, ``alpha`` and ``beta`` are considered
        to be angles and the associated motor steps are returned.

    """

    motor_steps = motor_steps or MOTOR_STEPS

    if inverse:
        return (
            int(numpy.round(alpha * motor_steps / 360.0)),
            int(numpy.round(beta * motor_steps / 360.0)),
        )

    return alpha / motor_steps * 360.0, beta / motor_steps * 360.0


def convert_kaiju_trajectory(path, speed=None, step_size=0.03, invert=True):
    """Converts a raw kaiju trajectory to a jaeger trajectory format.

    Parameters
    ----------
    path : str
        The path to the raw trajectory.
    speed : float
        The maximum speed, used to convert from kaiju steps to times,
        in degrees per second. If not set, assumes 1000 RPM.
    step_size : float
        The step size in degrees per step.
    invert : bool
        If `True`, inverts the order of the points.

    Returns
    -------
    trajectory : `dict`
        A dictionary with the trajectory in a format understood by
        `~jaeger.commands.send_trajectory`.

    """

    # TODO: this is a rough estimate of the deg/sec if RPM=1000.
    speed = speed or 6.82

    raw = open(path, "r").read().splitlines()

    alpha_steps = []
    beta_steps = []
    alpha_deg = []
    beta_deg = []

    for line in raw:
        if line.startswith("smoothAlphaStep"):
            alpha_steps = list(map(int, line.split(":")[1].split(",")))
        elif line.startswith("smoothBetaStep"):
            beta_steps = list(map(int, line.split(":")[1].split(",")))
        elif line.startswith("smoothAlphaDeg"):
            alpha_deg = list(map(float, line.split(":")[1].split(",")))
        elif line.startswith("smoothBetaDeg"):
            beta_deg = list(map(float, line.split(":")[1].split(",")))
        else:
            pass

    alpha_times = numpy.array(alpha_steps) * 0.03 / speed
    beta_times = numpy.array(beta_steps) * 0.03 / speed

    alpha = numpy.zeros((len(alpha_times), 2))
    beta = numpy.zeros((len(beta_times), 2))

    alpha[:, 0] = alpha_deg
    alpha[:, 1] = alpha_times
    beta[:, 0] = beta_deg
    beta[:, 1] = beta_times

    if invert:
        alpha = alpha[::-1]
        beta = beta[::-1]
        alpha[:, 1] = -alpha[:, 1] + alpha[0, 1]
        beta[:, 1] = -beta[:, 1] + beta[0, 1]

    return {"alpha": alpha.tolist(), "beta": beta.tolist()}


def get_goto_move_time(move, speed=None):
    r"""Returns the approximate time need for a given move, in seconds.

    The move time is calculated as :math:`\dfrac{60 \alpha r}{360 v}` where
    :math:`\alpha` is the angle, :math:`r` is the reduction ratio, and
    :math:`v` is the speed in the input in RPM. It adds 0.25s due to
    deceleration; this value is not exact but it's a good approximation for
    most situations.

    Parameters
    ----------
    move : float
        The move, in degrees.
    speed : float
        The speed of the motor for the move, in RPM on the input.

    """

    speed = speed or config["positioner"]["motor_speed"]

    return move * config["positioner"]["reduction_ratio"] / (6.0 * speed) + 0.25


def get_sjd(observatory: Optional[str] = None) -> int:
    """Returns the SDSS Julian Date as an integer based on the observatory.

    Parameters
    ----------
    observatory
        The current observatory, either APO or LCO. If `None`, uses ``$OBSERVATORY``.

    """

    if observatory is None:
        try:
            observatory = os.environ["OBSERVATORY"]
        except KeyError:
            raise JaegerError("Observatory not passed and $OBSERVATORY is not set.")

    observatory = observatory.upper()
    if observatory not in ["APO", "LCO"]:
        raise JaegerError(f"Invalid observatory {observatory}.")

    time = Time.now()
    mjd = time.mjd

    if observatory == "APO":
        return int(mjd + 0.3)
    else:
        return int(mjd + 0.7)
