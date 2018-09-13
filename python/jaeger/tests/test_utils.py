#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-11
# @Filename: test_utils.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-09-13 14:31:43

import numpy
import pytest

import jaeger.utils


@pytest.mark.parametrize('dtype, byteorder, result',
                         [(numpy.uint32, '>', '>u4'),
                          (numpy.uint16, '<', '<u2'),
                          (numpy.int16, 'big', '>i2'),
                          (numpy.int32, 'little', '<i4'),
                          ('u4', 'big', '>u4'),
                          ('<u4', '>', '<u4'),
                          ('=u4', 'little', None)])
def test_get_dtype_str(dtype, byteorder, result):

    if result is None:
        with pytest.raises(ValueError):
            jaeger.utils.get_dtype_str(dtype, byteorder=byteorder)
    else:
        assert jaeger.utils.get_dtype_str(dtype, byteorder=byteorder) == result


@pytest.mark.parametrize('value, dtype, byteorder, result',
                         [(5, numpy.uint32, '>', b'\x00\x00\x00\x05'),
                          (5, numpy.uint32, '<', b'\x05\x00\x00\x00'),
                          (5, numpy.uint16, '>', b'\x00\x05')])
def test_int_to_bytes(value, dtype, byteorder, result):

    assert jaeger.utils.int_to_bytes(value, dtype=dtype, byteorder=byteorder) == result


@pytest.mark.parametrize('bytes, dtype, byteorder, result',
                         [(b'\x00\x00\x00\x05', numpy.uint32, '>', 5),
                          (b'\x05\x00\x00\x00', numpy.uint32, '<', 5),
                          (b'\x00\x05', numpy.uint16, '>', 5)])
def test_bytes_to_int(bytes, dtype, byteorder, result):

    assert jaeger.utils.bytes_to_int(bytes, dtype=dtype, byteorder=byteorder) == result


@pytest.mark.parametrize('positioner_id, command_id, result',
                         [(5, 17, 1315072), (450, 5, 117966080)])
def test_get_identifier(positioner_id, command_id, result):

    assert jaeger.utils.get_identifier(positioner_id, command_id) == result


@pytest.mark.parametrize('identifier, result',
                         [(1315072, (5, 17, 0)), (117966081, (450, 5, 1))])
def test_get_identifier(identifier, result):

    positioner_id, command_id, response_flag = jaeger.utils.parse_identifier(identifier)

    assert positioner_id == result[0]
    assert command_id == result[1]
    assert response_flag.value == result[2]
