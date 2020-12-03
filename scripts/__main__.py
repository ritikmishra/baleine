import asyncio
import logging
import pprint

from anacreonlib.types.request_datatypes import AnacreonApiRequest
from anacreonlib.types.response_datatypes import World
from rx.operators import first

from scripts.context import AnacreonContext
from scripts.tasks import conquest_tasks
from scripts.tasks.cluster_building import build_cluster, connect_worlds_to_fnd, decentralized_trade_route_manager, \
    calculate_resource_deficit
from scripts.tasks.improvement_related_tasks import build_habitats_spaceports
from scripts.tasks.simple_tasks import dump_state_to_json
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
    futures = []
    update_task = None

    context = await AnacreonContext.create(AnacreonApiRequest(**auth))
    try:
        update_task = asyncio.create_task(context.periodically_update_objects())

        logger.info("Waiting to get objects")
        full_state = await context.watch_update_observable.pipe(first())
        logger.info("Got objects!")

        await build_habitats_spaceports(context)
        # await decentralized_trade_route_manager(context, clean_slate=True, throttle=3, dry_run=True)
        await calculate_resource_deficit(context)
        dump_state_to_json(context)

        ## Connect worlds to a foundation
        # await connect_worlds_to_fnd(context, 4216)

        ## Attack worlds around center world
        # capital = next(world for world in full_state if isinstance(world, World) and world.sovereign_id == SOV_ID)
        # possible_victims = [world for world in full_state if isinstance(world, World) and 0 < dist(world.pos, capital.pos) <= 200 and world.sovereign_id == 1]
        #
        # await conquest_tasks.conquer_planets(context, possible_victims, generic_hammer_fleets={"hammer"}, nail_fleets={"nail"})

        ## Scan the galaxy
        # fleet_names = ("roomba","roomba2","roomba3","roomba4","roomba5","roomba6")
        # futures.extend(asyncio.create_task(explore_unexplored_regions(context, fleet_name)) for fleet_name in fleet_names)

        ## Send scout ships
        # await simple_tasks.scout_around_planet(context, center_world_id=99)
    finally:
        for future in futures:
            await future
        update_task.cancel()

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
