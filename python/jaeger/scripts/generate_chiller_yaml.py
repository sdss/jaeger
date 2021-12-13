#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-12-13
# @Filename: generate_chiller_yaml.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import sys

import numpy
import pandas
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


def generate_chiller_yaml(variables_files: str):
    """Generates a YAML file for Drift with all the chiller variables from the CSV."""

    variables = pandas.read_csv(variables_files)

    data = yaml.load(BASE, yaml.SafeLoader)
    devices = {}

    for _, row in variables.iterrows():
        address = row.Address - 1
        devices[row.Name.upper()] = {
            "address": address,
            "units": row.Unit if isinstance(row.Unit, str) else "",
            "category": "chiller",
            "description": row.Description,
        }

        if row.Scale != 1:
            devices[row.Name.upper()].update(
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
