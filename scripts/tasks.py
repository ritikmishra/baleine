import asyncio
import functools
import logging
import math
from typing import Tuple, List

from anacreonlib.anacreon_async_client import SetFleetDestinationRequest
from anacreonlib.types.response_datatypes import ReigningSovereign, AnacreonObject, UpdateObject, AnacreonObjectWithId, \
    Fleet, World
from rx.operators import first

from scripts.context import AnacreonContext
from scripts.creds import SOV_ID

Point = Tuple[float, float]


def exploration_grid_to_tuples(exploration: List[List[float]]) -> List[Point]:
    exploration_border_points: List[Point] = []
    for contour in exploration:
        # pair up successive elements
        exploration_border_points.extend(zip(contour[::2], contour[1::2]))
    return exploration_border_points


def merge_objects(
        old_objects: List[AnacreonObject],
        new_objects: List[AnacreonObject]) -> Tuple[List[AnacreonObject], UpdateObject]:
    replaced_ids = set(obj.id for obj in new_objects if isinstance(obj, AnacreonObjectWithId))
    new_update = next((obj for obj in new_objects if isinstance(obj, UpdateObject)), None)

    def obj_is_replaced(obj: AnacreonObject) -> bool:
        if isinstance(obj, AnacreonObjectWithId):
            obj: AnacreonObjectWithId
            return obj.id in replaced_ids
        elif isinstance(obj, UpdateObject):
            return new_update is not None
        return False

    ret = [obj for obj in old_objects if not obj_is_replaced(obj)]
    ret.extend(new_objects)

    return ret, new_update or next(obj for obj in old_objects if isinstance(obj, UpdateObject))


def dist(pointA: Point, pointB: Point) -> float:
    dist2 = sum(map(lambda a, b: (a - b) * (a - b), pointA, pointB))
    return math.sqrt(dist2)


async def explore_unexplored_regions(context: AnacreonContext, fleet_name: str):
    print("hi :)")

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
        our_border = exploration_grid_to_tuples(our_sovereign.exploration_grid.explored_outline)
        nearest_border_point: Point = min(our_border, key=functools.partial(dist, current_fleet_pos))

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
