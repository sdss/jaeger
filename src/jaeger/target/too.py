#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2024-04-30
# @Filename: too.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from typing import TYPE_CHECKING

from jaeger import config, log


if TYPE_CHECKING:
    from jaeger.target.design import Design


def add_too_to_design(design: Design):
    """Replaces design targets with ToO targets according to the configuration setting.

    All the modification is done in place. The `.Design.target_data` dictionary
    entries for the replaced targets are modified.

    Parameters
    ----------
    design
        The design object to modify.

    """

    too_config = config["configuration"].get("targets_of_opportunity", {})

    if not too_config.get("replace", False):
        log.info("ToO replacement is disabled.")
        return

    log.info("Running ToO replacement.")
