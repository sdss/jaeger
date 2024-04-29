#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2021-11-14
# @Filename: test_fvc.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)


import pathlib

from typing import Sequence

import numpy
import polars
import pytest
from astropy.io import fits

import jaeger
from jaeger.fvc import FVC
from jaeger.target.schemas import FIBRE_DATA_SCHEMA

from . import check_fps_calibrations_version


def get_data_from_proc_fimg(path: pathlib.Path):
    """Returns the fibre data from a FVC file and offsets."""

    hdul = fits.open(str(path))

    fd_fits = hdul["FIBERDATA"].data
    fd = polars.DataFrame({col: fd_fits[col].tolist() for col in fd_fits.names})

    schema = {}
    for col in fd.columns:
        if col in FIBRE_DATA_SCHEMA:
            schema[col] = FIBRE_DATA_SCHEMA[col]
        else:
            schema[col] = fd[col].dtype

    fd = fd.cast(schema).fill_nan(None).sort(["positioner_id", "fibre_type"])

    off_fits = hdul["OFFSETS"].data
    off = polars.DataFrame({col: off_fits[col].tolist() for col in off_fits.names})

    return fd, off.sort("positioner_id")


@pytest.fixture()
def get_fimg_paths():
    """Returns the paths of the fimg, proc-fimg file and calibration fimg."""

    fcam_dir = pathlib.Path(__file__).parent / "data" / "fcam"

    return (
        fcam_dir / "60428/fimg-fvc1n-0027.fits",
        fcam_dir / "60428/proc-fimg-fvc1n-0027.fits",
        fcam_dir / "calib/medComb.fits",
    )


async def test_fvc():
    fvc = FVC("APO")
    assert fvc.command is None


async def test_get_fibre_data_fcam(get_fimg_paths: Sequence[pathlib.Path]):
    _, proc_fimg_path, _ = get_fimg_paths

    fibre_data, *_ = get_data_from_proc_fimg(proc_fimg_path)
    assert fibre_data.height == 1500
    assert fibre_data["assigned"].dtype == polars.Boolean


async def test_fvc_processing(
    get_fimg_paths: Sequence[pathlib.Path],
    monkeypatch: pytest.MonkeyPatch,
):
    check_fps_calibrations_version()

    fimf_path, proc_fimg_path, calib_fimg_path = get_fimg_paths

    monkeypatch.setitem(jaeger.config["fvc"], "dark_image", calib_fimg_path)

    fvc = FVC("APO")

    fibre_data, offsets = get_data_from_proc_fimg(proc_fimg_path)

    measured = (
        fibre_data.filter(polars.col.fibre_type == "Metrology")
        .select(
            polars.col.positioner_id,
            polars.col.fibre_type,
            polars.selectors.ends_with("_measured"),
        )
        .sort("positioner_id")
    )

    # Nullify the wok measured columns since that's how the FVC would see it.
    fibre_data = fibre_data.with_columns(
        xwok_measured=polars.lit(None, dtype=polars.Float64),
        ywok_measured=polars.lit(None, dtype=polars.Float64),
        zwok_measured=polars.lit(None, dtype=polars.Float64),
    )

    # Get the positioner alpha/beta reported as a dict and array.
    positioner_coords = {}
    reported_positions = numpy.zeros((500, 3), dtype=numpy.float64)
    for irow, row in enumerate(offsets.rows(named=True)):
        pid = row["positioner_id"]
        positioner_coords[pid] = [row["alpha_reported"], row["beta_reported"]]
        reported_positions[irow, :] = (pid, row["alpha_reported"], row["beta_reported"])

    fvc.process_fvc_image(
        fimf_path,
        positioner_coords,
        fibre_data=fibre_data,
        centroid_method="nudge",
    )

    assert fvc.fitrms is not None and fvc.fitrms > 0.05 and fvc.fitrms < 0.06

    assert fvc.fibre_data is not None

    fvc_met_fdata = (
        fvc.fibre_data.filter(polars.col.fibre_type == "Metrology")
        .select(["positioner_id", "xwok_measured", "ywok_measured"])
        .sort("positioner_id")
    )

    numpy.testing.assert_allclose(
        fvc_met_fdata["xwok_measured"].to_numpy(),
        measured["xwok_measured"].to_numpy(),
        atol=1e-5,
    )

    # Run calculate_offsets()
    fvc.calculate_offsets(reported_positions)

    assert fvc.offsets is not None

    offsets_proc = offsets.sort("positioner_id")
    offsets_fvc = fvc.offsets.clone().sort("positioner_id")

    numpy.testing.assert_allclose(
        offsets_proc["alpha_offset_corrected"].to_numpy(),
        offsets_fvc["alpha_offset_corrected"].to_numpy(),
        atol=1e-5,
    )
