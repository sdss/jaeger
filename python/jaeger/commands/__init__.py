#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-08-27
# @Filename: __init__.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-09-13 23:25:12

# flake8: noqa
# isort:skip_file

import enum


class CommandID(enum.IntEnum):
    """IDs associated with commands."""

    GET_ID = 1
    GET_STATUS = 3

    def get_command(self):
        """Returns the class associated with this command."""

        return COMMAND_LIST[self]


from .bootloader import *
from .commands import *
from .status import *


COMMAND_LIST = {1: GetID}
