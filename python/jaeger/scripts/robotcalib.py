#!/usr/bin/env python
# flake8: noqa
# type: ignore

import asyncio
import time

import click
import numpy
import pandas as pd

from kaiju import utils
from kaiju.robotGrid import RobotGridCalib
from sdsstools.daemonizer import cli_coro


# hardcoded defaults
MET_LED = 1  # leds 1 and 2
AP_LED = 3  # led 3
BOSS_LED = 8  # led 4
ANG_STEP = 0.1  # step size in degrees for path generation
EPS = ANG_STEP * 2  # absolute distance used for path smoothing
USE_SYNC_LINE = False  # whether or not to use the sync line for paths
GREED = 0.9  # parameter for MDP
PHOBIA = 0.1  # parameter for MDP
SMOOTH_PTS = 5  # number of points for smoothing paths
COLLISION_SHRINK = 0.08  # mm to shrink buffers by for path smoothing/simplification
PATH_DELAY = 1  # seconds of time in the future to send the first point
SAFE_BETA = [165, 195]
MAX_ALPHA = 358  # set here for robot 444 without full range alpha travel
BAD_ROBOTS = []  # put offline robots in this list?
DOWNSAMPLE = 100


def getTargetCoords(rg):
    # return the desired xyWok positions for the metrology
    # fiber for each robot, move this stuff to robot grid...
    positioner_id = []
    fibre_type = []
    hole_id = []
    xwok = []
    ywok = []
    alpha = []
    beta = []
    offline = []

    for r in rg.robotDict.values():

        # append metrology fiber info
        positioner_id.append(r.id)
        hole_id.append(r.holeID)
        fibre_type.append("Metrology")
        xwok.append(r.metWokXYZ[0])
        ywok.append(r.metWokXYZ[1])
        alpha.append(r.alpha)
        beta.append(r.beta)
        offline.append(int(r.isOffline))

        # append boss fiber info
        positioner_id.append(r.id)
        hole_id.append(r.holeID)
        fibre_type.append("BOSS")
        xwok.append(r.bossWokXYZ[0])
        ywok.append(r.bossWokXYZ[1])
        alpha.append(r.alpha)
        beta.append(r.beta)
        offline.append(int(r.isOffline))

        # append apogee fiber info
        positioner_id.append(r.id)
        hole_id.append(r.holeID)
        fibre_type.append("APOGEE")
        xwok.append(r.apWokXYZ[0])
        ywok.append(r.apWokXYZ[1])
        alpha.append(r.alpha)
        beta.append(r.beta)
        offline.append(int(r.isOffline))

    return pd.DataFrame(
        {
            "positioner_id": positioner_id,
            "hole_id": hole_id,
            "fibre_type": fibre_type,
            "xwok": xwok,
            "ywok": ywok,
            "alpha": alpha,
            "beta": beta,
            "offline": offline,
        }
    )


def getRandomGrid(seed, danger=False, collisionBuffer=None, lefthand=False):
    numpy.random.seed(seed)
    if lefthand:
        alphaDestination = 350
        betaDestination = 190
    else:
        alphaDestination = 10
        betaDestination = 170

    rg = RobotGridCalib(ANG_STEP, EPS, seed)

    if collisionBuffer is not None:
        rg.setCollisionBuffer(collisionBuffer)

    # hardcode offline robots here? eventually get them from jaeger config?
    # or RobotGridCalib will load from positionerTable
    # example:

    # rg.robotDict[235].setAlphaBeta(0.0076,180.0012)
    # rg.robotDict[235].setDestinationAlphaBeta(0.0076,180.0012)
    # rg.robotDict[235].isOffline = True

    # < no offline robots yet > #

    for robot in rg.robotDict.values():
        if robot.isOffline:
            continue

        if danger:
            # full range of motion
            if lefthand:
                robot.lefthanded = True
            # for now special handling for robot 444
            # note, i'm not checking after
            # decolliding grid, which maybe I should?
            if robot.id == 444:
                while True:
                    robot.setXYUniform()
                    if robot.alpha < MAX_ALPHA:
                        break
            else:
                robot.setXYUniform()
        else:
            alpha = numpy.random.uniform(0, MAX_ALPHA)
            beta = numpy.random.uniform(SAFE_BETA[0], SAFE_BETA[1])
            if robot.id == 999:
                print("robot 999 alpha", alpha)
            robot.setAlphaBeta(alpha, beta)

        robot.setDestinationAlphaBeta(alphaDestination, betaDestination)

    if danger:
        rg.decollideGrid()
    else:
        if rg.getNCollisions() != 0:
            raise RuntimeError("supposedly safe grid is apparently collided!!!")

    return rg


