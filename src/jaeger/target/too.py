#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2024-04-30
# @Filename: too.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

from __future__ import annotations

import os

from typing import TYPE_CHECKING, Any

import numpy
import polars
import scipy
from astropy.time import Time

from coordio import defaults, radec2wokxy

from jaeger import config, log
from jaeger.target.coordinates import apply_proper_motions
from jaeger.target.schemas import TARGET_DATA_SCHEMA
from jaeger.target.tools import get_tonight_targets, get_wok_data


if TYPE_CHECKING:
    from jaeger.target.design import Design


__all__ = ["add_targets_of_opportunity_to_design"]


def add_targets_of_opportunity_to_design(design: Design):
    """Replaces design targets with ToO targets according to the configuration setting.

    All the modification is done in place. The `.Design.target_data` dictionary
    entries for the replaced targets are modified.

    Parameters
    ----------
    design
        The design object to modify.

    """

    too_config = config["configuration"].get("targets_of_opportunity", {})

    if not too_config or not too_config.get("replace", False):
        log.info("[ToO]: replacement is disabled.")
        return

    log.info("Running ToO replacement.")

    too_file = os.path.expanduser(os.path.expandvars(too_config["path"]))

    try:
        log.debug(f"[ToO]: Reading targets from {too_file}.")
        too_targets = polars.read_parquet(too_file)
    except FileNotFoundError:  # pragma: no cover
        log.error("Failed reading ToO file.")
        return

    # Retrieve the ToO targets for this field.
    field_id = design.field.field_id
    too_targets_field = too_targets.filter(polars.col.field_id == field_id)
    log.debug(f"[ToO]: found {len(too_targets_field)} targets for field {field_id}.")

    if len(too_targets_field) == 0:  # pragma: no cover
        log.info("[ToO]: no targets available for this field.")
        return

    # Get tonight's already observed targets.
    observed_targets = get_tonight_targets()

    # Filter out the targets that have already been observed.
    # NOTE: this allows only one observation of the ToO per night, even if
    # the ToO requests n_exposures > 1.
    too_targets_field = too_targets_field.filter(
        ~polars.col.catalogid.is_in(observed_targets["catalogid"])
    )

    if len(too_targets_field) == 0:  # pragma: no cover
        log.info("[ToO]: all ToO targets for this field have already been observed.")
        return

    log.debug(f"[ToO]: {len(too_targets_field)} targets have not been observed.")

    # Filter out ToOs that are not valid for this design mode.
    design_mode = design.design.design_mode_label

    bn_col = f"bn_{design_mode.lower()}_valid"
    mag_lim_col = f"mag_lim_{design_mode.lower()}_valid"
    too_cols = too_targets_field.columns
    if bn_col not in too_cols or mag_lim_col not in too_cols:
        log.warning("[ToO]: missing columns for design mode validation.")
        return

    too_targets_dm = too_targets_field.filter(
        polars.col(bn_col) & polars.col(mag_lim_col)
    )
    if len(too_targets_dm) == 0:  # pragma: no cover
        log.info("[ToO]: no valid ToO targets for this design mode.")
        return
    log.debug(f"[ToO]: {len(too_targets_dm)} targets are valid for this design mode.")

    # Create a frame with the design targets that could be replaced.
    repl_design_targets = filter_targets(design.target_data)

    if len(repl_design_targets) == 0:  # pragma: no cover
        log.info("[ToO]: no replaceable targets in this design.")
        return

    log.debug(
        f"[ToO]: {len(repl_design_targets)} targets from design "
        f"{design.design_id} can be replaced."
    )

    # Match ToO targets to nearby holes. The returned frame has one entry per
    # ToO target and valid hole.
    too_to_hole = match_too_to_hole(
        design,
        too_targets_dm,
        repl_design_targets["hole_id"].to_list(),
    )

    if len(too_to_hole) == 0:  # pragma: no cover
        log.info("[ToO]: no ToO targets can be matched to holes.")
        return

    n_too_to_hole = too_to_hole["too_id"].unique().len()
    log.debug(f"[ToO]: {n_too_to_hole} ToO targets matched to holes.")

    # Join ToO hole data to the target data.
    too_to_hole = too_to_hole.join(
        design.target_data[["hole_id", "priority"]],
        on="hole_id",
    )

    too_to_add: list[dict[str, Any]] = []
    max_replacements = too_config.get("max_replacements", 1)
    for too_id in too_to_hole["too_id"].unique().to_list():
        too_entry = (
            too_to_hole.filter(polars.col.too_id == too_id)
            .sort("priority", descending=True)
            .head(1)
        ).to_dicts()[0]

        fibre_type = too_entry["fiber_type"]
        too_to_add.append(
            {
                "catalogid": too_entry["catalogid"],
                "ra": too_entry["ra"],
                "dec": too_entry["dec"],
                "pmra": too_entry["pmdec"],
                "pmdec": too_entry["pmra"],
                "epoch": too_entry["epoch"],
                "delta_ra": too_entry["delta_ra"],
                "delta_dec": too_entry["delta_dec"],
                "can_offset": too_entry["can_offset"],
                "lambda_eff": defaults.INST_TO_WAVE[fibre_type.capitalize()],
                "g": too_entry["g_mag"],
                "i": too_entry["i_mag"],
                "z": too_entry["z_mag"],
                "r": too_entry["r_mag"],
                "h": too_entry["h_mag"],
                "gaia_g": too_entry["gaia_g_mag"],
                "optical_prov": too_entry["optical_prov"],
                "hole_id": too_entry["hole_id"],
                "fibre_type": fibre_type.upper(),
                "design_mode": design_mode,
                "is_too": True,
            }
        )

        if len(too_to_add) >= max_replacements:
            break

    # Create new data frame with ToO data to add to target_data.
    new_targets = polars.DataFrame(too_to_add, schema=TARGET_DATA_SCHEMA)

    # Store replaced targets.
    design.replaced_target_data = design.target_data.filter(
        polars.col.hole_id.is_in(new_targets["hole_id"])
    )

    # Trim the replaced targets from the target data.
    target_data = design.target_data.filter(
        polars.col.hole_id.is_in(new_targets["hole_id"]).not_()
    )

    # Add ToOs.
    design.target_data = polars.concat([target_data, new_targets])

    for row in new_targets.to_dicts():
        too_id = too_to_hole.filter(polars.col.hole_id == row["hole_id"])[0, "too_id"]
        log.info(f"[ToO]: associated too_id={too_id} with hole {row['hole_id']}.")


