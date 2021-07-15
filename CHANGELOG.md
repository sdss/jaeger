# Changelog

## Next version

### 🚀 New

* [#127](https://github.com/sdss/jaeger/issues/127) Implemented positioner LED on/off commands.
* [#128](https://github.com/sdss/jaeger/issues/128) Deprecated the use of `python-can` buses since they block in a non-asynchronous way. This caused significant inefficiencies when controller >200 robots, especially on computers with old CPUs. This PR implements the major changes, including refacting `JaegerCAN` and `FPS` to initialise the buses asynchronously, and a reimplementation of `CANNetBus`, `VirtualBus`, and `Notifier`. This PR also includes some general performance gains such as a better implementation of `parse_identifier`.

### 🧹 Cleaned

* [#129](https://github.com/sdss/jaeger/issues/129) Removed the use of the database and predefined layouts for the FPS. Default mode is that positioners are always auto-discovered.
* [#133](https://github.com/sdss/jaeger/issues/133) Completely removed the use of `python-can`. A conditional import is done for the `slcan` and `socketcan` interfaces for which `python-can` does need to be installed.
* [#130](https://github.com/sdss/jaeger/issues/130) Removed engineering mode.


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
