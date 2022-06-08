# Changelog

## Next version

### 🚀 New

* [#183](https://github.com/sdss/jaeger/issues/183) The `FVC.write_summaryF()` method now also produces some histograms and quiver plots that show the FVC convergence in wok and ra/dec coordinates.
* [#184](https://github.com/sdss/jaeger/issues/184) Added a `jaeger.fvc.reprocess_configuration()` coroutine that allows to reprocess the final FVC image for a configuration with a different centroid method.
* [#186](https://github.com/sdss/jaeger/issues/186) New command `fvc snapshot` that creates a temporary configuration from the current positions and takes an FVC measurement.
* Add `jaeger configuration reload` command. It's equivalent to using `jaeger configuration load --no-clone DESIGNID` where `DESIGNID` is the currently loaded design.
* If called without arguments, `disable` now outputs the list of currently disabled robots.

### ✨ Improved

* The list of manually disabled positioners is kept during reinitialisation.

### 🏷️ Changed

* Default FVC centroid algorithm is now `zbplus`.

### 🔧 Fixed

* Bump sdssdb to ^0.5.2 to ensure that `assignment_hash` is available


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
