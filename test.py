import asyncio

from sdssdb.peewee.sdss5db import database

from jaeger.target import Design


async def main():
    database.connect("sdss5db_jaeger_test", user="sdss", port=5433, host="localhost")

    d = Design(21636, epoch=2460427)
    breakpoint()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
