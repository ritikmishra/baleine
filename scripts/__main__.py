import asyncio
import logging

from anacreonlib.types.request_datatypes import AnacreonApiRequest
from rx.operators import first

from scripts.context import AnacreonContext
from scripts.tasks import explore_unexplored_regions
from .creds import ACCESS_TOKEN, GAME_ID, SOV_ID
auth = {
    "auth_token": ACCESS_TOKEN,
    "game_id": GAME_ID,
    "sovereign_id": SOV_ID
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

async def main():
    logger = logging.getLogger("main")
    fleet_names = ("roomba","roomba2","roomba3","roomba4","roomba5","roomba6")
    futures = []

    context = AnacreonContext(AnacreonApiRequest(**auth))

    try:
        futures.append(asyncio.create_task(context.periodically_update_objects()))
        logger.info("Waiting to get objects")
        await context.watch_update_observable.pipe(first())
        logger.info("Got objects!")
        await asyncio.gather(*[explore_unexplored_regions(context, fleet_name) for fleet_name in fleet_names])
    finally:
        for future in futures:
            await future

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    asyncio.ensure_future(main())
    loop.run_forever()
