import asyncio
import logging

from anacreonlib.types.request_datatypes import AnacreonApiRequest
from anacreonlib.types.response_datatypes import World
from rx.operators import first

from scripts.context import AnacreonContext
from scripts.tasks import conquest_tasks
from scripts.tasks.improvement_related_tasks import build_habitats_spaceports
from scripts.utils import TermColors, dist

try:
    from scripts.creds import ACCESS_TOKEN, GAME_ID, SOV_ID
except ImportError:
    raise LookupError("Could not find creds.py in scripts package! Did you make one?")

auth = {
    "auth_token": ACCESS_TOKEN,
    "game_id": GAME_ID,
    "sovereign_id": SOV_ID
}

logging.basicConfig(level=logging.INFO, format=f'{TermColors.OKCYAN}%(asctime)s{TermColors.ENDC} - %(name)s - {TermColors.BOLD}%(levelname)s{TermColors.ENDC} - {TermColors.OKGREEN}%(message)s{TermColors.ENDC}')


async def main():
    logger = logging.getLogger("main")
    fleet_names = ("roomba","roomba2","roomba3","roomba4","roomba5","roomba6")
    futures = []
    update_task = None

    context = await AnacreonContext.create(AnacreonApiRequest(**auth))
    try:
        update_task = asyncio.create_task(context.periodically_update_objects())

        logger.info("Waiting to get objects")
        full_state = await context.watch_update_observable.pipe(first())
        logger.info("Got objects!")

        await build_habitats_spaceports(context)

        # capital = next(world for world in full_state if isinstance(world, World) and world.sovereign_id == SOV_ID)
        # possible_victims = [world for world in full_state if isinstance(world, World) and 0 < dist(world.pos, capital.pos) <= 200 and world.sovereign_id == 1]
        #
        # await conquest_tasks.conquer_planets(context, possible_victims, generic_hammer_fleets={"hammer"}, nail_fleets={"nail"})

        # futures.extend(asyncio.create_task(explore_unexplored_regions(context, fleet_name)) for fleet_name in fleet_names)
        # await simple_tasks.explore_around_planet(context, center_world_id=99)
    finally:
        for future in futures:
            await future
        update_task.cancel()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
