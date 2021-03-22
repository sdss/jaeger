#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2019-12-18
# @Filename: create_setup.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

# This is a temporary solution for the fact that pip install . fails with
# poetry when there is no setup.py and an extension needs to be compiled.
# See https://github.com/python-poetry/poetry/issues/1516. Running this
# script creates a setup.py filled out with information generated by
# poetry when parsing the pyproject.toml.

# type: ignore

import os
import sys
from distutils.version import StrictVersion


# If there is a global installation of poetry, prefer that.
lib = os.path.expanduser("~/.poetry/lib")
vendors = os.path.join(lib, "poetry", "_vendor")
current_vendors = os.path.join(
    vendors, "py{}".format(".".join(str(v) for v in sys.version_info[:2]))
)

sys.path.insert(0, lib)
sys.path.insert(0, current_vendors)

try:
    try:
        from poetry.core.factory import Factory
        from poetry.core.masonry.builders.sdist import SdistBuilder
    except (ImportError, ModuleNotFoundError):
        from poetry.masonry.builders.sdist import SdistBuilder
        from poetry.factory import Factory
    from poetry.__version__ import __version__
except (ImportError, ModuleNotFoundError) as ee:
    raise ImportError(
        f"install poetry by doing pip install poetry to use this script: {ee}"
    )


# Generate a Poetry object that knows about the metadata in pyproject.toml
factory = Factory()
poetry = factory.create_poetry(os.path.dirname(__file__))

# Use the SdistBuilder to genrate a blob for setup.py
if StrictVersion(__version__) >= StrictVersion("1.1.0b1"):
    sdist_builder = SdistBuilder(poetry, None)
else:
    sdist_builder = SdistBuilder(poetry, None, None)

setuppy_blob = sdist_builder.build_setup()

with open("setup.py", "wb") as unit:
    unit.write(setuppy_blob)
    unit.write(b"\n# This setup.py was autogenerated using poetry.\n")
