#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-12-13
# @Filename: generate_chiller_yaml.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import sys

import polars
import yaml


BASE = """
address: 10.25.1.162
port: 1111
modules:
    CHILLER:
        mode: holding_register
        channels: -1
        description: APO chiller status variables
        devices: {}
"""


NAME_CONV = {
    "STATUS_FLUID_FLOW_SV": "STATUS_FLUID_FLOW",
    "USER_FLOW_SP_GPM_SV": "FLOW_USER_SETPOINT",
    "STATUS_DISPLAY_VALUE_SV": "DISPLAY_VALUE",
    "USER_SETPOINT_SV": "TEMPERATURE_USER_SETPOINT",
    "STATUS_AMBIENT_AIR_SV": "STATUS_AMBIENT_AIR",
}


def generate_chiller_yaml(variables_files: str):
    """Generates a YAML file for Drift with all the chiller variables from the CSV."""

    variables = polars.read_csv(variables_files)

    data = yaml.load(BASE, yaml.SafeLoader)
    devices = {}

    for row in variables.iter_rows(named=True):
        address = row["Address"] - 1

        name = row["Name"].upper()
        if name in NAME_CONV:
            name = NAME_CONV[name]

        devices[name] = {
            "address": address,
            "units": row["Unit"] if isinstance(row["Unit"], str) else "",
            "category": "chiller",
            "description": row["Description"],
        }

        if row["Scale"] != 1:
            devices[name].update(
                {
                    "adaptor": "proportional",
                    "adaptor_extra_params": [0.1],
                }
            )

    data["modules"]["CHILLER"]["devices"] = devices

    with open("chiller.yaml", "w") as f:
        yaml.dump(data, f, yaml.SafeDumper)


if __name__ == "__main__":
    generate_chiller_yaml(sys.argv[1])
