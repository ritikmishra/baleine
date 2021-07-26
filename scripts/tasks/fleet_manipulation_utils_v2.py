import asyncio
import collections
import logging
from typing import Any, Callable, Coroutine, Optional, AsyncGenerator, NamedTuple

from anacreonlib.types.request_datatypes import (
    SetFleetDestinationRequest,
    BattlePlan,
    AttackRequest,
)
from anacreonlib.types.response_datatypes import Fleet, World
from anacreonlib.types.type_hints import BattleObjective
from rx.operators import first

from scripts.context import AnacreonContext


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


async def fleet_walk(
    context: AnacreonContext,
    fleet_id: int,
    on_arrival_at_world: Callable[[World], Coroutine[Any, Any, Optional[int]]],
    *,
    input_queue: "asyncio.Queue[OrderedPlanetId]",
    output_queue: "Optional[asyncio.Queue[OrderedPlanetId]]" = None,
    input_queue_is_live: bool = False,
    logger_name: Optional[str] = None,
) -> None:
    """Send a fleet to each planet in a queue, and do some action on arrival

    Args:
        context (AnacreonContext): Cnotext
        fleet_id (int): The ID of the fleet to control
        on_arrival_at_world (Callable[[World], Coroutine[Any, Any, Optional[int]]]): The function to call when the fleet arrives at a world.
            If putting worlds into an output queue, this may return a priority ranking.
        input_queue (asyncio.Queue[OrderedPlanetId]): The queue of planets to travel to
        output_queue (Optional[asyncio.Queue[OrderedPlanetId]], optional): If not None, put planets into this queue after we visit them. Defaults to None.
        input_queue_is_live (bool, optional): Indicates whether or not items are actively being added to the input queue. Defaults to False.
        logger_name (Optional[str], optional): Name of the logge rto use. Defaults to None.

    Raises:
        StopAsyncIteration: [description]
    """
    logger = logging.getLogger(logger_name or f"(fleet id {fleet_id})")

    # def _find_this_fleet(state=None):
    #     return next(fleet for fleet in (state or context.state)
    #                 if isinstance(fleet, Fleet) and fleet.id == fleet_id)

    while True:
        # Step 1: Find out which world we are going to
        order: float
        planet_id: int
        if input_queue_is_live:
            logger.info("Waiting to get next planet in queue")

            # TODO: nail fleets wait indefinitely here!
            order, planet_id = await input_queue.get()
        else:
            try:
                order, planet_id = input_queue.get_nowait()
            except asyncio.queues.QueueEmpty:
                return None
        logger.info(f"Going to planet ID {planet_id} (order: {order})")

        # Step 2a: Send the fleet to go there
        destination_request = SetFleetDestinationRequest(
            obj_id=fleet_id, dest=planet_id, **context.base_request.dict(by_alias=False)
        )

        logger.debug("-- DESTINATION REQUEST JSON --")
        logger.debug(destination_request.json(by_alias=True))
        partial_state = await context.client.set_fleet_destination(destination_request)
        logger.debug("-- PARTIAL STATE RESPONSE --")
        logger.debug(partial_state)

        context.register_response(partial_state)
        logger.info(f"Sent fleet to planet ID {planet_id}")

        # Step 2b: Wait for the fleet to arrive at the destination
        fleet_obj: Fleet = find_fleet(fleet_id, context=context)
        if fleet_obj.eta:
            while True:
                # the fleet is en route so we have to wait for it to finish
                full_state = await context.watch_update_observable.pipe(first())
                if find_fleet(fleet_id, state=full_state).anchor_obj_id == planet_id:
                    break
                logger.info(f"Still waiting for fleet to get to planet ID {planet_id}")

        # Step 3: Let our caller attack the world/whatever it needs to do
        logger.info(f"Fleet arrived at planet ID {planet_id}")

        # Step 4: Give our caller the world object and wait for them to send us back the ranking order
        output_queue_ranking = await on_arrival_at_world(
            next(
                world
                for world in context.state
                if isinstance(world, World) and world.id == planet_id
            )
        )

        # Step 5: Our caller has sent us back if it succeeded or not
        input_queue.task_done()
        logger.info(f"Fleet is done working at planet ID {planet_id}")
        if output_queue is not None and output_queue_ranking is not None:
            output_queue.put_nowait(OrderedPlanetId(output_queue_ranking, planet_id))
            logger.info(f"Fleet put planet into child queue")


async def attack_fleet_walk(
    context: AnacreonContext,
    fleet_id: int,
    output_queue_world_ranker: Callable[[World], Optional[int]],
    *,
    objective: BattleObjective,
    input_queue: "asyncio.Queue[OrderedPlanetId]",
    output_queue: "Optional[asyncio.Queue[OrderedPlanetId]]" = None,
    input_queue_is_live: bool = False,
    logger_name: Optional[str] = None,
) -> None:

    logger = logging.getLogger(logger_name)

    async def attack_worlds_on_arrival(world_to_attack: World) -> Optional[int]:
        # Step 3: attack! AAAAAAAAAAAaaAAaaaaa
        planet_id = world_to_attack.id
        plan = BattlePlan(
            battlefield_id=planet_id,
            enemy_sovereign_ids=[world_to_attack.sovereign_id],
            objective=objective,
        )

        attack_req = AttackRequest(
            attacker_obj_id=planet_id,
            battle_plan=plan,
            **context.base_request.dict(by_alias=False),
        )
        partial_state = await context.client.attack(attack_req)
        context.register_response(partial_state)

        logger.info(f"Attack fleet arrived! We are attacking {planet_id}! RAAAAA")
        try:
            if find_fleet(fleet_id, context=context).battle_plan is None:
                logger.warning("we attacked but battleplan is none?")
                logger.info("\n".join(str(x) for x in partial_state))
        except (StopIteration, AttributeError):
            logger.error("we could't find ourselves in the fleet response????")

        # Step 4: wait for battle to finish
        while True:
            full_state = await context.watch_update_observable.pipe(first())
            if find_fleet(fleet_id, state=full_state).battle_plan is None:
                break
            logger.info(f"objective {str(objective)} on {planet_id} is in progress")

        world = next(
            world
            for world in context.state
            if isinstance(world, World) and world.id == planet_id
        )

        if world.sovereign_id == context.base_request.sovereign_id:
            logger.info(f"Conquered the planet ID {planet_id}")

        return output_queue_world_ranker(world)

    await fleet_walk(
        context,
        fleet_id,
        on_arrival_at_world=attack_worlds_on_arrival,
        input_queue=input_queue,
        output_queue=output_queue,
        input_queue_is_live=input_queue_is_live,
        logger_name=logger_name,
    )
