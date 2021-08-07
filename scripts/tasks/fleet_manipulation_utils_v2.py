import asyncio
import logging
from typing import (
    Awaitable,
    Callable,
    Optional,
)

from anacreonlib.types.response_datatypes import Fleet, World
from anacreonlib.types.type_hints import BattleObjective

from anacreonlib.anacreon import Anacreon
from scripts.tasks.fleet_manipulation_utils import OrderedPlanetId


async def fleet_walk(
    context: Anacreon,
    fleet_id: int,
    on_arrival_at_world: Callable[[World], Awaitable[None]],
    *,
    input_queue: "asyncio.Queue[OrderedPlanetId]",
    input_queue_is_live: bool = False,
    logger_name: Optional[str] = None,
) -> None:
    """Send a fleet to each planet in a queue, and do some action
    ``on_arrival_at_world``on arrival

    Args:
        context (Anacreon): anacreon client
        fleet_id (int): The ID of the fleet to control
        on_arrival_at_world (Callable[[World], Awaitable[None]]): The function to call when the fleet arrives at a world.
            If putting worlds into an output queue, this may return a priority ranking.
        input_queue (asyncio.Queue[OrderedPlanetId]): The queue of planets to travel to
        input_queue_is_live (bool, optional): Indicates whether or not items are actively being added to the input queue. Defaults to False.
        logger_name (Optional[str], optional): Name of the logger to use. Defaults to None.

    Raises:
        StopAsyncIteration: [description]
    """
    logger = logging.getLogger(logger_name or f"(fleet id {fleet_id})")

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
        await context.set_fleet_destination(fleet_id, planet_id)

        # Step 2b: Wait for the fleet to arrive at the destination
        fleet = context.space_objects[fleet_id]
        assert isinstance(fleet, Fleet)
        if fleet.eta:
            while True:
                await context.wait_for_get_objects()
                fleet = context.space_objects[fleet_id]
                assert isinstance(fleet, Fleet)
                if fleet.anchor_obj_id == planet_id:
                    break
                logger.info(f"Still waiting for fleet to get to planet ID {planet_id}")

        # Step 3: Let our caller attack the world/whatever it needs to do
        logger.info(f"Fleet arrived at planet ID {planet_id}")

        # Step 4: Give our caller the world object and wait for them to send us back the ranking order
        world = context.space_objects[planet_id]
        assert isinstance(world, World)
        await on_arrival_at_world(world)

        # Step 5: Our caller has sent us back if it succeeded or not
        input_queue.task_done()
        logger.info(f"Fleet is done working at planet ID {planet_id}")


async def attack_fleet_walk(
    context: Anacreon,
    fleet_id: int,
    on_attack_completed: Callable[[World], Awaitable[None]],
    *,
    objective: BattleObjective,
    input_queue: "asyncio.Queue[OrderedPlanetId]",
    input_queue_is_live: bool = False,
    logger_name: Optional[str] = None,
) -> None:
    """Given an attack fleet and a queue of worlds, send the attack fleet to
    worlds coming in from the input queue, and attack with the desired objective.

    Then, if desired, put worlds into the output queue with a ranking determined
    by the ``output_queue_world_ranker`` parameter

    Args:
        context (Anacreon): API client
        fleet_id (int): Fleet to control
        on_attack_completed (Callable[[World], Awaitable[None]]): A function to call
        objective (BattleObjective): Whether to destroy defenses or invade the planet
        input_queue (asyncio.Queue[OrderedPlanetId]): Queue of planets to attack
        input_queue_is_live (bool, optional): Indicates whether or not we are expecting planets to be continually addded to the queue. Defaults to False.
        logger_name (Optional[str], optional): Logger name to use. Defaults to None.

    Returns:
        None: Returns when done
    """

    logger = logging.getLogger(logger_name)

    async def attack_worlds_on_arrival(world_to_attack: World) -> None:
        # Step 3: attack! AAAAAAAAAAAaaAAaaaaa
        planet_id = world_to_attack.id

        await context.attack(planet_id, objective, [world_to_attack.sovereign_id])

        logger.info(f"Attack fleet arrived! We are attacking {planet_id}! RAAAAA")
        try:
            if context.space_objects[fleet_id].battle_plan is None:
                logger.warning("we attacked but battleplan is none?")
        except KeyError:
            logger.error("we could't find ourselves in the fleet response????")
            raise

        # Step 4: wait for battle to finish
        while True:
            await context.wait_for_get_objects()
            if context.space_objects[fleet_id].battle_plan is None:
                break
            logger.info(f"objective {str(objective)} on {planet_id} is in progress")

        world = context.space_objects[planet_id]

        if world.sovereign_id == context.sov_id:
            logger.info(f"Conquered the planet ID {planet_id}")

        assert isinstance(world, World)
        await on_attack_completed(world)

    await fleet_walk(
        context,
        fleet_id,
        on_arrival_at_world=attack_worlds_on_arrival,
        input_queue=input_queue,
        input_queue_is_live=input_queue_is_live,
        logger_name=logger_name,
    )
