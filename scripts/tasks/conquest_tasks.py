import asyncio
import logging
from itertools import chain
from typing import List, Set, Optional, Union

from anacreonlib.types.response_datatypes import World, Fleet
from anacreonlib.types.type_hints import BattleObjective

from scripts.context import AnacreonContext
from scripts.tasks.fleet_manipulation_utils import OrderedPlanetId, attack_fleet_walk
from scripts.utils import TermColors

NameOrId = Union[int, str]


async def conquer_planets(context: AnacreonContext, planets: Union[List[World], Set[NameOrId]], *,
                          generic_hammer_fleets: Set[NameOrId], nail_fleets: Set[NameOrId],
                          anti_missile_hammer_fleets: Set[NameOrId] = None):
    """
    Conquer all planets belonging to a certain list

    :param context: The anacreon context
    :param generic_hammer_fleets: fleets that will destroy all of the space forces on a world to ~0 space forces
    :param nail_fleets: fleets of mostly transports that will conquer the world
    :param anti_missile_hammer_fleets: hammer fleets that should only conquer worlds with missiles on them.
    """
    logger = logging.getLogger("Conquer planets")

    # Using priority queues ensures that we attack weak planets first so we can give our all to strong planets
    hammer_missile_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
    hammer_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()
    nail_queue: asyncio.PriorityQueue = asyncio.PriorityQueue()

    # Step 0: ensure that we are working with are all planet objects
    planet_objects: List[World]
    if all(isinstance(obj, World) for obj in planets):
        planet_objects = list(planets)

    else:
        planet_objects = [world for world in context.state if
                          isinstance(world, World) and (world.name in planets or world.id in planets)]

    # Step 1: ensure that we have ids for all the fleets
    def matches(obj: Union[World, Fleet], id_or_name_set: Set[NameOrId]) -> bool:
        return obj.name in id_or_name_set or obj.id in id_or_name_set

    fleets = [fleet for fleet in context.state if isinstance(fleet, Fleet)]

    hammer_fleet_ids: List[int]
    if all(isinstance(fleet, int) for fleet in generic_hammer_fleets):
        hammer_fleet_ids = list(generic_hammer_fleets)
    else:
        hammer_fleet_ids = [fleet.id for fleet in fleets if matches(fleet, generic_hammer_fleets)]

    nail_fleet_ids: List[int]
    if all(isinstance(fleet, int) for fleet in nail_fleets):
        nail_fleet_ids = list(nail_fleets)
    else:
        nail_fleet_ids = [fleet.id for fleet in fleets if (fleet.name in nail_fleets or fleet.id in nail_fleets)]

    hammer_missile_fleet_ids: Optional[List[int]] = None
    if anti_missile_hammer_fleets is not None:
        if all(isinstance(fleet, int) for fleet in anti_missile_hammer_fleets):
            hammer_missile_fleet_ids = list(anti_missile_hammer_fleets)
        else:
            hammer_missile_fleet_ids = [fleet.id for fleet in fleets if matches(fleet, anti_missile_hammer_fleets)]

    logger.info("we are going to conquer the following planets")
    fstr = TermColors.BOLD + "{0!s:60}" + TermColors.ENDC + "{1!s:10}{2!s:10}{3!s:10}{4!s:10}{5!s:10}"
    logger.info(fstr.format("name", "gf", "sf", "missilef", "mode", "id"))
    # Step 2: Sort them into hammer, nail, and optionally anti missile hammer queues.
    for world in planet_objects:
        if world.resources is not None:
            force = context.get_forces(world.resources)
            if force.ground_forces <= 30:
                if force.space_forces <= 20:
                    nail_queue.put_nowait(OrderedPlanetId(force.ground_forces, world.id))
                    logger.info(
                        fstr.format(world.name, force.ground_forces, force.space_forces, force.missile_forces, "NAIL",
                                    world.id))
                elif force.space_forces <= 5000:
                    pair = OrderedPlanetId(force.space_forces, world.id)
                    if anti_missile_hammer_fleets is not None and force.space_forces - force.missile_forces <= 50:
                        hammer_missile_queue.put_nowait(pair)
                        logger.info(
                            fstr.format(world.name, force.ground_forces, force.space_forces, force.missile_forces,
                                        "ANTI MISSILE", world.id))
                    else:
                        hammer_queue.put_nowait(pair)
                        logger.info(
                            fstr.format(world.name, force.ground_forces, force.space_forces, force.missile_forces,
                                        "HAMMER", world.id))

    # Step 3: fire up coroutines
    def future_callback(fut: asyncio.Future):
        logger.info("A future has completed!")
        if fut.exception() is not None:
            logger.error("Error occured on future!")
            logger.error(fut.exception())

    logger.info("Firing up coroutines . . .")
    hammer_futures = []

    for hammer_fleet_id in hammer_fleet_ids:
        future = asyncio.create_task(manage_hammer_fleet(context, hammer_fleet_id, hammer_queue, nail_queue))
        future.add_done_callback(future_callback)
        hammer_futures.append(future)
    if hammer_missile_fleet_ids is not None:
        missile_only_futures = []
        for hammer_fleet_id in hammer_missile_fleet_ids:
            future = asyncio.create_task(
                manage_hammer_fleet(context, hammer_fleet_id, hammer_missile_queue, nail_queue))
            future.add_done_callback(future_callback)
            missile_only_futures.append(future)
        hammer_futures.extend(missile_only_futures)

    nail_futures = []
    for nail_fleet_id in nail_fleet_ids:
        future = asyncio.create_task(manage_nail_fleet(context, nail_fleet_id, nail_queue))
        future.add_done_callback(future_callback)
        nail_futures.append(future)

    logger.info("Coroutines turned on, waiting for queues to empty . . .")
    await asyncio.gather(
        hammer_queue.join(),
        hammer_missile_queue.join(),
        nail_queue.join()
    )

    logger.info("Queues are empty")
    for future in chain(hammer_futures, nail_futures):
        if not future.done():
            logger.warning("Had to cancel a coroutine ... why wasn't it done?")
            future.cancel()


