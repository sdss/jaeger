# Changelog

## 1.10.2 - August 24, 2025

### 🔧 Fixed

* Replace nulls in `can_offset` with `true` before calling the offset function.


## 1.10.1 - August 22, 2025

### ✨ Improved

* Explicitly require `sdss-coordio>=1.17.0` and `sdss-clu>=2.5.3`.
* Use `check_valid_offset=True` in `object_offset()` calls and remove duplicate code.


## 1.10.0 - August 7, 2025

### 🔥 Breaking change

* Until Kaiju can be properly updated to compiles on Python 3.11+, `jaeger` will only support 3.10.

### ✨ Improved

* Add `too_id` and `too_program` to configuration summary files.

### 🔧 Fixed

* Prevent repeated positioners appearing in the permanently disabled list.
* Improved handling of FVC images with no light.
* Include `too_id` and `too_program` in output of `configuration_to_dataframe()`.


## 1.9.1 - February 24, 2025

### ✨ Improved

* [#210](https://github.com/sdss/jaeger/pull/210) Move the default values for `offset_min_skybrightness` and `safety_factor` to the configuration file. The default values are now `offset_min_skybrightness=0` and `safety_factor=None`.
* Add field `disabled` to the summary file for robots that are disabled or offline.
* Bump `sdss-coordio` to `>=1.14.0`.

### 🏷️ Changed

* Set the default values for `scale_fudge_factor` to 1.

### 🔧 Fixed

* Fixed a crash loading a configuration when a ToO can be found in the field but cannot be matched to any valid hole ID.
* Move the check for invalid offsets to the right place.
* Correctly use `offset_min_skybrightness=0` and `safety_factor=None`.
* `assigned=0` only when a robot does not have an assigned target in a design, not when the alpha/beta coordinates are invalid or the target offsets are invalid. For those cases `valid=1`.
* Set `decollided=1` for robots that are decollided or un-deadlocked.


## 1.9.0 - January 22, 2025

### 🚀 New

* [#208](https://github.com/sdss/jaeger/pull/208) Add actor command `calibrations reset-offsets` to zero the alpha/beta offsets in one or more positioners.

### ✨ Improved

* Improved handling of sextant controllers.
* `configuration random` now calculates paths only once.

### 🔧 Fixed

* Prevent the ToO code from selecting the same target for two different positioners.
* Fix a case when the configuration fails to retrieve the trajectory paths because there are no robots that need updated coordinates.
* Pin `pymodbus` to 3.7.x.


## 1.8.0 - October 24, 2024

### 🚀 New

* Drop `poetry` and use `uv` for project management. Update GitHub workflows.

### ✨ Improved

* [#207](https://github.com/sdss/jaeger/pull/207) Update the call to determine target offsets.
* [#206](https://github.com/sdss/jaeger/pull/206) Added flag `--sea-anemone` to `jaeger configuration random`.

### ⚙️ Engineering

* Relax Python requirement to `^3.10,<4`.


## 1.7.7 - August 9, 2024

### ✨ Improved

* Add ``rot_ref_angle`` option to the FVC command to set the reference rotator angle for fiducial fitting.

### 🔧 Fixed

* Set the configuration epoch for `DitheredConfiguration` as the epoch of the parent.
* Use the configuration focal scale in all calls to `icrs_from_positioner_dataframe()`.

### 🚑 Hotfix

* [#205](https://github.com/sdss/jaeger/pull/205) Added a hotfix for an issue caused when the back-illuminated fibres don't turn on during and FVC loop (cause under investigation). When light is not detected in the FVC image the code will retry up to three times, each time turning the LEDs off and then on again.

### ⚙️ Engineering

* Add back `pyarrow` dependency.


## 1.7.6 - July 2, 2024

### 🏷️ Changed

* [#204](https://github.com/sdss/jaeger/pull/204) Replace the old `confSummary_test` paths which have not become the default. When a configuration or FVC writes a configuration file, it does so to `$SDSSCORE_DIR` with the new formatting (i.e., using thousand and hundred groupings). Additionally, and at least for now, it also writes the same file to `$SDSSCORE_LEGACY_DIR` with the old format (only hundred groupings).

### ⚙️ Engineering

* Upgrade `sdsstools` to 1.7.1 with support for Numpy 2.0.
* Upgrade `polars` to 1.0.0.


## 1.7.5 - June 15, 2024

### 🏷️ Changed

* Disabled `alert_fluid_temperature`.


## 1.7.4 - May 30, 2024

### ✨ Improved

* Bumped `kaiju` to 1.4.0 with speed-up improvements.


## 1.7.3 - May 26, 2024

### 🔧 Fixed

* Fix import of `os` in `target/tools.py` which was affecting the loading of cloned configurations and possibly other features.


## 1.7.2 - May 24, 2024

### ✨ Improved

* [#203](https://github.com/sdss/jaeger/pull/203) Implementation of targets of opportunity. When a `Design` is created (and unless `use_targets_of_opportunity=False`) design targets can be replaced with ToOs from a dump file (defaults to `$TOO_DATA_DIR/current`). The replacement options are managed via `configuration.targets_of_opportunity` which accepts the following options

  ```yaml
  configuration:
    targets_of_opportunity:
      replace: true
      path: $TOO_DATA_DIR/current
      exclude_design_modes: ['^.+?_eng$', '^.+?_rm_?.*$']
      max_replacements: 2
      categories: ['science']
      minimum_priority: [6000, 3000, 0]
  ```

  Initially disabled in the configuration.

### 🔧 Fixed

* Fixed FVC `apply_corrections()` only setting the values of robots with invalid transformations.


## 1.7.1 - May 8, 2024

### ✨ Improved

* Use `positionerToWok` and `wokToPositioner` functions from `sdss-coordio 1.11.0` which allow to convert wok to and from positioner coordinates as an array (all holes at the same time), which very significantly improves `Design` creation time.
* Enabled chiller fault alarms.


## 1.7.0 - April 29, 2024

This is marked as a minor version although it should not have any visible changes, but the codebase has significantly changed and there's potential for regression issues that are better tracked as a clearly different version.

### ⚙️ Engineering

* [#202](https://github.com/sdss/jaeger/pull/202) This started as a quick rewrite of some parts to use `polars` and ended as a mid-to-large refactor of significant parts of the code, especially the `Configuration` and `Assignment` classes.

  The main highlights are:

  * Dropped support for Python 3.9 and extended support up to 3.12. For Python `>=3.11` the `1.4.0b1` version of `kaiju` is used.
  * The code does not use `pandas` anymore, and `polars` data frames are used everywhere. `jaeger` still handles `pandas` dataframes when they are returned by other libraries (mostly from the `FVCTransform` code in `coordio`).
  * The `Configuration` and `Assignment` classes have been completely rewritten. Coordinate transformations code is now mostly in `jaeger.target.coordinates`. The new code should be significantly cleaner and easier to maintain.
  * `AssignmentData*` has been renamed to `Assignment*`.
  * Some modest efficiency improvements to the coordinate transformations in `Assignment`. Before some conversions from ICRS to wok and vice-versa were done on a per-target bases. Now they are doing for all the targets at once, but the bottleneck is still the conversion between wok and positioner (and vice-versa) which has to be done as a loop for each target.
  * Simplified the singleton patter for `FPS`.
  * Significantly extended the test suite. Now `Design`/`Configuration`/`Assignment` and `FVC` are reasonably covered.
  * Added a test database for CI testing.
  * Added a `configuration_to_dataframe` function that generates a `confSummary`-like dataframe that could be saved to `sdsscore` as Parquet (currently not doing that).
  * Added `ra/dec/alt/az_observed` to `confSummary`.
  * Moved all codebase from `python/` to `src/`.


## 1.6.4 - April 29, 2024

### 🔧 Fixed

* Change LCO expected scale factors after IMB modifications: apply only to LCO.


## 1.6.3 - April 1, 2024

### ✨ Improved

* Add `--extra-epoch-delay` to `jaeger configuration load` and `preload`. This parameter adds an extra delay to the configuration epoch. It is mainly used by HAL when it preloads a design ahead of time


## 1.6.2 - February 27, 2024

### 🏷️ Changed

* Change LCO expected scale factors after IMB modifications.


## 1.6.1 - January 15, 2024

### ✨ Improved

* Reset cherno offsets when a design gets loaded.
* Use `coordio` 1.9.2 with `sdss-sep`.

### 🔧 Fixed

* Fix some warnings and retries during broadcasts when a robot has been marked offline.
* Fix future deprecation in Pandas by downcasting columns in the assignment fibre data.
* Fix docs builds.


## 1.6.0 - December 22, 2023

### 🔥 Breaking changes

* Deprecate Python 3.8.

### 🏷️ Changed

* Use `sdsstools` version of `yanny`.

### 🔧 Fixed

* Ensure that `fvc_image_path`` is populated.


## 1.5.0 - September 29, 2023

### 🚀 New

* [#200](https://github.com/sdss/jaeger/issues/200) Save copy of `confSummary` files to `$SDSSCORE_TEST_DIR`, if present. For testing purposes only, for now.
* Modify default ZB orders passed to the FVC transformation and add flag `--polids` to `jaeger fvc loop` to manually set the orders.
* Increased timeout for `jaeger configuration preload`.

### ✨ Improved

* [#199](https://github.com/sdss/jaeger/pull/199) Subtract FVC dark frame during FVC image processing.
* [#200](https://github.com/sdss/jaeger/pull/200) Initial test to update files in `sdsscore_test`.
* [#201](https://github.com/sdss/jaeger/issues/201) Move FVC dark frames to calibration folder where they won't be deleted in the future.
* Updated call to coordio's `object_offset()`.
* Chiller: do not depend on IEB when setting absolute values.

### 🔧 Fixed

* If measured alpha vs reported alpha are on either side of the wrap at 360 deg, then adjust the offset for the FVC loop.
* Update IEB info before calling `write_proc_image()`.

### ⚙️ Engineering

* Lint using `ruff`.


## 1.4.0 - April 15, 2023

### 🚀 New

* Allow to specify the wavelength to use for BOSS and APOGEE fibres when creating a configuration. Using `jaeger configuration load --boss-wavelength` or `--apogee-wavelength` will specify the wavelength to use for atmospheric refraction and affect the positioning of the fibre. The closest valid focal plane model is used in this case. Only assigned fibre of the specified fibre type are affected.

### 🏷️ Changed

* Changed the default value of the safety factor for the offset function to 1.


## 1.3.4 - April 12, 2023

### 🚀 New

* Add flag `--offset-min-skybrightness` to `jaeger configuration load/preload` to set the `coordio.utils.offset_definition` `offset_min_skybrightness` parameters. Defaults to 0.5.

### 🏷️ Changed

* Add a 3 minute timeout for `jaeger configuration preload` (normal configuration load does not time out).
* [COS-103](https://jira.sdss.org/browse/COS-103) Add `locked_alpha` and `locked_beta` keywords when a collision happens.


## 1.3.3 - January 15, 2023

### ✨ Improved

* Add a delay after `STOP_TRAJECTORY` or `SEND_TRAJECTORY_ABORT`. Those commands are issued with `timeout=0` so they complete immediately. It seems that if one sends another command immediately after them the new command times out. It's unclear if that happens because the replies from both commands clog the CAN buffer or because of some issue at the firmware level. Adding a 0.5 second delay to allow positioners to reply seems to fix the issue.


## 1.3.2 - January 10, 2023

### 🔧 Fixed

* Only retry `FPS.update_firmware_version()` if `n_positioners` is not `None`. Otherwise a time out is expected.


## 1.3.1 - January 10, 2023

### ✨ Improved

* [#195](https://github.com/sdss/jaeger/issues/195) Prevent late replies to timed out commands to flood the log/actor window. The errors are now redirected only to the CAN log. Timed out commands are reported unless the command is a broadcast with the number of replies not defined.
* Increase timeout for `FPS.update_status()` and `FPS.update_position()` to 2 seconds.
* Allow `FPS.update_position()`, `FPS.update_status()` and `FPS.update_firmware_version()` to retry once if they time out.


## 1.3.0 - January 2, 2023

### ✨ Improved

* Trajectories now save the snapshot asynchronously, which should save a few seconds at the end of each trajectory.
* Avoids saving trajectory dump file multiple times.
* FVC `proc-` image is saved asynchronously and the command doesn't wait for it to finish writing. The `confSummaryF` file is computed and saved asynchronously.
* `plotFVCResults` in `coordio.transforms` is monkeypatched to run as a task.

### ⚙️ Engineering

* Upgraded to `Drift` 1.0.0 which uses `pymodbus` 3.


## 1.2.1 - December 21, 2022

### 🔧 Fixed

* Require `sdss-kaiju>=1.3.1` which solves a problem with a `shapely` dependency upgrade breaking some plotting.


## 1.2.0 - December 21, 2022

### ✨ Improved

* Output `preloaded_is_cloned` keyword indicating whether a preloaded design is cloned.
* Move the check for whether the FPS is moving before sending a new trajectory to `Tracjectory.send()` and after an `FPS.update_status()` has been issued, which may help with cases in which the FPS is stuck as moving because the status has not been updated.

### 🏷️ Changed

* Call `cherno get-scale --max-age 3600`.
* Changed `max_cloned_time` to 4500 and `max_designs_epoch` to 4 at both APO and LCO.
* Updated fudge factor at APO to 0.99988.
* Use `zbplus2` as default for FVC centroiding at APO.


## 1.2.0b1 - November 7, 2022

### 🚀 New

* [#193](https://github.com/sdss/jaeger/issues/193) Calculate `delta_ra` and `delta_dec` offsets using `coordio`'s `object_offset`.

### ✨ Improved

* `jaeger fvc snapshot` now creates a snapshot based on the FVC-measured positions and alerts if a robots is more than 5 degrees off.

### 🔧 Fixed

* Keep disabled positioners after a power cycle.

### 🏷️ Changed

* Use MDP2 for LCO.


## 1.1.1 - October 20, 2022

### 🔧 Fixed

* Require `kaiju>=1.3.0`, which is needed for MDP2 path planning.


## 1.1.0 - October 19, 2022 (yanked due to missing dependency; all changes available in 1.1.1)

### 🚀 New

* [#191](https://github.com/sdss/jaeger/issues/191) Support for MDP path generation mode. The default mode can be set in the configuration file under `kaiju.default_path_generator` and overridden in `jaeger configuration load` and `jaeger configuration random` with the `--path-generation-mode` flag.

### ✨ Improved

* Add `FVCITER` keyword to the FVC process image header with the number of the FVC iteration.

### 🏷️ Changed

* `IEB` never disables itself automatically. Now it will try five times to connect to the WAGO module and if that fails it will issue an error but not self disable itself.
* Use 5 degrees as start angle during homing.
* Update disabled/enabled positioners at APO and LCO.

### 🔧 Fixed

* [#189](https://github.com/sdss/jaeger/issues/189) Prevent FPS initialisation from failing if a positioner is reporting a collided status.
* Fix SJD calculation for LCO.


## 1.0.1 - September 11, 2022

### 🔧 Fixed

* Bumped minimum version of `sdssdb` to 0.5.5 since jaeger requires the `Design.field` property.


## 1.0.0 - September 10, 2022

### ✨ Improved

* [#188](https://github.com/sdss/jaeger/issues/188) Chiller temperature and flow can now be set to a fixed value which is monitored and reset if necessary. Temperature can still be set to an "auto" mode that will maintain the set point slightly below the ambient temperature. The `chiller set` command now accepts `auto`, `disable`, or a value for either `flow` or `temperature`. The default values can be set in the configuration file under `chiller.temperature` (`true` for automatic mode, `null` to disable monitoring, or a fixed value) or `chiller.flow` (accepts `null` or a fixed value).
* Added `--quiet` flag to `status` that does not print the status of each robot.
* Added back `jaeger configuration fake-field` command.
* Fixed `jaeger configuration slew` command to work with `lcotcc`.
* Enabled additional alarms for LCO.

### 🏷️ Changed

* Reworked scale logic when loading a new design:
  * If a `--scale` flag is passed, that scale is passed directly to coordio without any additional fudge factor.
  * If the guider scale is available and `use_guider_scale=True`, the guider scale multiplied by the fudge factor is passed.
  * If `use_guider_scale=True` and the `scale_temperature_coeffs` are defined, and the guider scale is not available, the guider scale is defined by the scale-temperature correlation and the fudge factor is applied.
  * If `use_guider_scale=False` and `--scale` is not passed, or otherwise the guider scale cannot be defined, the `default_scale` value is used.
* Use `targetdb.design_to_field` table.
* Use difference centroiding methods for APO and LCO.
* Change various configuration parameters for LCO.
* Renamed `kludge_factor` and `--kludge-factor` to `fudge_factor` and `--fudge-factor`.

### 🔧 Fixed

* Alerts and chiller bots were not being started on init.
* Solve a case in which a manually disabled positioner could not be re-enabled after the FPS had been power cycled or reinitialised.


## 0.16.1 - June 10, 2022

### 🚀 New

* [#183](https://github.com/sdss/jaeger/issues/183) The `FVC.write_summaryF()` method now also produces some histograms and quiver plots that show the FVC convergence in wok and ra/dec coordinates.
* [#184](https://github.com/sdss/jaeger/issues/184) Added a `jaeger.fvc.reprocess_configuration()` coroutine that allows to reprocess the final FVC image for a configuration with a different centroid method.
* [#185](https://github.com/sdss/jaeger/issues/185) Support for LCO and additional improvements:
  * General support for running jaeger at APO and LCO.
  * The `jaeger` and `ieb` configuration files have been split into `_APO` and `_LCO`.
  * Makes `fvc loop` more reliable. The `proc-` image is now saved in most conditions.
  * Use `FVCTransformAPO` or `FVCTransformLCO` depending on the observatory.
  * Fix `fvc loop` with `--fbi-level 0`.
  * Chiller and alerts bots are now run as part of the actor instead of in the `FPS` object. Alerts are now observatory-specific.
  * Do not calculate paths when using `jaeger configuration load --from-positions`.
  * Fix for #182: GFA alerts are disabled if the camera is powered off.
  * New `home` command to send `GO_TO_DATUMS` to multiple or all positioners at once.
* [#186](https://github.com/sdss/jaeger/issues/186) New command `fvc snapshot` that creates a temporary configuration from the current positions and takes an FVC measurement.
* Add `jaeger configuration reload` command. It's equivalent to using `jaeger configuration load --no-clone DESIGNID` where `DESIGNID` is the currently loaded design.
* If called without arguments, `disable` now outputs the list of currently disabled robots.

### ✨ Improved

* The list of manually disabled positioners is kept during reinitialisation.

### 🏷️ Changed

* Default FVC centroid algorithm is now `zbplus`.

### 🔧 Fixed

* [#182](https://github.com/sdss/jaeger/issues/182) GFA alert for a camera is disabled if the camera is off.
* Bump sdssdb to ^0.5.2 to ensure that `assignment_hash` is available


## 0.16.0 - June 10, 2022

* Yanked due to error releasing.


## 0.15.1 - April 24, 2022

### 🔧 Fixed

* Pin `pydl` 1.0.0rc1 since rc2 has been yanked.


## 0.15.0 - April 21, 2022

### 🔥 Breaking changes

* Minimum Python version is now 3.8. Astropy 5 required.

### 🚀 New

* [#180](https://github.com/sdss/jaeger/issues/180) A design can now be preloaded using `jaeger configuration preload [DESIGNID]`. If a design ID is not provied, the first element from the queue will be used. Preloading calculates the trajectories for the new configuration but does not write to the database, generate the configuration summary file, or output keywords. To make the preloaded design active, use `jaeger configuration load --from-preloaded`.
* [#181](https://github.com/sdss/jaeger/issues/181) Automatically determine the epoch delay for a new configuration created from a design in the queue. If there are multiple consecutive designs in the queue with the same hash (i.e., that will be cloned), determines the epoch delay so that the array is reconfigured for the middle epoch of the observations.
* Added `fvc_percent_reached` and `perc_95` keywords to the FVC loop to show the percentage of robots that have reached their positions (within the FVC tolerance) and the 95% percentile respectively.
* `delta_ra` and `delta_dec` from the database are now applied.
* Require coordio 1.3.0.

### ✨ Improved

* If a new configuration is loaded while the array is unfolded and `jaeger configuration reverse` is called, the reverse trajectory from the previous configuration will be used.
* Update coordinates using `delta_ra` and `delta_dec` from `targetdb.carton_to_target`.
* Introduced an empirical kludge factor for the guider scale that can be adjusted in the configuration file as `configuration.scale_kludge_factor`.
* Use reverse trajectory from previous configurations if the current one has not been executed.
* `jaeger configuration reverse` now accepts an `--explode` flag.
* FVC loops succeeds if `fvc_perc_90 < 30`.
* Add `fvc_image_path` to confSummaryF.
* Allow to home alpha and beta independently.

### 🔧 Fixed

* Fixed an issue in which the epoch RA/Dec in a configuration had the correctly propagated proper motions but those were not used for observed, field, etc. coordinates. Those coordinated derived in practice from the catalogue coordinates at the catalogue epoch.


## 0.14.0 - February 11, 2022

### 🚀 New

* [#177](https://github.com/sdss/jaeger/issues/177) When `jaeger configuration load` is called with a design that has the same targets as the currently loaded (as it happens for multi-exposure fields), the existing configuration and metadata are copied but decollision is not run and new paths are not generated.
* [#175](https://github.com/sdss/jaeger/issues/175) Added alert system.
* [#176](https://github.com/sdss/jaeger/issues/176) Refine configuration focal plane scale using the guider measured scale.
* Addded a scale factor when loading a configuration.
* Allow to load a design from the queue.

### ✨ Improved

* [#178](https://github.com/sdss/jaeger/issues/178) Major refactor of the chiller code.
* [#179](https://github.com/sdss/jaeger/issues/179) Refactored FVC code to use coordIO's `FVCTransformAPO`.
* Add jaeger, kaiju, and coordio versions to confSummary.
* Allow to disable chiller watcher.
* Store positioners in trajectory data file. Dump trajectory data even if trajectory fails.

### 🔧 Fixed

* `jaeger status` now won't output additional information if the status of a single positioner is requested.


## 0.13.1 - January 7, 2022

### 🚀 New

* Added `jaeger configuration slew` command.
* Added chiller control.
* A keyword `folded` is updated after a trajectory indicating if the array is folded. In `jaeger unwind`, if the array is already folded (with a tolerance of 1 degree), it does nothing. `jaeger unwind --status` only reports the folded status.
* Report `alive_at` every 60 second.
* Add commands to enable and disable a positioner during runtime.

### ✨ Improved

* Check that the rotator is halted before exposing the FVC.
* Added wok coordinates to summary files.
* `jaeger configuration load` is now cancellable.
* Restore parent configuration when reversing a dithered configuration.

### 🔧 Fixed

* Increase `optical_prov` field in confSummary file to 30 characters.


## 0.13.0 - December 14, 2021

### 🚀 New

* [#163](https://github.com/sdss/jaeger/issues/163) The to and from destination trajectories are saved when `BaseConfiguration.decollide_and_get_paths()` is called. The reverse path can be sent from the actor using `configuration reverse`. The paths can be generated in advance when loading the design. An ``--epoch-delay`` parameter can be passed when loading the design to create a configuration for some time in the future.
* [#167](https://github.com/sdss/jaeger/issues/167) Add the ability of loading a configuration from the current positions of the robots.
* [#169](https://github.com/sdss/jaeger/issues/169) Move `ieb power` and `ieb switch` to simply `power`.
* [#173](https://github.com/sdss/jaeger/issues/173) Added `DitheredConfiguration` class.

### ✨ Improved

* [#166](https://github.com/sdss/jaeger/issues/166) During `BaseConfiguration.decollide_and_get_paths()` the paths are decollided and deadlocks resolved while trying to maintain as many robots on target as possible. The fibre table is updated.
* [#168](https://github.com/sdss/jaeger/issues/168) Functional version of design loading. Collisions are solved by first attempting to remove unassigned targets. Deadlock resolution uses the same logic as the random configuration creation. ``fiberId`` is not added to the summary file. Snapshots for each configuration are created.
* [#172](https://github.com/sdss/jaeger/issues/172) The FVC centroids are now derotated according to the rotator angle, allowing to run the FVC loop at any rotator position.
* [#174](https://github.com/sdss/jaeger/issues/174) Improved metadata handling in FVC loop.
* Snapshots are run in a process pool executor and are saved automatically at the end of a trajectory or when `TrajectoryError` is raised.
* `jaeger.commands.goto.goto()` generates `kaiju`-valid trajectories by default.
* FVC RMS fit only takes assigned robots into account.
* Added a check when loading a design to confirm that the design exists and is for the current observatory.
* Unassigned robots in a configuration as scrambled and are the first to be decollided.

### 🔧 Fixed

* [#168](https://github.com/sdss/jaeger/issues/168) Fixed use of proper motions that were being applied as if the JD epoch was the Julian year.

### 🔥 Removed

* `Positioner.goto()` has been removed. Use `jaeger.commands.goto.goto()` instead.


## 0.12.0 - November 28, 2021

### 🚀 New

* Code to load robostrategy designs to `targetdb` and create configurations. Added actor commands to use `kaiju` to calculate and send a valid trajectory and to unwind, explode, and send a random configuration to the array.
* [#153](https://github.com/sdss/jaeger/issues/153) Handling of low temperature now happens in `FPS` instead of in the actor. Added an `FPS.status` attribute with the global status of the system (idle, moving, collided, and temperature status). The actor subscribes to status changes using `FPS.async_status()` and broadcasts them.
* Add `FPS.save_snapshot()` along with actor command `snapshot` to use kaiju to save a plot with the current arrangement of the FPS array.
* Add a lockfile to prevent multiple instance of `jaeger` running at the same time. At the observatories, `jaeger` can only run on `sdss5-fps`.
* All functions that call CPU-intensive methods in `kaiju` are now run in a `ProcessPool` executor.
* FVC loop is now functional.

### ✨ Improved

* [#157](https://github.com/sdss/jaeger/issues/157) Prevents clearing the collided flags when the array is stopped during a collision or when initialised. Issues `SEND_TRAJECTORY_ABORT` instead of `STOP_TRAJECTORY`. They both stop all the positioners but the former does not clear the collided status flags.
* Added additional checks to confirm that a trajectory starts correctly and succeeds. After one second, the code checks that the FPS is moving and that `DISPLACEMENT_COMPLETED` is not present on any positioner status. At the end of the trajectory a check confirms that all the positioners are within 0.1 degrees of their destinations.
* Add `--no-gfas` to the `ieb power on` command to avoid powering the GFAs during the power on sequence.
* Allows to call `FVC.expose()` without an active command by creating an ad-hoc Tron connection.
* `FVC.expose()` now can stack multiple exposures.
* Allows to use fibre_type other than 'Metrology' when processing an FVC image.
* Defaults to `proc-<image>` when calling `FVC.write_proc_image()`.


## 0.11.0 - October 12, 2021

### 🚀 New

* [#152](https://github.com/sdss/jaeger/pull/152) FVC IEB support and actor commands.
* Add commands `GET_HALL_CALIB_ERROR`, `GET_ALPHA_HALL_CALIB`, and `GET_BETA_HALL_CALIB` for hall sensor calibration querying.
* The actor status command now returns the number of trajectories executed.

### ✨ Improved

* Add `Trajectory.start_time` and `Trajectory.end_time` that can be used to determine when the trajectory failed. `send_trajectory` now allows to return the unsent or non-started trajectory.
* When running the actor as a daemon in detached mode, log stdout and stderr to file.
* By default, do not fail when a command receives an `UNKNOWN_COMMAND` reply; this usually means that the positioner firmware does not support that command yet. This can be disabled by initialising the `Command` with `ignore_unknown=False`.
* It's now possible to switch the SYNC line relay (`ieb switch sync`). The SYNC line may be left closed if there's an uncaught exception while it's being actuated, or if the script is killed during that time. This allows to restore it to open.
* `TrajectoryError` now includes the original `Trajectory` object as `TrajectoryError.trajectory`.
* `FPS.send_trajectory()` now raises a `TrajectoryError` if it fails.
* Better logging of the reason for failure in `Trajectory`. In particular, `Trajectory.failed_positioners` contains a dictionary with the reason why a give positioner failed to receive or execute the trajectory, if that information is known.

### 🔧 Fixed

* Avoid clipping the current position to `(0, 360)` when calculating the trajectory in `goto()`. This prevents using `goto()` when the positioner is at a negative position.


## 0.10.0 - August 3, 2021

### 🚀 New

* [#149](https://github.com/sdss/jaeger/issues/149) Added an `FPS.goto()` method that sends a list of positioners to a given position using trajectories. By default `Positioner.goto()` now also uses trajectories, but `GOTO_ABSOLUTE_POSITION` can still be used.
* [#150](https://github.com/sdss/jaeger/issues/150) Allow to skip positioners that are connected to the bus but that we want to ignore. Also allow to disable collision detection for a list of positioners. See configuration options `fps.skip_positioners` and `fps.disable_collision_detection_positioners`.

### ✨ Improved

* `Trajectory()` now sends data points using a single command per trajectory chunk.
* Warn about individual replies that return without `COMMAND_ACCEPTED`.
* Remove check for whether a positioner has started to move after sending the goto command. It sometimes produced false positives on very short moves.
* Disable precise moves by default.
* Improve reloading the FPS.
* Remove `bootloader` commands from actor.
* `FPS.locked_by` now reports what positioner id(s) locked the FPS on a collision.
* Actor `ieb fbi` now accepts multiple devices.
* Use coil space for IEB relays.
* Use 8 message per positioner by default when upgrading the firmware.

### 🔧 Fixed

* When sending multiple message per positioner per command, assign different UIDs.
* Fix address of IEB RTD12.
* Fix upgrade firmware script in the case of a single test sextant.
* Turn off all sextants before upgrading the firmware.
* Fixed and tested the power on and power off IEB sequences.


## 0.9.0 - July 18, 2021

### 🚀 New

* [#131](https://github.com/sdss/jaeger/issues/131) **Breaking change**. This version includes a major rewrite of the internals of `Command` and how it is used throughout `jaeger`. In addition to acception a single `positioner_id`, `Command` can now receive a list of positioners to command. When the command is awaited it will wait until all the positioners have replied or the command has timed out. For the most part this is equivalent to using the old `FPS.send_to_all()` which has now been deprecated, but with the advantage that a single `Future` is created. This seems to significantly decrease the overhead that `asyncio` introduces when creating and await many tasks. `FPS.send_command()` now also accepts a list of positioners, thus replacing `send_to_all()`. For the most part low level initialisation of commands, as long as they are used to address a single positioner, should not have changed. To address multiple positioners at once use `send_command()`.
* [#127](https://github.com/sdss/jaeger/issues/127) Implemented positioner LED on/off commands.
* [#128](https://github.com/sdss/jaeger/issues/128) Deprecated the use of `python-can` buses since they block in a non-asynchronous way. This caused significant inefficiencies when controller >200 robots, especially on computers with old CPUs. This PR implements the major changes, including refacting `JaegerCAN` and `FPS` to initialise the buses asynchronously, and a reimplementation of `CANNetBus`, `VirtualBus`, and `Notifier`. This PR also includes some general performance gains such as a better implementation of `parse_identifier`.
* [#134](https://github.com/sdss/jaeger/issues/134) Added a new actor command `reload` that will reinitialise the `FPS` instance and reload any new robots after a sextant power cycle.
* [#142](https://github.com/sdss/jaeger/issues/142) Added an `ieb info` actor command to show information about the IEB layout to users.
* [#119](https://github.com/sdss/jaeger/issues/119) Allow to manually add and initialise a single positioner.

### ✨ Improved

* [#135](https://github.com/sdss/jaeger/issues/135) Cleaned up initialisation methods for `JaegerCAN` and `FPS`. Objects can now be instantiated and initialised at the same time using the async classmethod `.create()`.
* [#141](https://github.com/sdss/jaeger/issues/141) The `jaeger upgrade-firmware` command will now upgrade the firmware of one sextant at a time to avoid powering on too many power supplies at the same time.
* [#124](https://github.com/sdss/jaeger/issues/124) Collisions are handled better. If a move command is running when the FPS is locked, the command is cancelled. `Postioner.goto()` and `send_trajectory()` now continuously check if the FPS has been locked during the move. If it is, they fail in a non-verbose way. `FPS.send_trajectory()` now logs an error but doesn't raise an exception if the trajectory fails.

### 🧹 Cleaned

* [#129](https://github.com/sdss/jaeger/issues/129) Removed the use of the database and predefined layouts for the FPS. Default mode is that positioners are always auto-discovered.
* [#133](https://github.com/sdss/jaeger/issues/133) Completely removed the use of `python-can`. A conditional import is done for the `slcan` and `socketcan` interfaces for which `python-can` does need to be installed.
* [#130](https://github.com/sdss/jaeger/issues/130) Removed engineering mode.
* [#132](https://github.com/sdss/jaeger/issues/132) Merged `JagerCAN._send_commands()` and `.send_to_interfaces()` into `JaegerCAN.send_commands()`. Renamed `FPS.send_command()` `synchronous` parameter to `now`.


## 0.8.0 - June 21, 2021

### 🚀 New

* [#122](https://github.com/sdss/jaeger/issues/122) Precise moves can now be disabled for all positioners by setting the configuration parameter `positioner.disable_precise_moves`. Also implements the CAN commands `SWITCH_[ON|OFF]_PRECISE_MOVE_[ALPHA|BETA]`.
* New `debug` parameter in the configuration file. If it is `false`, some warnings will be silenced and `JaegerCAN` will not log to `can.log`.
* [#126](https://github.com/sdss/jaeger/issues/126) Use [furo](https://pradyunsg.me/furo/) Sphinx theme. Add `noxfile` for `sphinx-autobuild`.

### 🔧 Fixed

* Bug preventing the FPS from being initialised when upgrading the firmware if one was not power cycling the electronics from software.

### ✨ Improved

* Improved the performance when upgrading the firmware. When calling `load_firmware` one can specify how many ``messages_per_positioner`` to send at once. Too many messages at once will overflow the buffer, but the right number can optimise performance. By default, logging to the CAN log will be suspended during the firmware upgrade to boost performance.


## 0.7.0 - May 24, 2021

### 🚀 New

* [#96](https://github.com/sdss/jaeger/issues/85) Raise error if sending a bootloader command while not in bootloader mode.
* [#109](https://github.com/sdss/jaeger/issues/109) Added JSON schema for the actor.
* [#97](https://github.com/sdss/jaeger/issues/97) Implement low temperature handling. When the air temperature is less than 0 degrees, the motor RPM is set to 3000. When the temperature is less than -10, the beta motor holding current is increased to 30%.
* [#15](https://github.com/sdss/jaeger/issues/15) Allow to disable a positioner. If the positioner is disabled, a non-safe command sent to the FPS will raise an error. In `send_to_all`, a broadcast will be only sent to the non-disabled positioners. Trajectories that include disabled positioners will fail.
* [#116](https://github.com/sdss/jaeger/issues/116) Safe mode to prevent the beta arm to go below 160 degrees.

### ✨ Improved

* [#121](https://github.com/sdss/jaeger/issues/121) Improve the use of the database to define the FPS layout.

### 🧹 Cleanup

* [#96](https://github.com/sdss/jaeger/issues/96) Discontinue the use of `sdsscore`. Improved the handling of user configuration files.
* [#95](https://github.com/sdss/jaeger/issues/95) Support Python 3.9.
* Require `drift>=0.2.2` to fix a bug in setting the relay values.
* Stop using `releases` for the changelog and migrate to using [CHANGELOG.md](https://github.com/sdss/jaeger/blob/main/CHANGELOG.md). Release information for previous version is available [here](https://sdss-jaeger.readthedocs.io/en/0.6.0/changelog.html).


## The Pre-history

The changelog for previous version of `jaeger` is available [here](https://sdss-jaeger.readthedocs.io/en/0.6.0/changelog.html).
