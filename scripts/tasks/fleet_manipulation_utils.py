from anacreonlib import Anacreon
import asyncio
import logging
from typing import Optional, AsyncGenerator, NamedTuple

from anacreonlib.types.response_datatypes import Fleet, World
from anacreonlib.types.type_hints import BattleObjective



class OrderedPlanetId(NamedTuple):
    """Allows for putting planet IDs into a PriorityQueue or similar construct"""

    order: float
    id: int


async def wait_for_fleet(context: Anacreon, fleet_id: int) -> Fleet:
    fleet_obj = context.space_objects[fleet_id]
    assert isinstance(fleet_obj, Fleet)

    if fleet_obj.eta:
        while True:
            # the fleet is en route so we have to wait for it to finish
            await context.wait_for_any_update()
            fleet_obj = context.space_objects[fleet_id]
            assert isinstance(fleet_obj, Fleet)

            if fleet_obj.eta is None:
                break
            logging.info(f"Still waiting for fleet id {fleet_id} to get to destination")

    return fleet_obj
