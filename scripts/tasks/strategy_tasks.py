from anacreonlib.types.type_hints import Location
import numpy as np 
from itertools import islice
import logging
import math
from typing import Any, Tuple, Optional, List, Union, NewType

from anacreonlib.types.response_datatypes import OwnedWorld, World

from scripts import utils
from scripts.context import AnacreonContext


BLocation = NewType("BLocation", Location)

def find_sec_cap_candidates(context: AnacreonContext, ideal_dist: float = 432, angle_increment: float = math.pi / 3) -> List[World]:
    logger = logging.getLogger("Sector Capital Search v2")

    # Working with two bases here
    # Basis A is the regular Anacreon coordinate system
    # Basis B is a basis where 1 B unit = <ideal_dist> A units, and the y vector angled up from the horizontal by <angle_inrcement> radians
    # In basis B, every integer pair of coordinates is a good spot to have a sector capital at.

    # np.matmul(btoa, blocation) = alocation - cap_pos
    btoa = np.array([
        [1, np.cos(angle_increment)],
        [0, np.sin(angle_increment)]
    ]) * ideal_dist # shape: (2, 2)

    atob = np.linalg.inv(btoa)

    our_worlds = [world
        for world in context.state
        if isinstance(world, OwnedWorld)]
    
    capital = next(world for world in our_worlds if context.scenario_info_objects[world.designation].role == "imperialCapital")

    print(capital)

    capital_pos_nparray = np.array([capital.pos]).T
    logger.info(f"the capital pos is {capital_pos_nparray}")

    def to_triangle_grid_coords(pos: Location) -> BLocation:
        pos_ndarray = np.array([pos]).T  # shape: (2, 1)

        b_pos_ndarray: np.ndarray = np.matmul(atob, pos_ndarray - capital_pos_nparray).flatten()
        return BLocation((b_pos_ndarray[0], b_pos_ndarray[1]))
    
    def pos_error(pos: Location) -> float:
        b_pos = to_triangle_grid_coords(pos)
        nearest_int_coords: BLocation = BLocation((round(b_pos[0]), round(b_pos[1])))
        dx, dy = (b_pos[0] - nearest_int_coords[0]), (b_pos[1] - nearest_int_coords[1])
        return math.sqrt((dx * dx) + (dy * dy))

    existing_sector_caps: List[OwnedWorld] = [world for world in our_worlds if context.scenario_info_objects[world.designation].role == "sectorCapital"]

    our_capitals = [capital, *existing_sector_caps]

    def is_world_far_from_capital(world: World) -> bool:
        return all(250 < utils.dist(world.pos, cap.pos) < (2 * ideal_dist) for cap in our_capitals)

    eligible_worlds = [
        world
        for world in context.state
        if isinstance(world, World)
        and world.tech_level >= 5
        and world.sovereign_id == 1
        and is_world_far_from_capital(world)
    ]

    eligible_worlds.sort(key=lambda world: pos_error(world.pos))

    table_fstr = "{!s:6}{!s:6}{:40}{:30}{:15}{:15}"
    logger.info(
        utils.TermColors.BOLD
        + table_fstr.format("rank", "id", "name", "a pos", "b pos", "error")
    )

    def format_tuple(tup: Tuple[float, ...]) -> str:
        return str(tuple(round(x, 1) for x in tup))

    for i, candidate in enumerate(eligible_worlds[:10]):
        logger.info(
            table_fstr.format(
                i,
                candidate.id,
                candidate.name,
                format_tuple(candidate.pos),
                format_tuple(to_triangle_grid_coords(candidate.pos)),
                "{:.04f}".format(pos_error(candidate.pos)),
            )
        )

    return eligible_worlds



async def find_next_sector_capital_worlds(context: AnacreonContext) -> List[World]:
    """
    Finds next place to put a sector capital based distance related criteria

    :param context: Context
    :return: List of worlds suitable to become a sector capital
    """
    logger = logging.getLogger("Sector Capital Search")

    dist_ideal: float = 432

    angle_increment: float = math.pi / 3

    our_capitals: List[OwnedWorld] = [
        world
        for world in context.state
        if isinstance(world, OwnedWorld)
        and context.scenario_info_objects[world.designation].role
        in {"imperialCapital", "sectorCapital"}
    ]

    def world_meets_location_requirements(
        world: World,
    ) -> Union[Tuple[float, float], Big]:
        """
        :param world: Check if this world meets spatial requirements
        :return: Either None if it does not meet spatial requirements of being a sector cap
                 or Tuple of (angle error, distance error)
        """
        dist_to_closest_cap, cap = min(
            (utils.dist(world.pos, cap.pos), cap) for cap in our_capitals
        )
        dist_error = abs(dist_to_closest_cap - dist_ideal)
        meets_dist_requirements = dist_to_closest_cap > 250

        wx, wy = world.pos
        capx, capy = cap.pos

        angle_wrt_closest_cap = math.atan2(capy - wy, capx - wx)
        angle_error = angle_wrt_closest_cap % angle_increment

        if meets_dist_requirements:
            return dist_error, math.degrees(angle_error)
        else:
            return Big()

    worlds_in_good_locations: List[World] = [
        world
        for world in context.state
        if isinstance(world, World)
        and world.tech_level >= 5
        and world.sovereign_id == 1
        and world_meets_location_requirements(world) is not None
    ]

    worlds_in_good_locations.sort(key=world_meets_location_requirements)

    table_fstr = "{!s:6}{!s:6}{:40}{:30}{:15}{:15}"
    logger.info(
        utils.TermColors.BOLD
        + table_fstr.format("rank", "id", "name", "pos", "angle error", "dist error")
    )

    def format_tuple(tup: Tuple[float, ...]) -> str:
        return str(tuple(round(x, 1) for x in tup))

    idx_world_error_tuple = enumerate(
        (world, errors)
        for world in worlds_in_good_locations
        if not isinstance((errors := world_meets_location_requirements(world)), Big)
    )
    for i, (candidate, (dist_error, angle_error)) in islice(idx_world_error_tuple, 10):
        logger.info(
            table_fstr.format(
                i,
                candidate.id,
                candidate.name,
                format_tuple(candidate.pos),
                "{:.04f}".format(angle_error),
                "{:.04f}".format(dist_error),
            )
        )

    return worlds_in_good_locations