async def manage_hammer_fleet(context: AnacreonContext, fleet_id: int, hammer_queue: asyncio.Queue,
                              nail_queue: asyncio.Queue):
    logger_name = f"Hammer Fleet Manager (fleet ID {fleet_id})"
    logger = logging.getLogger(logger_name)

    fleet_walk_gen = attack_fleet_walk(context, fleet_id, objective=BattleObjective.SPACE_SUPREMACY,
                                       input_queue=hammer_queue, output_queue=nail_queue,
                                       logger_name=logger_name)

    async for world_state_after_attacked in fleet_walk_gen:
        planet_id = world_state_after_attacked.id
        forces = context.get_forces(world_state_after_attacked.resources)

        if forces.space_forces <= 3:
            logger.info(f"Probably conquered {planet_id} :)")
            await fleet_walk_gen.asend(forces.ground_forces)
        else:
            await fleet_walk_gen.asend(None)
            logger.info(f"Whatever happened on planet id {planet_id} was a failure most likely :(")


async def manage_nail_fleet(context: AnacreonContext, fleet_id: int, nail_queue: asyncio.Queue):
    logger_name = f"Nail Fleet Manager (fleet ID {fleet_id})"
    logger = logging.getLogger(logger_name)

    fleet_walk_gen = attack_fleet_walk(context, fleet_id, objective=BattleObjective.INVASION, input_queue=nail_queue,
                                       input_queue_is_live=True, logger_name=logger_name)

    async for world_state_after_attacked in fleet_walk_gen:
        await fleet_walk_gen.asend(None)
        if world_state_after_attacked.sovereign_id == context.base_request.sovereign_id:
            logger.info(f"Conquered the planet ID {world_state_after_attacked.id}")
