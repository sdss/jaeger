#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-11-11
# @Filename: utils.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)


RH0 = 0.0               # Humidity linear calibration
RHs = 100.0 / 32767.0   # = 100/(2^15-1)
T0 = -30.0              # Temperature linear calibration
Ts = 100.0 / 32767.0      # = 100/(2^15-1)


def convert_ee_rh(raw_value):
    """Returns E+E sensor relative humidity (RH) from a raw register value.

    Range is 0-100%.

    """

    return RH0 + RHs * float(raw_value)


def convert_ee_temp(raw_value):
    """Returns E+E sensor temperature from a raw register value.

    Range is -30C to +70C.

    """

    return T0 + Ts * float(raw_value)


def convert_rtd(raw_value):
    """Converts platinum RTD (resistance thermometer) output to degrees C.

    The temperature resolution is 0.1C per ADU, and the temperature range is
    -273C to +850C. The 16-bit digital number wraps below 0C to  216-1 ADU.
    This handles that conversion.

    Parameters
    ----------
    raw_value : int
        The register raw value from the sensor.

    """

    tempRes = 0.1                      # Module resolution is 0.1C per ADU
    tempMax = 850.0                    # Maximum temperature for a Pt RTD in deg C
    wrapT = tempRes * ((2.0**16) - 1)  # ADU wrap at <0C to 2^16-1

    temp = tempRes * raw_value
    if temp > tempMax:
        temp -= wrapT

    return temp
