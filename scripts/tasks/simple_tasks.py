import functools
import logging

from anacreonlib.types.request_datatypes import SetFleetDestinationRequest
from anacreonlib.types.response_datatypes import Fleet, ReigningSovereign, World
from anacreonlib.types.type_hints import Location
from rx.operators import first

from scripts.context import AnacreonContext
from scripts.creds import SOV_ID
from scripts.utils import flat_list_to_tuples, dist


async def explore_unexplored_regions(context: AnacreonContext, fleet_name: str):
    def find_fleet(objects):
        return next(obj for obj in objects if isinstance(obj, Fleet) and obj.name.strip() == fleet_name)

    logger = logging.getLogger(fleet_name)

    banned_world_ids = set()
    ban_candidate = None
    number_of_visits_to_ban_candidate = 0

    while True:
        our_sovereign: ReigningSovereign = next(obj for obj in context.state
                                                if isinstance(obj, ReigningSovereign)
                                                and obj.id == SOV_ID)

        current_fleet: Fleet = find_fleet(context.state)
        current_fleet_pos = current_fleet.position
        logger.info(f"Fleet currently at {current_fleet_pos}")
        our_border = flat_list_to_tuples(our_sovereign.exploration_grid.explored_outline)
        nearest_border_point: Location = min(our_border, key=functools.partial(dist, current_fleet_pos))

        worlds = iter(obj for obj in context.state if isinstance(obj, World) and obj.id not in banned_world_ids)
        nearest_planet_to_target: World = min(worlds, key=lambda w: dist(nearest_border_point, w.pos))

        if ban_candidate != nearest_planet_to_target.id:
            ban_candidate = nearest_planet_to_target.id
            number_of_visits_to_ban_candidate = 0
        else:
            number_of_visits_to_ban_candidate += 1
            if number_of_visits_to_ban_candidate >= 3:
                banned_world_ids.add(ban_candidate)
                ban_candidate = None

        logger.info(f"Fleet decided to go to planet {nearest_planet_to_target.name}")

        # send the fleet + refresh data
        new_resp = await context.client.set_fleet_destination(
            SetFleetDestinationRequest(obj_id=current_fleet.id, dest=nearest_planet_to_target.id, **context.auth)
        )
        context.register_response(new_resp)
        banned_world_ids.add(nearest_planet_to_target.id)

        logger.info(f"Sent fleet, waiting for the next watch to update")
        await context.watch_update_observable.pipe(first())
        logger.info(f"New watch, lets see what happened")