import functools
import json
import logging
from typing import List, Callable, Dict, Optional

import matplotlib.pyplot as plt
import numpy as np
from anacreonlib.types.request_datatypes import SetFleetDestinationRequest, DeployFleetRequest
from anacreonlib.types.response_datatypes import Fleet, ReigningSovereign, World, AnacreonObject
from anacreonlib.types.type_hints import Location
from rx.operators import first

from scripts.context import AnacreonContext
from scripts.creds import SOV_ID
from scripts.utils import flat_list_to_tuples, dist, dict_to_flat_list


def _exploration_outline_to_points(outline) -> List[Location]:
    flattened = []
    for contour in outline:
        flattened.extend(contour)
    return flat_list_to_tuples(flattened)


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

        our_border = _exploration_outline_to_points(our_sovereign.exploration_grid.explored_outline)

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


async def graph_exploration_boundary(context: AnacreonContext):
    logger = logging.getLogger("exploration boundary grapher")
    our_sovereign = next(obj for obj in context.state if isinstance(obj, ReigningSovereign) and obj.id == SOV_ID)
    outline_list_of_pts = sorted(
        (flat_list_to_tuples(contour) for contour in our_sovereign.exploration_grid.explored_outline), key=len,
        reverse=True)

    for contour in outline_list_of_pts:
        outline_points = np.array(contour)
        outline_x, outline_y = outline_points.T

        plt.scatter(outline_x, outline_y, 0.5, marker=",")
        filename = f"exploration_len{len(contour)}.png"
        plt.savefig(filename, dpi=200)
        logger.info("Saved graph file! " + filename)


async def dump_state_to_json(context: AnacreonContext, state_subset: Optional[List[AnacreonObject]]=None, filename="objects.json"):
    logger = logging.getLogger("dump context state")

    if state_subset is None:
        state_subset = context.state

    just_raw_objects = [obj for obj in state_subset if isinstance(obj, dict)]

    logger.info("\n".join(map(repr, just_raw_objects)))

    all_raw_objects = []
    for obj in state_subset:
        if isinstance(obj, dict):
            all_raw_objects.append(obj)
        else:
            all_raw_objects.append(obj.dict(by_alias=True))
    with open(filename, "w") as f:
        json.dump(all_raw_objects, f, indent=4)

    logger.info("state dump complete!")


async def dump_scn_to_json(context: AnacreonContext, filename="scn_info.json"):
    logger = logging.getLogger("dump scn info")

    logger.info("getting scenario info")
    scn_info = await context.client.get_game_info(context.base_request.auth_token, context.base_request.game_id)
    logger.info("retrieved scnn info!")

    with open(filename, "w") as f:
        json.dump(scn_info, f, indent=4)
    logger.info("saved it to disk!")


async def send_fleet_to_worlds_meeting_predicate(context: AnacreonContext, source_obj_id: int,
                                                 resources: Dict[int, int], predicate: Callable[[World], bool], *,
                                                 logger=None):
    if logger is None:
        logger = logging.getLogger("send fleet to worlds meeting predicate")

    worlds_to_send_fleet_to = [world for world in context.state if isinstance(world, World) and predicate(world)]
    for world in worlds_to_send_fleet_to:
        partial_state = await context.client.deploy_fleet(
            DeployFleetRequest(source_obj_id=source_obj_id, resources=dict_to_flat_list(resources), **context.base_request.dict(by_alias=True)))

        print(partial_state)
        newest_fleet = max((fleet for fleet in partial_state if isinstance(fleet, Fleet)), key=lambda f: f.id)
        logger.info(f"Deployed fleet (name = '{newest_fleet.name}') (id = '{newest_fleet.id}')!")
        context.register_response(partial_state)

        partial_state = await context.client.set_fleet_destination(
            SetFleetDestinationRequest(obj_id=newest_fleet.id, dest=world.id, **context.base_request.dict(by_alias=True)))
        logger.info(f"Sent fleet id {newest_fleet.id} to planet (name = '{world.name}') (id = '{world.id}')")
        context.register_response(partial_state)


async def explore_around_planet(context: AnacreonContext, center_world_id: int, radius=200, *, resource_dict=None,
                                source_obj_id=None):
    logger = logging.getLogger(f"explore around planet {center_world_id}")

    if resource_dict is None:
        resource_dict = {102: 5}
    if source_obj_id is None:
        source_obj_id = center_world_id

    center_world = next(world for world in context.state if isinstance(world, World) and world.id == center_world_id)

    def is_world_in_radius(world: World) -> bool:
        return dist(world.pos, center_world.pos) < radius

    await send_fleet_to_worlds_meeting_predicate(context, source_obj_id, resource_dict, is_world_in_radius,
                                                 logger=logger)
