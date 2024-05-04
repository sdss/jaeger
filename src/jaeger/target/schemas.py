#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2024-04-28
# @Filename: schemas.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

from typing import Mapping

import numpy
import polars
import polars.type_aliases


__all__ = ["CONFSUMMARY_FIBER_MAP_SCHEMA", "FIBRE_DATA_SCHEMA", "CONFIGURATION_SCHEMA"]


CONFSUMMARY_FIBER_MAP_SCHEMA = [
    ("positionerId", numpy.int16, -999),
    ("holeId", "U7", ""),
    ("fiberType", "U10", ""),
    ("assigned", numpy.int16, 0),
    ("on_target", numpy.int16, 0),
    ("valid", numpy.int16, 0),
    ("decollided", numpy.int16, 0),
    ("xwok", numpy.float64, -999.0),
    ("ywok", numpy.float64, -999.0),
    ("zwok", numpy.float64, -999.0),
    ("xFocal", numpy.float64, -999.0),
    ("yFocal", numpy.float64, -999.0),
    ("alpha", numpy.float32, -999.0),
    ("beta", numpy.float32, -999.0),
    ("racat", numpy.float64, -999.0),
    ("deccat", numpy.float64, -999.0),
    ("pmra", numpy.float32, -999.0),
    ("pmdec", numpy.float32, -999.0),
    ("parallax", numpy.float32, -999.0),
    ("ra", numpy.float64, -999.0),
    ("dec", numpy.float64, -999.0),
    ("ra_observed", numpy.float64, -999.0),
    ("dec_observed", numpy.float64, -999.0),
    ("alt_observed", numpy.float64, -999.0),
    ("az_observed", numpy.float64, -999.0),
    ("lambda_design", numpy.float32, -999.0),
    ("lambda_eff", numpy.float32, -999.0),
    ("coord_epoch", numpy.float32, -999.0),
    ("spectrographId", numpy.int16, -999),
    ("fiberId", numpy.int16, -999),
    ("mag", numpy.dtype(("<f4", (5,))), [-999.0] * 5),
    ("optical_prov", "U30", ""),
    ("bp_mag", numpy.float32, -999.0),
    ("gaia_g_mag", numpy.float32, -999.0),
    ("rp_mag", numpy.float32, -999.0),
    ("h_mag", numpy.float32, -999.0),
    ("catalogid", numpy.int64, -999),
    ("carton_to_target_pk", numpy.int64, -999),
    ("cadence", "U100", ""),
    ("firstcarton", "U100", ""),
    ("program", "U100", ""),
    ("category", "U100", ""),
    ("sdssv_boss_target0", numpy.int64, 0),
    ("sdssv_apogee_target0", numpy.int64, 0),
    ("delta_ra", numpy.float64, 0.0),
    ("delta_dec", numpy.float64, 0.0),
]

SchemaType = Mapping[
    polars.type_aliases.ColumnNameOrSelector | polars.type_aliases.PolarsDataType,
    polars.type_aliases.PolarsType,
]

FIBRE_DATA_SCHEMA: SchemaType = {
    "index": polars.Int32,
    "positioner_id": polars.Int32,
    "fibre_type": polars.String,
    "hole_id": polars.String,
    "fibre_id": polars.Int32,
    "site": polars.String,
    "assigned": polars.Boolean,
    "reassigned": polars.Boolean,
    "valid": polars.Boolean,
    "on_target": polars.Boolean,
    "disabled": polars.Boolean,
    "offline": polars.Boolean,
    "deadlocked": polars.Boolean,
    "decollided": polars.Boolean,
    "dubious": polars.Boolean,
    "wavelength": polars.Float32,
    "fiberId": polars.Float32,
    "catalogid": polars.Int64,
    "ra_icrs": polars.Float64,
    "dec_icrs": polars.Float64,
    "pmra": polars.Float32,
    "pmdec": polars.Float32,
    "parallax": polars.Float32,
    "coord_epoch": polars.Float32,
    "delta_ra": polars.Float32,
    "delta_dec": polars.Float32,
    "ra_offset": polars.Float64,
    "dec_offset": polars.Float64,
    "ra_epoch": polars.Float64,
    "dec_epoch": polars.Float64,
    "ra_observed": polars.Float64,
    "dec_observed": polars.Float64,
    "alt_observed": polars.Float64,
    "az_observed": polars.Float64,
    "xfocal": polars.Float64,
    "yfocal": polars.Float64,
    "xwok": polars.Float64,
    "ywok": polars.Float64,
    "zwok": polars.Float64,
    "xwok_kaiju": polars.Float64,
    "ywok_kaiju": polars.Float64,
    "zwok_kaiju": polars.Float64,
    "xwok_measured": polars.Float64,
    "ywok_measured": polars.Float64,
    "zwok_measured": polars.Float64,
    "alpha": polars.Float64,
    "beta": polars.Float64,
}


CONFIGURATION_SCHEMA: SchemaType = FIBRE_DATA_SCHEMA.copy()
CONFIGURATION_SCHEMA.update(
    {
        "configuration_id": polars.Int32,
        "robostrategy_run": polars.String,
        "fps_calibrations_version": polars.String,
        "jaeger_version": polars.String,
        "coordio_version": polars.String,
        "kaiju_version": polars.String,
        "design_id": polars.Int32,
        "field_id": polars.Int32,
        "focal_scale": polars.Float32,
        "instruments": polars.List(polars.String),
        "configuration_epoch": polars.Float32,
        "obstime": polars.Float32,
        "MJD": polars.Int32,
        "observatory": polars.String,
        "temperature": polars.Float32,
        "ra_cen": polars.Float64,
        "dec_cen": polars.Float64,
        "pa": polars.Float32,
        "is_dithered": polars.Boolean,
        "parent_configuration": polars.Int32,
        "dither_radius": polars.Float32,
        "cloned_from": polars.Int32,
        "lambda_design": polars.Float32,
        "carton_to_target_pk": polars.Int64,
        "cadence": polars.String,
        "firstcarton": polars.String,
        "program": polars.String,
        "category": polars.String,
        "sloan_g_mag": polars.Float32,
        "sloan_r_mag": polars.Float32,
        "sloan_i_mag": polars.Float32,
        "sloan_z_mag": polars.Float32,
        "optical_prov": polars.String,
        "gaia_bp_mag": polars.Float32,
        "gaia_rp_mag": polars.Float32,
        "gaia_g_mag": polars.Float32,
        "tmass_h_mag": polars.Float32,
    }
)
