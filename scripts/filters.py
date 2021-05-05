from typing import Callable

from anacreonlib.types.response_datatypes import World

from scripts import utils
from scripts.context import AnacreonContext
from scripts.tasks import NameOrId


def dist_filter(
    context: AnacreonContext, center_planet: NameOrId, radius: float
) -> Callable[[World], bool]:
    world = next(
        world
        for world in context.state
        if isinstance(world, World)
        and (world.id == center_planet or world.name == center_planet)
    )

    def filter_planet(other_world: World) -> bool:
        return utils.dist(world.pos, other_world.pos) <= radius

    return filter_planet
