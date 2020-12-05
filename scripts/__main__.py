import asyncio
import logging

from anacreonlib.types.request_datatypes import AnacreonApiRequest
from rx.operators import first

from scripts.context import AnacreonContext
from scripts.tasks import conquest_tasks
from scripts.tasks.cluster_building import calculate_resource_deficit
from scripts.tasks.improvement_related_tasks import build_habitats_spaceports
from scripts.tasks.simple_tasks import dump_state_to_json
from scripts.tasks.transportation_tasks import sell_stockpile_of_resource
from scripts.utils import TermColors

try:
    from scripts.creds import ACCESS_TOKEN, GAME_ID, SOV_ID
except ImportError:
    raise LookupError("Could not find creds.py in scripts package! Did you make one?")

auth = {
    "auth_token": ACCESS_TOKEN,
    "game_id": GAME_ID,
    "sovereign_id": SOV_ID
}

logging.basicConfig(level=logging.INFO,
                    format=f'{TermColors.OKCYAN}%(asctime)s{TermColors.ENDC} - %(name)s - {TermColors.BOLD}%(levelname)s{TermColors.ENDC} - {TermColors.OKGREEN}%(message)s{TermColors.ENDC}')


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

        # await scout_around_planet(context,
        #                           center_world_id=(await find_next_sector_capital_worlds(context))[0].id,
        #                           radius=250,
        #                           resource_dict={101: 2},
        #                           source_obj_id=99
        #                           )

        ## Sell a stockpile of resources to the mesophons
        futures.append(asyncio.create_task(sell_stockpile_of_resource(context, "shuttle", "core.hexacarbide",
                                                                      {"BR 1405 (hex)", "Lesser Nishapur (hex)"})))

        ## Connect worlds to a foundation
        # await connect_worlds_to_fnd(context, 4216)

        ## Attack worlds around center world
        futures.append(asyncio.create_task(
            conquest_tasks.conquer_independents_around_id(context,
                                                          "tears",
                                                          generic_hammer_fleets={"hammer"},
                                                          anti_missile_hammer_fleets={"Missile Yummer"},
                                                          nail_fleets={"nail"})
        ))

        ## Attack worlds around romere
        futures.append(asyncio.create_task(
            conquest_tasks.conquer_independents_around_id(context,
                                           4868,
                                           generic_hammer_fleets={"ldham"},
                                           nail_fleets={"ldnail"})
        ))

        ## Scan the galaxy
        # fleet_names = ("roomba","roomba2","roomba3","roomba4","roomba5","roomba6")
        # futures.extend(asyncio.create_task(explore_unexplored_regions(context, fleet_name)) for fleet_name in fleet_names)

        ## Send scout ships
        # await simple_tasks.scout_around_planet(context, center_world_id=99)

        dump_state_to_json(context)
    finally:
        for future in futures:
            await future
        update_task.cancel()


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