async def setKaijuCurrent(fps, rg):
    """Get a kaiju robot grid initialized to the jaeger reported robot positions"""
    await fps.update_position()
    posArray = fps.get_positions()
    for rID, alpha, beta in posArray:
        robot = rg.robotDict[int(rID)]
        if robot.isOffline:
            continue
        robot.setAlphaBeta(alpha, beta)


async def ledOn(fps, devName, ledpower):
    on_value = 32 * int(1023 * (ledpower) / 100)
    device = fps.ieb.get_device(devName)
    await device.write(on_value)


async def ledOff(fps, devName):
    device = fps.ieb.get_device(devName)
    await device.write(0)


async def exposeFVC(fvc, exptime, fibre_data, nexp):
    from jaeger.exceptions import FVCError

    for ii in range(nexp):
        try:
            print("exposing FVC %i of %i" % (ii + 1, nexp))
            rawfname = await fvc.expose(exposure_time=exptime)
            print("exposure complete: %s" % rawfname)
            fvc.process_fvc_image(rawfname, fibre_data, plot=True)
            print("image processing complete")
            positions = await fvc.fps.update_position()
            fvc.calculate_offsets(positions)
            print("calculcate offsets complete")
            await fvc.write_proc_image()
            print("image write complete")
        except FVCError as e:
            print("exposure failed with FVCError, continuing")


async def unwind(fps, speed, collisionBuffer):
    _seed = 0  # unimportant for unwind
    rg = getRandomGrid(seed=_seed, collisionBuffer=collisionBuffer)

    # set the robot grid to the current jaeger positions
    await setKaijuCurrent(fps, rg)
    if rg.getNCollisions() > 0:
        print("refuse to unwind, grid is kaiju collided!")
        return False

    # generate the path to fold
    tstart = time.time()
    rg.pathGenGreedy()
    print("unwind path generation took %.1f seconds" % (time.time() - tstart))

    # verify that the path generation was successful if not exit
    if rg.didFail:
        print("failed to unwind grid. deadlock in path generation")
        return False

    # smooth the paths
    toDestination, fromDestination = rg.getPathPair(
        speed=speed,
        smoothPoints=SMOOTH_PTS,
        collisionShrink=COLLISION_SHRINK,
        pathDelay=PATH_DELAY,
    )

    # check for collisions in path smoothing
    if rg.smoothCollisions:
        print("failed to unwind grid. collision in path smoothing")
        return False

    # command the path from the initial state (set by jaeger)
    # to the destination state (folded)
    tstart = time.time()
    print("sending unwind trajectory")
    await fps.send_trajectory(toDestination, use_sync_line=USE_SYNC_LINE)
    print("unwind finished, took %.1f seconds" % (time.time() - tstart))
    return True


