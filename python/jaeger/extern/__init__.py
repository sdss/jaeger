#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-15
# @Filename: __init__.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)
#
# @Last modified by: José Sánchez-Gallego (gallegoj@uw.edu)
# @Last modified time: 2018-09-15 19:44:45

import os
import sys


# If asyncioActor is not available globally, uses the submodule
try:
    import asyncioActor
except ImportError:
    sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__),
                                                     'asyncioActor/python')))


# Forces the use of python-can from submodule
sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), 'python-can')))
