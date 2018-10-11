#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-11
# @Filename: utils.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-10-10 18:45:12

import numpy

from jaeger.maskbits import ResponseCode


__ALL__ = ['get_dtype_str', 'int_to_bytes', 'bytes_to_int',
           'get_identifier', 'parse_identifier']


def get_dtype_str(dtype, byteorder='big'):
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

    if byteorder == 'big':
        byteorder = '>'
    elif byteorder == 'little':
        byteorder = '<'
    elif byteorder in ['>', '<']:
        pass
    else:
        raise ValueError(f'invalid byteorder {byteorder}')

    if isinstance(dtype, str):
        if dtype[0] in ['>', '<']:
            return dtype
        elif dtype[0] == '=':
            raise ValueError('invalid byte order =. '
                             'Please, use a specific endianess.')
        else:
            return byteorder + dtype

    dtype_str = dtype().dtype.str

    return byteorder + dtype_str[1:]


def int_to_bytes(value, dtype='u4', byteorder='big'):
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


def bytes_to_int(bytes, dtype='u4', byteorder='big'):
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


def get_identifier(positioner_id, command_id, response_code=0):
    """Returns a 29 bits identifier with the correct format.

    The CAN identifier format for the positioners uses an extended frame with
    29-bit encoding so that the 11 higher bits correspond to the positioner
    ID, the 10 middle bits are the command number, and the 8 lower bits are the
    response code.

    Parameters
    ----------
    positioner_id : int
        The Id of the positioner to command, or zero for broadcast.
    command_id : int
        The ID of the command to send.
    response_code : `int` or `~jaeger.maskbits.ResponseCode`
        The response code.

    Returns
    -------
    identifier : `int`
        The decimal integer corresponding to the 29-bit identifier.

    Examples
    --------
    ::

        >>> get_identifier(5, 17)
        1315072
        >>> bin(1315072)
        '0b101000001000100000000'

    """

    posid_bin = format(positioner_id, '011b')
    cid_bin = format(command_id, '010b')
    response_bin = format(ResponseCode(response_code).value, '08b')

    identifier = posid_bin + cid_bin + response_bin

    assert len(identifier) == 29

    return int(identifier, 2)


def parse_identifier(identifier):
    """Parses an extended frame identifier and returns its components.

    The 29-bit extended frame identifier is composed of a positioner id,
    a command id, and a response code. This function parses an identifier
    and returns the value of each element.

    Parameters
    ----------
    identifier : `int`
        The identifier returned by the CAN bus.

    Returns
    -------
    components : tuple
        A tuple with the components of the identifier. The first element is
        the positioner id, the second the command id, and the third the
        response flag as an instance of `~jaeger.maskbits.ResponseCode`.

    Examples
    --------
    ::

        >>> parse_identifier(1315072)
        (5, 17, <ResponseCode.COMMAND_ACCEPTED: 0>)
        >>> parse_identifier(1315074)
        (5, 17, <ResponseCode.INVALID_TRAJECTORY: 2>)

    """

    identifier_bin = format(identifier, '029b')

    positioner_id = int(identifier_bin[0:11], 2)
    command_id = int(identifier_bin[11:21], 2)
    response_code = int(identifier_bin[21:], 2)

    response_flag = ResponseCode(response_code)

    return positioner_id, command_id, response_flag
