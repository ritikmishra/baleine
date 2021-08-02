from dataclasses import dataclass
from enum import Enum, IntEnum, auto
import pathlib
from contextlib import suppress
import asyncio
import functools
import json
import logging
from typing import Iterable, List, Callable, Dict, Optional, Sequence, Tuple

import matplotlib.pyplot as plt
import numpy as np
from anacreonlib.types.response_datatypes import (
    Fleet,
    OwnSovereign,
    OwnedWorld,
    Trait,
    World,
    AnacreonObject,
)
from anacreonlib import Anacreon
from anacreonlib.types.type_hints import Location
from anacreonlib.types.scenario_info_datatypes import Category, Role, ScenarioInfo
import anacreonlib.exceptions
from rx.operators import first

from scripts.creds import SOV_ID
from scripts.utils import flat_list_to_tuples, dist, dict_to_flat_list, world_has_trait


def _exploration_outline_to_points(outline: List[List[float]]) -> List[Location]:
    """Turn an outline from the API into a list of points representhing the boundary

    Args:
        outline (List[List[float]]): List of contours returned by the api. Each inner
        list constitutes a contour. Inner lists are of the form [x1, y1, x2, y2, ...]
        where the points (x1, y1), (x2, y2), etc are points on the boundary of the contour

    Returns:
        List[Location]: [description]
    """
    flattened = []
    for contour in outline:
        flattened.extend(contour)
    return flat_list_to_tuples(flattened)


async def explore_unexplored_regions(context: Anacreon, fleet_name: str) -> None:
    def find_fleet(objects: Iterable[AnacreonObject]) -> Fleet:
        return next(
            obj
            for obj in objects
            if isinstance(obj, Fleet) and obj.name.strip() == fleet_name
        )

    logger = logging.getLogger(fleet_name)

    banned_world_ids = set()
    ban_candidate = None
    number_of_visits_to_ban_candidate = 0

    while True:
        our_sovereign: OwnSovereign = next(
            obj
            for obj in context.sovereigns.values()
            if isinstance(obj, OwnSovereign) and obj.id == SOV_ID
        )

        current_fleet: Fleet = find_fleet(context.space_objects.values())
        current_fleet_pos = current_fleet.pos
        logger.info(f"Fleet currently at {current_fleet_pos}")

        assert our_sovereign.exploration_grid is not None
        our_border = _exploration_outline_to_points(
            our_sovereign.exploration_grid.explored_outline
        )

        nearest_border_point: Location = min(
            our_border, key=functools.partial(dist, current_fleet_pos)
        )

        worlds = (
            obj
            for obj in context.space_objects.values()
            if isinstance(obj, World) and obj.id not in banned_world_ids
        )
        nearest_planet_to_target: World = min(
            worlds, key=lambda w: dist(nearest_border_point, w.pos)
        )

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
        await context.set_fleet_destination(current_fleet.id, nearest_planet_to_target.id)
        banned_world_ids.add(nearest_planet_to_target.id)

        logger.info(f"Sent fleet, waiting for the next watch to update")
        await context.wait_for_get_objects()
        logger.info(f"New watch, lets see what happened")


async def graph_exploration_boundary(context: Anacreon) -> None:
    logger = logging.getLogger("exploration boundary grapher")
    our_sovereign = next(
        obj
        for obj in context.sovereigns.values()
        if isinstance(obj, OwnSovereign) and obj.id == SOV_ID
    )

    assert our_sovereign.exploration_grid is not None
    outline_list_of_pts = sorted(
        (
            flat_list_to_tuples(contour)
            for contour in our_sovereign.exploration_grid.explored_outline
        ),
        key=len,
        reverse=True,
    )

    for contour in outline_list_of_pts:
        outline_points = np.array(contour)
        outline_x, outline_y = outline_points.T

        plt.scatter(outline_x, outline_y, 0.5, marker=",")
        filename = f"exploration_len{len(contour)}.png"
        plt.savefig(filename, dpi=200)
        logger.info("Saved graph file! " + filename)


def _ensure_filename_exists(filename: str) -> None:
    filepath = pathlib.Path(filename)
    with suppress(FileExistsError):
        filepath.mkdir(parents=True, exist_ok=True)
        filepath.rmdir()
        filepath.touch(exist_ok=True)


def dump_state_to_json(
    context: Anacreon,
    state_subset: Optional[List[AnacreonObject]] = None,
    filename: str = "out/objects.json",
) -> None:
    logger = logging.getLogger("dump context state")

    if state_subset is None:
        state_subset = [
            *context.space_objects.values(),
            *context.sieges.values(),
            *context.sovereigns.values(),
        ]
        if context.update_obj is not None:
            state_subset.append(context.update_obj)

    just_raw_objects = [obj for obj in state_subset if isinstance(obj, dict)]

    logger.info("\n".join(map(repr, just_raw_objects)))

    all_raw_objects = []
    for obj in state_subset:
        if isinstance(obj, dict):
            all_raw_objects.append(obj)
        else:
            all_raw_objects.append(obj.dict(by_alias=True))

    _ensure_filename_exists(filename)

    with open(filename, "w") as f:
        json.dump(all_raw_objects, f, indent=4)

    with open("out/could_not_deserialize.json", "a") as f:
        json.dump([obj for obj in state_subset if isinstance(obj, dict)], f, indent=4)
    logger.info("state dump complete!")


