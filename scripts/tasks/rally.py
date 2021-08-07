import asyncio
from typing import Callable, List, Optional
from anacreonlib.anacreon import Anacreon
import logging
from anacreonlib.types.response_datatypes import Fleet, OwnedWorld, World
from scripts.tasks import fleet_manipulation_utils

# TODO: make rally be able to draw from fleets as well


async def rally_ships_to_world_id(
    context: Anacreon,
    ship_resource_id: int,
    ship_qty: Optional[int],
    destination_world_id: int,
) -> List[int]:
    """Rally ships to a particular world ID

    Args:
        context (Anacreon): context
        ship_resource_id (int): Resource ID of the ship type to rally
        ship_qty (Optional[int]): Total number of ships that should
            arrive at the destination world, ignoring attrition. If None,
            send as many ships as possible from across the empire to these worlds
        destination_world_id (int): World ID to send

    Returns:
        List[int]: A list of all of the fleet IDs that were created

    Raises:
        ValueError: [description]
    """
    logger = logging.getLogger("rally ships")

    amount_of_resource_on_world: Callable[[World], float] = lambda w: w.resource_dict[
        ship_resource_id
    ]

    # List of worlds with the ship sorted by most to least
    worlds_with_resource = sorted(
        (
            world
            for world in context.space_objects.values()
            if isinstance(world, OwnedWorld) and ship_resource_id in world.resource_dict
        ),
        key=amount_of_resource_on_world,
    )

    # Check that we have enough ships across the empire to rally the desired amount
    total_amount_of_resource = sum(
        map(amount_of_resource_on_world, worlds_with_resource)
    )
    if ship_qty is not None and ship_qty > total_amount_of_resource:
        raise ValueError("not enough ships to rally!")

    qty_left_to_send = (
        ship_qty if ship_qty is not None else int(total_amount_of_resource)
    )
    fleets: List[Fleet] = []
    for world in worlds_with_resource:
        qty_to_deploy = int(min(amount_of_resource_on_world(world), qty_left_to_send))
        if qty_to_deploy < 1:
            break

        logger.info(
            f"Deploying {qty_to_deploy} ships from planet {world.name} (id {world.id})"
        )
        fleet = await context.deploy_fleet(world.id, [ship_resource_id, qty_to_deploy])
        assert fleet is not None
        fleets.append(fleet)

        qty_left_to_send -= qty_to_deploy

    for i, fleet in enumerate(fleets):
        logger.info(
            f"({i+1}/{len(fleets)}) sending fleet to world id {destination_world_id}"
        )
        await context.set_fleet_destination(fleet.id, destination_world_id)

    return [fleet.id for fleet in fleets]
    # done!


async def merge_fleets_in_transit(context: Anacreon, fleet_ids: List[int]) -> None:
    logger = logging.getLogger("merge fleets in transit")
    fleet_waiting_tasks = [
        asyncio.create_task(fleet_manipulation_utils.wait_for_fleet(context, fleet_id))
        for fleet_id in fleet_ids
    ]
    await asyncio.gather(*fleet_waiting_tasks)

    master_fleet, *fleets_to_merge = fleet_ids

    logger.info(
        f"fleets arrived, merging fleets into fleet id {master_fleet}"
    )

    for fleet in fleets_to_merge:
        fleet_resources = context.space_objects[fleet].resources
        assert fleet_resources is not None
        await context.transfer_fleet(master_fleet, fleet, fleet_resources)
