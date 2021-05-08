import asyncio
from asyncio.tasks import Task
import logging
from pprint import pprint
from scripts.tasks.garbage_collect_trade_routes import garbage_collect_trade_routes
from scripts.tasks.balance_trade_routes import balance_trade_routes
from typing import Any, Awaitable, List

from anacreonlib.types.request_datatypes import AnacreonApiRequest
from rx.operators import first, take

from scripts import utils, filters
from scripts.context import AnacreonContext
from scripts.tasks import conquest_tasks, cluster_building
from scripts.tasks.cluster_building import (
    calculate_resource_deficit,
    connect_worlds_to_fnd,
    find_best_foundation_world,
)
from scripts.tasks.improvement_related_tasks import build_habitats_spaceports
from scripts.tasks.simple_tasks import dump_state_to_json
from scripts.utils import TermColors

try:
    from scripts.creds import ACCESS_TOKEN, GAME_ID, SOV_ID
except ImportError:
    raise LookupError("Could not find creds.py in scripts package! Did you make one?")

auth = {"auth_token": ACCESS_TOKEN, "game_id": GAME_ID, "sovereign_id": SOV_ID}

logging.basicConfig(
    level=logging.INFO,
    format=f"{TermColors.OKCYAN}%(asctime)s{TermColors.ENDC} - %(name)s - {TermColors.BOLD}%(levelname)s{TermColors.ENDC} - {TermColors.OKGREEN}%(message)s{TermColors.ENDC}",
)


async def main() -> None:
    logger = logging.getLogger("main")
    futures: List[Awaitable[None]] = []
    daemon_tasks: List[Task[None]] = []

    context = await AnacreonContext.create(AnacreonApiRequest(**auth))
    try:

        async def on_every_watch() -> None:
            """builds spaceports and designates low tl worlds on every watch"""
            while True:
                # wait 1 min for next watch update
                await context.watch_update_observable.pipe(first())
                await asyncio.gather(
                    build_habitats_spaceports(context),
                    cluster_building.designate_low_tl_worlds(context),
                )

        async def every_hour() -> None:
            """garbage collect trade routes every hour"""
            while True:
                # wait for 60 mins to pass
                await context.watch_update_observable.pipe(take(60))
                await asyncio.gather(garbage_collect_trade_routes(context))

        async def every_40_mins() -> None:
            while True:
                await context.watch_update_observable.pipe(take(40))
                await balance_trade_routes(context)

        daemon_tasks.append(asyncio.create_task(on_every_watch()))
        daemon_tasks.append(asyncio.create_task(every_hour()))
        daemon_tasks.append(asyncio.create_task(every_40_mins()))
        daemon_tasks.append(asyncio.create_task(context.periodically_update_objects()))

        logger.info("Waiting to get objects")
        full_state = await context.watch_update_observable.pipe(first())
        logger.info("Got objects!")

        # await cluster_building.calculate_resource_deficit(context, exports_only=True)

        # balance trade routes
        await balance_trade_routes(context)

        ## Find a new foundation world
        # logger.info(f"The best foundation world ids are")
        # pprint(find_best_foundation_world(context))

        # await scout_around_planet(context,
        #                           center_world_id=(await find_next_sector_capital_worlds(context))[0].id,
        #                           radius=250,
        #                           resource_dict={101: 2},
        #                           source_obj_id=99
        #                           )

        ## Sell a stockpile of resources to the mesophons
        # futures.append(asyncio.create_task(sell_stockpile_of_resource(context, "shuttle", "core.hexacarbide",
        #                                                               {"BR 1405 (hex)", "Lesser Nishapur (hex)"})))

        ## Connect worlds to a foundation
        # await connect_worlds_to_fnd(context, 3085)

        # futures.append(asyncio.create_task(asyncio.sleep(8 * 3600)))

        ## Attack worlds around center world
        # futures.append(asyncio.create_task(
        #     conquest_tasks.conquer_independents_around_id(context,
        #                                                   {"tears", "Romere"},
        #                                                   generic_hammer_fleets={"hammer"},
        #                                                   nail_fleets={"nail"})
        # ))

        ## Scan the galaxy
        # fleet_names = ("roomba","roomba2","roomba3","roomba4","roomba5","roomba6")
        # futures.extend(asyncio.create_task(explore_unexplored_regions(context, fleet_name)) for fleet_name in fleet_names)

        ## Send scout ships
        # await simple_tasks.scout_around_planet(context, center_world_id=99)

        dump_state_to_json(context)
    finally:
        for future in futures:
            await future
        for task in daemon_tasks:
            task.cancel()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
