import asyncio
from typing import Callable, List
from anacreonlib.types.request_datatypes import (
    DeployFleetRequest,
    SetFleetDestinationRequest,
    TransferFleetRequest,
)
import logging
from anacreonlib.types.response_datatypes import Fleet, World
from scripts.context import AnacreonContext
from scripts.tasks import fleet_manipulation_utils

# TODO: make rally support fleets as well


async def rally_ships_to_world_id(
    context: AnacreonContext,
    ship_resource_id: int,
    ship_qty: int,
    destination_world_id: int,
) -> None:
    logger = logging.getLogger("rally ships")

    amount_of_resource_on_world: Callable[
        [World], float
    ] = lambda w: w.resource_dict.get(ship_resource_id, -1)

    # List of worlds with the ship sorted by most to least
    worlds_with_resource = sorted(
        (
            world
            for world in context.our_worlds
            if ship_resource_id in world.resource_dict
        ),
        key=amount_of_resource_on_world,
    )

    if sum(map(amount_of_resource_on_world, worlds_with_resource)) < ship_qty:
        raise ValueError("not enough ships to rally!")

    qty_left_to_send: float = float(ship_qty)
    fleets: List[Fleet] = []
    for world in worlds_with_resource:
        qty_to_deploy = min(amount_of_resource_on_world(world), qty_left_to_send)
        if qty_to_deploy < 1:
            break

        deploy_req = DeployFleetRequest(
            source_obj_id=world.id,
            resources=[ship_resource_id, qty_to_deploy],
            **context.auth,
        )

        logger.info(
            f"Deploying {qty_to_deploy} ships from planet {world.name} (id {world.id})"
        )
        partial_state_containing_fleet = await context.client.deploy_fleet(deploy_req)

        context.register_response(partial_state_containing_fleet)

        fleet = next(
            obj for obj in partial_state_containing_fleet if isinstance(obj, Fleet)
        )
        fleets.append(fleet)

        qty_left_to_send -= qty_to_deploy

    fleet_waiting_tasks: "List[asyncio.Task[Fleet]]" = []

    for i, fleet in enumerate(fleets):
        set_dest_req = SetFleetDestinationRequest(
            obj_id=fleet.id, dest=destination_world_id, **context.auth
        )

        logger.info(
            f"({i+1}/{len(fleets)}) sending fleet to world id {destination_world_id}"
        )
        partial_state = await context.client.set_fleet_destination(set_dest_req)

        context.register_response(partial_state)
        fleet_waiting_tasks.append(
            asyncio.create_task(
                fleet_manipulation_utils.wait_for_fleet(context, fleet.id)
            )
        )

    fleets = await asyncio.gather(*fleet_waiting_tasks)

    master_fleet, *fleets_to_merge = fleets

    logger.info(
        f"fleets arrived, merging fleets into fleet {master_fleet.name} (id {master_fleet.id})"
    )

    # TODO: fix
    for fleet in fleets_to_merge:
        transfer_req = TransferFleetRequest(
            fleet_obj_id=fleet.id,
            dest_obj_id=master_fleet.id,
            resources=fleet.resources,
            **context.auth,
        )
        partial_state = await context.client.transfer_fleet(transfer_req)
        context.register_response(partial_state)

    # done!


async def test_deploy_fleet(context: AnacreonContext) -> None:
    partial_state = await context.client.deploy_fleet(
        DeployFleetRequest(source_obj_id=2018, resources=[164, 1], **context.auth)
    )
