#!/usr/bin/env python
# flake8: noqa
# type: ignore

import asyncio
import time

import click
import numpy
import pandas as pd

from kaiju.robotGrid import RobotGridCalib
from sdsstools.daemonizer import cli_coro

from jaeger import config, log
from jaeger.exceptions import TrajectoryError
from jaeger.fvc import FVC


log.sh.setLevel(20)


# hardcoded defaults
MET_LED = 1  # leds 1 and 2
AP_LED = 3  # led 3
BOSS_LED = 16  # led 4
ANG_STEP = 0.1  # step size in degrees for path generation
EPS = ANG_STEP * 2.2  # absolute distance used for path smoothing
USE_SYNC_LINE = False  # whether or not to use the sync line for paths
GREED = 0.8  # parameter for MDP
PHOBIA = 0.2  # parameter for MDP
SMOOTH_PTS = 5  # number of points for smoothing paths
COLLISION_SHRINK = 0.05  # mm to shrink buffers by for path smoothing/simplification
PATH_DELAY = 1  # seconds of time in the future to send the first point
SAFE_BETA = [165, 195]
MAX_ALPHA = 358  # set here for the one robot without full range alpha travel
BAD_ROBOTS = []  # put offline robots in this list?


def getTargetCoords(rg):
    # return the desired xyWok positions for the metrology
    # fiber for each robot, move this stuff to robot grid...
    positioner_id = []
    fibre_type = []
    hole_id = []
    xwok = []
    ywok = []
    offline = []

    for r in rg.robotDict.values():

        # append metrology fiber info
        positioner_id.append(r.id)
        hole_id.append(r.holeID)
        fibre_type.append("Metrology")
        xwok.append(r.metWokXYZ[0])
        ywok.append(r.metWokXYZ[1])
        offline.append(int(r.isOffline))

        # append boss fiber info
        positioner_id.append(r.id)
        hole_id.append(r.holeID)
        fibre_type.append("BOSS")
        xwok.append(r.bossWokXYZ[0])
        ywok.append(r.bossWokXYZ[1])
        offline.append(int(r.isOffline))

        # append apogee fiber info
        positioner_id.append(r.id)
        hole_id.append(r.holeID)
        fibre_type.append("APOGEE")
        xwok.append(r.apWokXYZ[0])
        ywok.append(r.apWokXYZ[1])
        offline.append(int(r.isOffline))

    return pd.DataFrame(
        {
            "positioner_id": positioner_id,
            "hole_id": hole_id,
            "fibre_type": fibre_type,
            "xwok": xwok,
            "ywok": ywok,
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
            robot.setXYUniform()
        else:
            alpha = numpy.random.uniform(0, MAX_ALPHA)
            beta = numpy.random.uniform(SAFE_BETA[0], SAFE_BETA[1])
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


async def exposeFVC(fvc, exptime, fibre_data):
    print("exposing FVC")
    rawfname = await fvc.expose(exposure_time=exptime)
    print("exposure complete: %s" % rawfname)
    fvc.process_fvc_image(rawfname, fibre_data, plot=True)
    print("image processing complete")
    await fvc.write_proc_image()
    print("image write complete")


@click.command()
@click.option(
    "--niter",
    default=1,
    type=click.IntRange(min=1),
    show_default=True,
    help="number of move iterations to perform",
)
@click.option(
    "--exptime",
    default=1.6,
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
    "--mdp",
    is_flag=True,
    show_default=True,
    help="if passed, use markov decision process "
    "for path generation, otherwise a greedy algorithm is used",
)
@cli_coro()
async def robotcalib(
    niter,
    exptime,
    speed,
    cb,
    danger=False,
    lh=True,
    seed=None,
    met=False,
    boss=False,
    apogee=False,
    mdp=False,
):

    fvc = FVC(config["observatory"])
    fps = fvc.fps
    await fps.initialise()

    if seed is None:
        seed = numpy.random.randint(0, 30000)

    ######## UNWIND GRID #############
    rg = getRandomGrid(seed=seed, danger=danger, collisionBuffer=cb, lefthand=lh)

    # set the robot grid to the current jaeger positions
    await setKaijuCurrent(fps, rg)

    # generate the path to fold
    tstart = time.time()
    if mdp:
        rg.pathGenMDP(GREED, PHOBIA)
    else:
        rg.pathGenGreedy()

    print("unwind path generation took %.1f seconds" % (time.time() - tstart))

    # verify that the path generation was successful if not exit
    if rg.didFail:
        print("failed to unwind grid. deadlock in path generation")
        return

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
        return

    # command the path from the initial state (set by jaeger)
    # to the destination state (folded)
    tstart = time.time()
    print("sending unwind trajectory")
    await fps.send_trajectory(toDestination, use_sync_line=USE_SYNC_LINE)
    print("unwind finished, took %.1f seconds" % (time.time() - tstart))

    ########### UNWIND FINISHED ##############

    ########### BEGIN CALIBRATION LOOP #######
    for ii in range(niter):
        seed += 1
        print("\n MOVE ITER %i of %i(seed=%i)\n-----------" % (ii, niter, seed))

        # begin searching for a valid path
        # this is easy when things are safe
        # and easier for smaller collision buffers using the MDP algorithm
        replacedRobotList = []
        rg = getRandomGrid(seed=seed, danger=danger, collisionBuffer=cb, lefthand=lh)

        # try 5 times to find valid path
        # before giving up and moving on
        for jj in range(5):
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
            nDeadlocks = rg.deadlockedRobots()
            if len(nDeadlocks) > 6:
                print("too many deadlocks to resolve (%i deadlocks)" % nDeadlocks)
                break

            replaceableRobots = list(set(rg.deadlockedRobots) - set(BAD_ROBOTS))
            nextReplacement = numpy.random.choice(replaceableRobots)
            replacedRobotList.append(nextReplacement)
            rg = getRandomGrid(
                seed=seed, danger=danger, collisionBuffer=cb, lefthand=lh
            )
            for robotID in replacedRobotList:
                robot = rg.robotDict[robotID]
                robot.setXYUniform()
            rg.decollideGrid()

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

        if rg.smoothCollisions:
            print(
                "%i smooth collisions, skipping to next iteration"
                % (rg.smoothCollisions)
            )
            continue

        tstart = time.time()
        print("sending path fold-->target")
        try:
            await fps.send_trajectory(fromDestination, use_sync_line=USE_SYNC_LINE)
            print("move complete, duration %.1f" % (time.time() - tstart))
        except TrajectoryError as e:
            print("TRAJECTORY ERROR moving fold-->target")
            print("failed positioners: ", str(e.trajectory.failed_positioners))
            return

        ### turn all LEDs off ###
        await ledOff(fps, "led1")
        await ledOff(fps, "led2")
        await ledOff(fps, "led3")
        await ledOff(fps, "led4")

        if exptime > 0:
            if True not in [met, apogee, boss]:
                # no fibers illuminated, expose anyway
                await exposeFVC(fvc, exptime, targetCoords)

            else:
                # take a single exposure for each fiber wanted
                if met:
                    print("back illuminating metrology")
                    await ledOn(fps, "led1", MET_LED)
                    await ledOn(fps, "led2", MET_LED)
                    await exposeFVC(fvc, exptime, targetCoords)
                    await ledOff(fps, "led1")
                    await ledOff(fps, "led2")
                if boss:
                    print("back illuminating boss")
                    await ledOn(fps, "led4", BOSS_LED)
                    await exposeFVC(fvc, exptime, targetCoords)
                    await ledOff(fps, "led4")
                if apogee:
                    print("back illuminating apogee")
                    await ledOn(fps, "led3", AP_LED)
                    await exposeFVC(fvc, exptime, targetCoords)
                    await ledOff(fps, "led3")

        ### send path back to lattice ####
        tstart = time.time()
        print("sending path target-->fold")
        try:
            await fps.send_trajectory(toDestination, use_sync_line=USE_SYNC_LINE)
            print("move complete, duration %.1f" % (time.time() - tstart))
        except TrajectoryError as e:
            print("TRAJECTORY ERROR moving target-->fold")
            print("failed positioners: ", str(e.trajectory.failed_positioners))
            return


if __name__ == "__main__":
    asyncio.run(robotcalib())