async def dump_scn_to_json(
    context: Anacreon, filename: str = "out/scn_info.json"
) -> None:
    logger = logging.getLogger("dump scn info")

    logger.info("getting scenario info")
    scn_info: ScenarioInfo = context.game_info
    logger.info("retrieved scnn info!")

    _ensure_filename_exists(filename)

    with open(filename, "w") as f:
        json.dump(scn_info.dict(by_alias=True), f, indent=4)
    logger.info("saved it to disk!")


async def send_fleet_to_worlds_meeting_predicate(
    context: Anacreon,
    source_obj_id: int,
    resources: Dict[int, int],
    predicate: Callable[[World], bool],
    *,
    logger: Optional[logging.Logger] = None,
) -> None:
    if logger is None:
        logger = logging.getLogger("send fleet to worlds meeting predicate")

    worlds_to_send_fleet_to = [
        world
        for world in context.space_objects.values()
        if isinstance(world, World) and predicate(world)
    ]
    for world in worlds_to_send_fleet_to:
        newest_fleet = await context.deploy_fleet(source_obj_id, resources)
        assert newest_fleet is not None, "Could not find newest fleet??"

        logger.info(
            f"Deployed fleet (name = '{newest_fleet.name}') (id = '{newest_fleet.id}')!"
        )

        await context.set_fleet_destination(newest_fleet.id, world.id)

        logger.info(
            f"Sent fleet id {newest_fleet.id} to planet (name = '{world.name}') (id = '{world.id}')"
        )
        await asyncio.sleep(3)


async def scout_around_planet(
    context: Anacreon,
    center_world_id: int,
    radius: float = 200,
    *,
    resource_dict: Optional[Dict[int, int]] = None,
    source_obj_id: Optional[int] = None,
) -> None:
    """
    Sends fleets to all planets within a radius of the center planet

    :param context: Anacreon
    :param center_world_id: The world that should be the center of our circle
    :param radius: The radius of the circle
    :param resource_dict: the fleet composition. defaults to 5 vanguards.
    :param source_obj_id: the world from which the fleets should be deployed. defaults to the center world id.
    :return: None
    """
    logger = logging.getLogger(f"explore around planet {center_world_id}")

    if resource_dict is None:
        resource_dict = {101: 5}  # 5 helions
    if source_obj_id is None:
        source_obj_id = center_world_id

    center = context.space_objects[center_world_id]

    def is_world_in_radius(world: World) -> bool:
        return dist(world.pos, center.pos) < radius

    await send_fleet_to_worlds_meeting_predicate(
        context, source_obj_id, resource_dict, is_world_in_radius, logger=logger
    )


class ZeroOutDefenseStructureAllocationMode(IntEnum):
    AUTONOMOUS_WORLDS = auto()
    DESIGNATED_WORLDS = auto()
    ALL_WORLDS = auto()


async def zero_out_defense_structure_allocation(
    context: Anacreon,
    mode: ZeroOutDefenseStructureAllocationMode = ZeroOutDefenseStructureAllocationMode.DESIGNATED_WORLDS,
) -> None:
    logger = logging.getLogger("zero_out_defense_structure_allocation")

    autonomous_desig = context.game_info.find_by_unid("core.autonomousDesignation")

    defense_structure_ids = {
        kind.id: kind.name_desc
        for kind in context.scenario_info_objects.values()
        if kind.category == Category.IMPROVEMENT
        and (
            kind.role == Role.ORBITAL_DEFENSE_INDUSTRY
            or kind.role == Role.GROUND_DEFENSE_INDUSTRY
            or kind.role == Role.ACADEMY_INDUSTRY
        )
        and kind.id is not None
    }

    @dataclass
    class DeallocationOrder:
        log_txt: str
        world_id: int
        structure_id: int

    orders: List[DeallocationOrder] = []

    our_worlds = (world for world in context.space_objects.values() if isinstance(world, OwnedWorld))
    if mode == ZeroOutDefenseStructureAllocationMode.AUTONOMOUS_WORLDS:
        worlds_to_deallocate = (
            world for world in our_worlds if world.designation == autonomous_desig.id
        )
    elif mode == ZeroOutDefenseStructureAllocationMode.DESIGNATED_WORLDS:
        worlds_to_deallocate = (
            world for world in our_worlds if world.designation != autonomous_desig.id
        )
    else:
        worlds_to_deallocate = our_worlds

    for world in worlds_to_deallocate:
        defense_structures_on_world = (
            structure_id
            for structure_id in defense_structure_ids
            if world_has_trait(context.game_info.scenario_info, world, structure_id)
        )

        for structure_id in defense_structures_on_world:
            # Don't make orders if the allocation is already 0
            if (
                isinstance((trait := world.squashed_trait_dict[structure_id]), Trait)
                and trait.target_allocation == 0
            ):
                continue

            orders.append(
                DeallocationOrder(
                    f"Deallocate [{defense_structure_ids[structure_id]}] on world [{world.name}] (id {world.id})",
                    world_id=world.id,
                    structure_id=structure_id,
                )
            )

    for i, order in enumerate(orders):
        try:
            logger.info(f"({i + 1}/{len(orders)}) {order.log_txt}")
            await context.set_industry_alloc(order.world_id, order.structure_id, 0)
            await asyncio.sleep(2)
        except anacreonlib.exceptions.HexArcException:
            logger.error("could not complete the previous order!")