def filter_targets(target_data: polars.DataFrame):
    """Returns a list of targets for a design that can be replaced with ToO targets."""

    valid_targets = target_data.clone()

    too_config = config["configuration"].get("targets_of_opportunity", {})

    # Only select targets that match the valid categories.
    categories: list[str] | None = too_config.get("categories", None)
    if categories is not None:
        valid_targets = valid_targets.filter(polars.col.category.is_in(categories))

    # Remove targets that match the exclude_design_modes.
    exclude_dms: list[str] | None = too_config.get("exclude_design_modes", None)
    if exclude_dms is not None:
        exprs = [polars.col.design_mode.str.contains(dm).not_() for dm in exclude_dms]
        valid_targets = valid_targets.filter(*exprs)

    # If we have a list of priorities to follow, exclude anything that's below
    # the minimum acceptable priority.
    priorities: int | list[int] | None = too_config.get("minimum_priority", None)
    if priorities is not None:
        min_priority = min(priorities) if isinstance(priorities, list) else priorities
        valid_targets = valid_targets.filter(polars.col.priority >= min_priority)

    return valid_targets


def match_too_to_hole(
    design: Design,
    too_targets: polars.DataFrame,
    hole_ids: list[str] | None,
):
    """Matches each ToO with the holes that are close enough for assignment.

    Parameters
    ----------
    design
        The `.Design` object, which contains the field information.
    too_targets
        A data frame with the ToO targets.
    hole_ids
        A list of hole IDs to consider. If `None`, all holes are considered.

    """

    now = Time.now()

    assert design.field is not None

    observatory = config["observatory"]

    # Patrol radius of each robot in mm. Trim the radius by 5% to avoid edge cases.
    patrol_radius = 0.95 * (defaults.ALPHA_LEN + defaults.BETA_LEN)

    # First get the ra/dec IRCS coordinates for the current epoch. We do it
    # independently of radec2wokxy because the pmra/pmdec data may be patchy.
    too_radec_epoch = apply_proper_motions(too_targets)

    # Propagate the ToO coordinates to wok coordinates. We do not populate the pmra
    # and pmdec columns because the input coordinates are already at the current epoch.
    too_xwok, too_ywok, *_ = radec2wokxy(
        too_radec_epoch["ra"].to_numpy(),
        too_radec_epoch["dec"].to_numpy(),
        now.jd,
        too_targets["fiber_type"].str.to_titlecase().to_numpy(),
        design.field.racen,
        design.field.deccen,
        design.field.position_angle,
        observatory,
        now.jd,
    )

    too_targets = too_targets.with_columns(
        ra_epoch=too_radec_epoch["ra"],
        dec_epoch=too_radec_epoch["dec"],
        xwok=polars.Series(too_xwok, dtype=polars.Float64),
        ywok=polars.Series(too_ywok, dtype=polars.Float64),
    )

    wok_data = get_wok_data(observatory)

    # Limit the wok data to the holes we are interested in.
    if hole_ids is not None:
        wok_data = wok_data.filter(polars.col.holeID.is_in(hole_ids))

    # Calculate disances between ToO and wok holes.
    dist = scipy.spatial.distance.cdist(
        too_targets[["xwok", "ywok"]].to_numpy(),
        wok_data[["xWok", "yWok"]].to_numpy(),
    )

    # For each ToO, get a list of the holes that are close enough.
    holes: list[list[str]] = []
    for itarget in range(len(too_targets)):
        valid_holes_idx = numpy.where(dist[itarget] < patrol_radius)[0]
        valid_holes = wok_data[valid_holes_idx.tolist(), "holeID"]
        holes.append(valid_holes.to_list())

    # Add the list of holes to each ToO and explode so that we get one row
    # per ToO and valid hole ID.
    too_targets = too_targets.with_columns(valid_hole_id=polars.Series(holes))
    too_targets = too_targets.explode("valid_hole_id")

    # Remove targets without any nearby holes.
    too_targets = too_targets.filter(polars.col.valid_hole_id.is_not_null())

    # Join with the wok data and select the relevant columns.
    too_wok_data = too_targets.join(
        wok_data,
        left_on="valid_hole_id",
        right_on="holeID",
    )
    too_wok_data = too_wok_data.select(
        polars.col(too_targets.columns).exclude("valid_hole_id"),
        polars.col.positionerID.alias("positioner_id"),
        polars.col.valid_hole_id.alias("hole_id"),
    )

    return too_wok_data
