# Changelog

## Next release

### 🚀 New

* [#96](https://github.com/sdss/jaeger/issues/85) Raise error if sending a bootloader command while not in bootloader mode.
* [#109](https://github.com/sdss/jaeger/issues/109) Added JSON schema for the actor.
* [#97](https://github.com/sdss/jaeger/issues/97) Implement low temperature handling. When the air temperature is less than 0 degrees, the motor RPM is set to 3000. When the temperature is less than -10, the beta motor holding current is increased to 30%.
* [#15](https://github.com/sdss/jaeger/issues/15) Allow to disable a positioner. If the positioner is disabled, a non-safe command sent to the FPS will raise an error. In `send_to_all`, a broadcast will be only sent to the non-disabled positioners. Trajectories that include disabled positioners will fail.
* [#116](https://github.com/sdss/jaeger/issues/116) Safe mode to prevent the beta arm to go below 160 degrees.

### 🧹 Cleanup

* [#96](https://github.com/sdss/jaeger/issues/96) Discontinue the use of `sdsscore`. Improved the handling of user configuration files.
* [#95](https://github.com/sdss/jaeger/issues/95) Support Python 3.9.
* Require `drift>=0.2.2` to fix a bug in setting the relay values.
* Stop using `releases` for the changelog and migrate to using [CHANGELOG.md](https://github.com/sdss/jaeger/blob/main/CHANGELOG.md). Release information for previous version is available [here](https://sdss-jaeger.readthedocs.io/en/0.6.0/changelog.html).


## The Pre-history

The changelog for previous version of `jaeger` is available [here](https://sdss-jaeger.readthedocs.io/en/0.6.0/changelog.html).
