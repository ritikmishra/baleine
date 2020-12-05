import logging
import math
from typing import Tuple, Optional, List

from anacreonlib.types.response_datatypes import OwnedWorld, World

from scripts import utils
from scripts.context import AnacreonContext


async def find_next_sector_capital_worlds(context: AnacreonContext) -> List[World]:
    """
    Finds next place to put a sector capital based distance related criteria

    :param context: Context
    :return: List of worlds suitable to become a sector capital
    """
    logger = logging.getLogger("Sector Capital Search")

    dist_ideal: float = 420
    dist_tolerance: float = 12

    angle_increment: float = math.pi / 3
    angle_tolerance: float = math.radians(3)

    our_capitals: List[OwnedWorld] = [world for world in context.state
                                     if isinstance(world, OwnedWorld)
                                     and context.scenario_info_objects[world.designation].role in {"imperialCapital",
                                                                                                   "sectorCapital"}]

    def world_meets_location_requirements(world: World) -> Optional[Tuple[float, float]]:
        """
        :param world: Check if this world meets spatial requirements
        :return: Either None if it does not meet spatial requirements of being a sector cap
                 or Tuple of (angle error, distance error)
        """
        dist_to_closest_cap, cap = min((utils.dist(world.pos, cap.pos), cap) for cap in our_capitals)
        dist_error = abs(dist_to_closest_cap - dist_ideal)
        meets_dist_requirements = dist_to_closest_cap > 250 and dist_error <= dist_tolerance

        wx, wy = world.pos
        capx, capy = cap.pos

        angle_wrt_closest_cap = math.atan2(capy - wy, capx - wx)
        angle_error = angle_wrt_closest_cap % angle_increment
        meets_angle_requirements = (angle_error) <= angle_tolerance

        if meets_angle_requirements and meets_dist_requirements:
            return angle_error, dist_error

        return None

    worlds_in_good_locations: List[World] = [
        world for world in context.state
        if isinstance(world, World)
           and world.tech_level >= 5
           and world.sovereign_id == 1
           and world_meets_location_requirements(world) is not None
    ]

    worlds_in_good_locations.sort(key=world_meets_location_requirements)

    table_fstr = "{!s:6}{!s:6}{:40}{:30}{:15}{:15}"
    logger.info(utils.TermColors.BOLD + table_fstr.format("rank", "id", "name", "pos", "angle error", "dist error"))

    def format_tuple(tup: Tuple[float, ...]) -> str:
        return str(tuple(round(x, 1) for x in tup))

    for i, candidate in enumerate(worlds_in_good_locations):
        angle_error, dist_error = world_meets_location_requirements(candidate)

        logger.info(table_fstr.format(
            i,
            candidate.id,
            candidate.name,
            format_tuple(candidate.pos),
            "{:.04f}".format(angle_error),
            "{:.04f}".format(dist_error)
        ))

    return worlds_in_good_locations
