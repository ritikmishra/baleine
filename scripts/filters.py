from typing import Callable
from anacreonlib.anacreon import Anacreon

from anacreonlib.types.response_datatypes import World

from anacreonlib import utils
from scripts.tasks import NameOrId


def dist_filter(
    context: Anacreon, center_planet: NameOrId, radius: float
) -> Callable[[World], bool]:
    if isinstance(center_planet, int):
        world = context.space_objects[center_planet]
    else:
        world = next(
            w for w in context.space_objects.values() if w.name == center_planet
        )

    def filter_planet(other_world: World) -> bool:
        return utils.dist(world.pos, other_world.pos) <= radius

    return filter_planet


def world_is_not_high_tech_trace_tril(context: Anacreon, world: World) -> bool:
    return world.tech_level < 9 or not utils.world_has_trait(
        context.game_info.scenario_info, world, context.game_info.find_by_unid("core.trillumRare").id
    )

def world_sf_below(context: Anacreon, sf_threshold: float, world: World) -> bool:
    return (world.resources is not None) and context.calculate_forces(world).space_forces <= sf_threshold