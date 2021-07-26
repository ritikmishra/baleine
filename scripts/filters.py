from typing import Callable
from anacreonlib.client_wrapper import AnacreonClientWrapper

from anacreonlib.types.response_datatypes import World

from scripts import utils
from scripts.tasks import NameOrId


def dist_filter(
    context: AnacreonClientWrapper, center_planet: NameOrId, radius: float
) -> Callable[[World], bool]:
    if isinstance(center_planet, int):
        world = context.space_objects[center_planet]
    else:
        world = next(w for w in context.space_objects.values() if w.name == center_planet)

    def filter_planet(other_world: World) -> bool:
        return utils.dist(world.pos, other_world.pos) <= radius

    return filter_planet
