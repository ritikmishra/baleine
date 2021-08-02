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


def find_fleet(fleet_id: int, *, state=None, context=None):
    return next(
        fleet
        for fleet in (state or context.state)
        if isinstance(fleet, Fleet) and fleet.id == fleet_id
    )


async def wait_for_fleet(context: Anacreon, fleet_id: int) -> Fleet:
    fleet_obj = context.space_objects[fleet_id]
    assert isinstance(fleet_obj, Fleet)

    if fleet_obj.eta:
        while True:
            # the fleet is en route so we have to wait for it to finish
            await context.wait_for_any_update()
            fleet_obj = context.space_objects[fleet_id]
            assert isinstance(fleet_obj, Fleet)

            if fleet_obj.anchor_obj_id:
                break
            logging.info(f"Still waiting for fleet id {fleet_id} to get to destination")

    return fleet_obj


async def fleet_walk(
    context: Anacreon,
    fleet_id: int,
    *,
    input_queue: "asyncio.Queue[OrderedPlanetId]",
    output_queue: "Optional[asyncio.Queue[OrderedPlanetId]]" = None,
    input_queue_is_live: bool = False,
    logger_name: Optional[str] = None,
) -> AsyncGenerator[World, Optional[int]]:
    logger = logging.getLogger(logger_name or f"(fleet id {fleet_id}")

    while True:
        # Step 1: Find out which world we are going to
        order: float
        planet_id: int
        if input_queue_is_live:
            logger.info("Waiting to get next planet in queue")
            order, planet_id = await input_queue.get()
        else:
            try:
                order, planet_id = input_queue.get_nowait()
            except asyncio.queues.QueueEmpty:
                raise StopAsyncIteration()
        logger.info(f"Going to planet ID {planet_id} (order: {order})")

        # Step 2a: Send the fleet to go there
        await context.set_fleet_destination(fleet_id, planet_id)
        logger.info(f"Sent fleet to planet ID {planet_id}")

        # Step 2b: Wait for the fleet to arrive at the destination
        wait_for_fleet(context, fleet_id)

        # Step 3: Let our caller attack the world/whatever it needs to do
        logger.info(f"Fleet arrived at planet ID {planet_id}")

        # Step 4: Give our caller the world object and wait for them to send us back the ranking order
        planet_obj = context.space_objects[planet_id]
        assert isinstance(planet_obj, World)
        output_queue_ranking = yield planet_obj

        # Step 5: Our caller has sent us back if it succeeded or not
        input_queue.task_done()
        logger.info(f"Fleet is done working at planet ID {planet_id}")
        if output_queue is not None and output_queue_ranking is not None:
            output_queue.put_nowait(OrderedPlanetId(output_queue_ranking, planet_id))
            logger.info(f"Fleet put planet into child queue")

        # Presumably our caller is using us in an `async for` loop. This will wait for the loop to call
        # __anext__ on us.
        yield


async def attack_fleet_walk(
    context: Anacreon,
    fleet_id: int,
    *,
    objective: BattleObjective,
    input_queue: "asyncio.Queue[OrderedPlanetId]",
    output_queue: "Optional[asyncio.Queue[OrderedPlanetId]]" = None,
    input_queue_is_live: bool = False,
    logger_name: Optional[str] = None,
):

    logger = logging.getLogger(logger_name)
    fleet_walk_gen = fleet_walk(
        context,
        fleet_id,
        input_queue=input_queue,
        output_queue=output_queue,
        input_queue_is_live=input_queue_is_live,
        logger_name=logger_name,
    )

    async for world_to_attack in fleet_walk_gen:
        # Step 3: attack! AAAAAAAAAAAaaAAaaaaa
        planet_id = world_to_attack.id

        await context.attack(
            battlefield_id=planet_id,
            objective=objective,
            enemy_sovereign_ids=[world_to_attack.sovereign_id],
        )

        logger.info(f"Attack fleet arrived! We are attacking {planet_id}! RAAAAA")
        try:
            if context.space_objects[fleet_id].battle_plan is None:
                logger.warning("we attacked but battleplan is none?")
        except (StopIteration, AttributeError):
            logger.error("we could't find ourselves in the fleet response????")

        # Step 4: wait for battle to finish
        while True:
            await context.wait_for_get_objects()
            if context.space_objects[fleet_id].battle_plan is None:
                break
            logger.info(f"objective {str(objective)} on {planet_id} is in progress")

        world = context.space_objects[planet_id]

        # the client should send us
        output_queue_power = yield world

        await fleet_walk_gen.asend(output_queue_power)
        if world.sovereign_id == int(context._auth_info.sovereign_id):
            logger.info(f"Conquered the planet ID {planet_id}")

        yield
