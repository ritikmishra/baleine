import asyncio
from asyncio.tasks import Task
from anacreonlib import Anacreon
import logging
from pprint import pprint

from anacreonlib.types.response_datatypes import Fleet
from scripts.tasks.transportation_tasks import sell_stockpile_of_resource
from scripts.tasks.rally import rally_ships_to_world_id
from scripts.tasks.strategy_tasks import find_sec_cap_candidates
from scripts.tasks.garbage_collect_trade_routes import garbage_collect_trade_routes
from scripts.tasks.balance_trade_routes import balance_trade_routes
from typing import Any, Awaitable, List

from anacreonlib.types.request_datatypes import AnacreonApiRequest
from rx.operators import first, take

from scripts import utils, filters
from scripts.tasks import conquest_tasks, cluster_building
from scripts.tasks.cluster_building import (
    calculate_resource_deficit,
    connect_worlds_to_fnd,
    find_best_foundation_world,
)
from scripts.tasks.improvement_related_tasks import build_habitats_spaceports
from scripts.tasks.simple_tasks import (
    dump_scn_to_json,
    dump_state_to_json,
    scout_around_planet,
    zero_out_defense_structure_allocation,
)
from scripts.utils import TermColors

import scripts.creds

logging.basicConfig(
    level=logging.INFO,
    format=f"{TermColors.OKCYAN}%(asctime)s{TermColors.ENDC} - %(name)s - {TermColors.BOLD}%(levelname)s{TermColors.ENDC} - {TermColors.OKGREEN}%(message)s{TermColors.ENDC}",
)


async def main() -> None:
    logger = logging.getLogger("main")
    futures: List[Awaitable[None]] = []
    daemon_tasks: List[Task[None]] = []

    logger.info("Logging in ...")
    context = await Anacreon.log_in(
        scripts.creds.GAME_ID, scripts.creds.USERNAME, scripts.creds.PASSWORD
    )
    logger.info("Successfully logged in!")

    try:
        async def on_every_watch() -> None:
            """builds spaceports and designates low tl worlds on every watch"""
            while True:
                # wait 1 min for next watch update
                await context.wait_for_get_objects()
                await asyncio.gather(
                    build_habitats_spaceports(context),
                    cluster_building.designate_low_tl_worlds(context),
                )

        async def every_hour() -> None:
            """garbage collect trade routes every hour"""
            while True:
                # wait for 60 mins to pass
                for _ in range(60):
                    await context.wait_for_get_objects()
                await asyncio.gather(garbage_collect_trade_routes(context))

        async def every_40_mins() -> None:
            while True:
                for _ in range(40):
                    await context.wait_for_get_objects()
                await balance_trade_routes(context)

        daemon_tasks.append(asyncio.create_task(on_every_watch()))
        daemon_tasks.append(asyncio.create_task(every_hour()))
        daemon_tasks.append(asyncio.create_task(every_40_mins()))
        daemon_tasks.append(context.call_get_objects_periodically())

        logger.info(
            f"Number of fleets: {sum(isinstance(obj, Fleet) and obj.sovereign_id == context._auth_info.sovereign_id for obj in context.space_objects.values() )}"
        )
        ##//

        # hex_res_id = context.get_scn_info_el_unid("core.hexacarbide").id
        # assert hex_res_id is not None
        # worlds_with_hex = {
        #     world.id
        #     for world in context.our_worlds
        #     if world.resource_dict.get(hex_res_id, 0) > 5000
        # }

        # print(worlds_with_hex)

        # await sell_stockpile_of_resource(
        #     context, "hex haulers", hex_res_id, worlds_with_hex
        # )

        ##//

        # await zero_out_defense_structure_allocation(context)
        # await rally_ships_to_world_id(context, 170, None, 1111)

        # find_sec_cap_candidates(context)

        # await cluster_building.calculate_resource_deficit(context, exports_only=True)

        # balance trade routes
        # await balance_trade_routes(context)
        # await garbage_collect_trade_routes(context)

        ## Find a new foundation world
        # logger.info(f"The best foundation world ids are")
        # pprint(find_best_foundation_world(context))

        # await scout_around_planet(
        #     context,
        #     center_world_id=155,
        #     radius=250,
        #     resource_dict={101: 2},
        #     source_obj_id=5104,
        # )

        ## Sell a stockpile of resources to the mesophons
        # futures.append(asyncio.create_task(sell_stockpile_of_resource(context, "shuttle", "core.hexacarbide",
        #                                                               {"BR 1405 (hex)", "Lesser Nishapur (hex)"})))

        ## Connect worlds to a foundation
        # await connect_worlds_to_fnd(context, 264)
        # await connect_worlds_to_fnd(context, 692)

        # futures.append(asyncio.create_task(asyncio.sleep(8 * 3600)))

        ## Attack worlds around center world
        # futures.append(
        #     asyncio.create_task(
        #         conquest_tasks.conquer_independents_around_id(
        #             context,
        #             {"Eta Ophiuchii Seven", "Signal"},
        #             generic_hammer_fleets={
        #                 "hammer 1",
        #                 "hammer 2",
        #                 "hammer 3",
        #                 "hammer 4",
        #                 "hammer 5",
        #                 "hammer 6",
        #             },
        #             nail_fleets={
        #                 "nail",
        #                 "nail 2",
        #                 "nail 3",
        #                 "nail 4",
        #                 "nail 5",
        #                 "nail 6",
        #                 "nail 7",
        #                 "nail 8",
        #                 "nail 9",
        #                 "nail 10",
        #             },
        #             anti_missile_hammer_fleets={"ehammer 1", "ehammer 2"},
        #         )
        #     )
        # )

        ## Scan the galaxy
        # fleet_names = ("roomba","roomba2","roomba3","roomba4","roomba5","roomba6")
        # futures.extend(asyncio.create_task(explore_unexplored_regions(context, fleet_name)) for fleet_name in fleet_names)

        ## Send scout ships
        # await scout_around_planet(context, center_world_id=4926, source_obj_id=4651)
        # await scout_around_planet(context, center_world_id=1175, source_obj_id=4651)

        dump_state_to_json(context)
        await dump_scn_to_json(context)
    finally:
        for future in futures:
            await future
        for task in daemon_tasks:
            task.cancel()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(main())