@click.command()
@click.option(
    "--niter",
    default=1,
    type=click.IntRange(min=1),
    show_default=True,
    help="number of move iterations to perform",
)
@click.option(
    "--nexp",
    default=1,
    type=click.IntRange(min=0),
    show_default=True,
    help="number of repeated exposures for each move and backlit fiber combo",
)
@click.option(
    "--exptime",
    default=2.5,
    type=click.FloatRange(min=0),
    show_default=True,
    help="fvc exposure time, if 0, no exposures are taken",
)
@click.option("--seed", type=click.IntRange(min=0), help="random seed to use")
@click.option(
    "--speed",
    default=2,
    type=click.FloatRange(0.1, 2.9),
    show_default=True,
    help="speed of robot in RPM at output",
)
@click.option("--lh", is_flag=True, help="if passed, use lefthand robot kinematics")
@click.option(
    "--cb",
    default=2.4,
    type=click.FloatRange(1.5, 2.5),
    show_default=True,
    help="set collision buffer for all robots in grid",
)
@click.option(
    "--danger",
    is_flag=True,
    show_default=True,
    help="if passed, use full workspace for each robot, else limit to safe moves only",
)
@click.option(
    "--met",
    is_flag=True,
    show_default=True,
    help="if passed, get image of back illuminated metrology fibers",
)
@click.option(
    "--boss",
    is_flag=True,
    show_default=True,
    help="if passed, get image of back illuminated boss fibers",
)
@click.option(
    "--apogee",
    is_flag=True,
    show_default=True,
    help="if passed, get image of back illuminated apogee fibers",
)
@click.option(
    "--allfibers",
    is_flag=True,
    show_default=True,
    help="if passed, get image of all fibers back illuminated simultaneously",
)
@click.option(
    "--mdp",
    is_flag=True,
    show_default=True,
    help="if passed, use markov decision process "
    "for path generation, otherwise a greedy algorithm is used",
)
@click.option(
    "--nomove",
    is_flag=True,
    show_default=True,
    help="if passed, do not execute a move, leave array as it is",
)
@click.option(
    "--simpath",
    is_flag=True,
    show_default=True,
    help="if passed, simulate the trajectory but don't send it.  make a movie",
)
@cli_coro()
async def robotcalib(
    niter,
    exptime,
    speed,
    cb,
    nexp,
    danger=False,
    lh=True,
    seed=None,
    met=False,
    boss=False,
    apogee=False,
    allfibers=False,
    mdp=False,
    nomove=False,
    simpath=False,
):
    if seed is None:
        seed = numpy.random.randint(0, 30000)

    if not simpath:
        # unwind if we aren't simulating!!!
        from jaeger import config, log
        from jaeger.exceptions import FVCError, TrajectoryError
        from jaeger.fvc import FVC

        log.sh.setLevel(20)

        fvc = FVC(config["observatory"])
        fps = fvc.fps
        await fps.initialise()
        await fps.unlock()

        ######## UNWIND GRID #############

        success = await unwind(fps, speed, cb - 0.1)
        if not success:
            return  # unwind failed
        ########### UNWIND FINISHED ##############

    ########### BEGIN CALIBRATION LOOP #######
    movesExecuted = 0
    nErrorsForward = 0
    badRobotForward = []
    nErrorsReverse = 0
    badRobotReverse = []
    for ii in range(niter):
        seed += 1
        print(
            "\n----------------------\nITER %i of %i (seed=%i)\n----------------------"
            % (ii + 1, niter, seed)
        )

        # begin searching for a valid path
        # this is easy when things are safe
        # and easier for smaller collision buffers using the MDP algorithm
        replacedRobotList = []
        rg = getRandomGrid(seed=seed, danger=danger, collisionBuffer=cb, lefthand=lh)

        # try 5 times to find valid path
        # before giving up and moving on
        for jj in range(5):
            if nomove:
                # we're not moving!
                await setKaijuCurrent(fps, rg)
                targetCoords = getTargetCoords(rg)
                break
            print("path gen attempt %i" % jj)

            # get the expected coords for each fiber on each robot
            targetCoords = getTargetCoords(rg)

            tstart = time.time()
            if mdp:
                rg.pathGenMDP(GREED, PHOBIA)
            else:
                rg.pathGenGreedy()
            print(
                "attempt %i path generation took %.1f seconds"
                % (jj, (time.time() - tstart))
            )

            if not rg.didFail:
                break

            dlrobots = rg.deadlockedRobots()
            print("%i deadlocked robots" % len(dlrobots))
            if len(dlrobots) > 6:
                print("too many deadlocks to resolve")
                break

            replaceableRobots = list(set(dlrobots) - set(BAD_ROBOTS))
            nextReplacement = numpy.random.choice(replaceableRobots)
            replacedRobotList.append(nextReplacement)
            rg = getRandomGrid(
                seed=seed, danger=danger, collisionBuffer=cb, lefthand=lh
            )
            for robotID in replacedRobotList:
                robot = rg.robotDict[robotID]
                robot.setXYUniform()
            rg.decollideGrid()

        if not nomove:
            if rg.didFail:
                print("failed to generate valid paths, skipping to next iteration")
                continue

            print("valid paths found")
            toDestination, fromDestination = rg.getPathPair(
                speed=speed,
                smoothPoints=SMOOTH_PTS,
                collisionShrink=COLLISION_SHRINK,
                pathDelay=PATH_DELAY,
            )

            # print max steps in paths
            maxPoints = -1
            for alphaBetaPath in toDestination.values():
                for path in alphaBetaPath.values():
                    if len(path) > maxPoints:
                        maxPoints = len(path)
            print("maximum path points %i" % maxPoints)

            if simpath:
                ## simulate only, don't proceed further
                movesExecuted += 1
                interpSteps = len(
                    list(rg.robotDict.values())[0].interpSimplifiedAlphaPath
                )
                print("interpsteps", interpSteps)
                print(
                    "simulating path with %.5e smooth collisions per step"
                    % (rg.smoothCollisions / interpSteps)
                )
                print("smooth collided robots", rg.smoothCollidedRobots)
                for rid in rg.smoothCollidedRobots:
                    r = rg.robotDict[rid]
                    utils.plotTraj(r, "simpath_%i" % ii, dpi=250)
                # utils.plotPaths(rg, downsample=DOWNSAMPLE, filename="simpath_%i.mp4"%ii)
                continue

            if rg.smoothCollisions:
                print(
                    "%i smooth collisions, skipping to next iteration"
                    % (rg.smoothCollisions)
                )
                continue

            movesExecuted += 1
            tstart = time.time()
            print("sending path fold-->target")
            try:
                await fps.send_trajectory(fromDestination, use_sync_line=USE_SYNC_LINE)
                print("move complete, duration %.1f" % (time.time() - tstart))
            except TrajectoryError as e:
                nErrorsForward += 1
                badRobotForward.append(e.trajectory.failed_positioners)
                print("TRAJECTORY ERROR moving fold-->target")
                print("failed positioners: ", str(e.trajectory.failed_positioners))
                print("attempting to recover from trajectory error with unwind")
                # shrink collision buffer
                _cbShrink = cb - 0.1
                await fps.unlock()
                success = await unwind(fps, speed, _cbShrink)
                if success:
                    print("unwind worked skipping to next iteration")
                    continue
                else:
                    print("moves executed", movesExecuted)
                    print("number of traj errors fold-->target", nErrorsForward)
                    print("err robots fold-->target", badRobotForward)
                    print("number of traj errors target-->fold", nErrorsReverse)
                    print("err robots target-->fold", badRobotReverse)
                    return  # exit routine

        else:
            print("not moving array --nomove flag passed")

        ### turn all LEDs off ###
        await ledOff(fps, "led1")
        await ledOff(fps, "led2")
        await ledOff(fps, "led3")
        await ledOff(fps, "led4")
        await asyncio.sleep(1)

        if exptime > 0 and nexp != 0:
            if True not in [met, apogee, boss, allfibers]:
                # no fibers illuminated, expose anyway
                await exposeFVC(fvc, exptime, targetCoords)

            else:
                # take a single exposure for each fiber wanted
                if met:
                    print("back illuminating metrology")
                    await ledOn(fps, "led1", MET_LED)
                    await ledOn(fps, "led2", MET_LED)
                    await asyncio.sleep(1)
                    await exposeFVC(fvc, exptime, targetCoords, nexp)
                    await ledOff(fps, "led1")
                    await ledOff(fps, "led2")
                    await asyncio.sleep(1)
                if boss:
                    print("back illuminating boss")
                    await ledOn(fps, "led4", BOSS_LED)
                    await asyncio.sleep(1)
                    await exposeFVC(fvc, exptime, targetCoords, nexp)
                    await ledOff(fps, "led4")
                    await asyncio.sleep(1)
                if apogee:
                    print("back illuminating apogee")
                    await ledOn(fps, "led3", AP_LED)
                    await asyncio.sleep(1)
                    await exposeFVC(fvc, exptime, targetCoords, nexp)
                    await ledOff(fps, "led3")
                    await asyncio.sleep(1)
                if allfibers:
                    print("back illuminating all fibers")
                    await ledOn(fps, "led1", MET_LED)
                    await ledOn(fps, "led2", MET_LED)
                    await ledOn(fps, "led4", BOSS_LED)
                    await ledOn(fps, "led3", AP_LED)
                    await asyncio.sleep(1)
                    await exposeFVC(fvc, exptime, targetCoords, nexp)
                    await ledOff(fps, "led1")
                    await ledOff(fps, "led2")
                    await ledOff(fps, "led3")
                    await ledOff(fps, "led4")
                    await asyncio.sleep(1)

        ### send path back to lattice ####
        if not nomove:
            tstart = time.time()
            print("sending path target-->fold")
            try:
                await fps.send_trajectory(toDestination, use_sync_line=USE_SYNC_LINE)
                print("move complete, duration %.1f" % (time.time() - tstart))
            except TrajectoryError as e:
                nErrorsReverse += 1
                badRobotReverse.append(e.trajectory.failed_positioners)
                print("TRAJECTORY ERROR moving target-->fold")
                print("failed positioners: ", str(e.trajectory.failed_positioners))
                print("attempting to recover from trajectory error with unwind")
                # shrink collision buffer
                _cbShrink = cb - 0.1
                await fps.unlock()
                success = await unwind(fps, speed, _cbShrink)
                if success:
                    # unwind worked move to next iteration
                    print("unwind worked skipping to next iteration")
                    continue
                else:
                    print("moves executed", movesExecuted)
                    print("number of traj errors fold-->target", nErrorsForward)
                    print("err robots fold-->target", badRobotForward)
                    print("number of traj errors target-->fold", nErrorsReverse)
                    print("err robots target-->fold", badRobotReverse)
                    return  # exit routine

    print("\n-------\nend script\n-------\n")
    print("moves executed", movesExecuted)
    print("number of traj errors fold-->target", nErrorsForward)
    print("err robots fold-->target", badRobotForward)
    print("number of traj errors target-->fold", nErrorsReverse)
    print("err robots target-->fold", badRobotReverse)


if __name__ == "__main__":
    asyncio.run(robotcalib())
