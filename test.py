import asyncio

from jaeger import FPS, Positioner


async def main():
    p = Positioner(20)
    fps = FPS()
    await fps.start_can()
    fps.add_positioner(p, interface=0, bus=4)
    await p.initialise()
    print(p.alpha, p.beta)
    print(p.get_bus())


asyncio.run(main())
