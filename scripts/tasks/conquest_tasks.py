import asyncio
import collections
import logging
from asyncio import QueueEmpty
from typing import List, Set, Optional, Union

from anacreonlib.types.request_datatypes import SetFleetDestinationRequest, AttackRequest, BattlePlan
from anacreonlib.types.response_datatypes import World, Fleet, UpdateObject
from anacreonlib.types.type_hints import BattleObjective
from rx.operators import first
from itertools import chain

from scripts.context import AnacreonContext
from scripts.utils import TermColors

NameOrId = Union[int, str]

ForcePlanetIdPair = collections.namedtuple("ForcePlanetIdPair", ["force", "id"])


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
    hammer_missile_queue = asyncio.PriorityQueue()
    hammer_queue = asyncio.PriorityQueue()
    nail_queue = asyncio.PriorityQueue()

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
                    nail_queue.put_nowait(ForcePlanetIdPair(force.ground_forces, world.id))
                    logger.info(fstr.format(world.name, force.ground_forces, force.space_forces, force.missile_forces, "NAIL", world.id))
                elif force.space_forces <= 5000:
                    pair = ForcePlanetIdPair(force.space_forces, world.id)
                    if anti_missile_hammer_fleets is not None and force.space_forces - force.missile_forces <= 50:
                        hammer_missile_queue.put_nowait(pair)
                        logger.info(fstr.format(world.name, force.ground_forces, force.space_forces, force.missile_forces, "ANTI MISSILE", world.id))
                    else:
                        hammer_queue.put_nowait(pair)
                        logger.info(fstr.format(world.name, force.ground_forces, force.space_forces, force.missile_forces, "HAMMER", world.id))

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
            future = asyncio.create_task(manage_hammer_fleet(context, hammer_fleet_id, hammer_missile_queue, nail_queue))
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


async def manage_hammer_fleet(context: AnacreonContext, fleet_id: int, hammer_queue: asyncio.Queue, nail_queue: asyncio.Queue):
    logger = logging.getLogger(f"Hammer Fleet Manager (fleet ID {fleet_id})")

    sf: float
    planet_id: int

    while True:
        # Step 0: find the planet we're going to
        try:
            sf, planet_id = hammer_queue.get_nowait()
        except QueueEmpty:
            return

        logger.info(f"Going to hammer planet ID {planet_id}")
        planet_sovereign_id: int = next(world.sovereign_id for world in context.state if isinstance(world, World) and world.id == planet_id)

        # Step 1: send fleet to the planet
        partial_state = await context.client.set_fleet_destination(SetFleetDestinationRequest(obj_id=fleet_id, dest=planet_id, **context.base_request.dict(by_alias=False)))
        context.register_response(partial_state)
        logger.info(f"Sent hammer to planet ID {planet_id}")

        get_this_fleet = lambda state: next(fleet for fleet in state if isinstance(fleet, Fleet) and fleet.id == fleet_id)

        # Step 2: wait for the fleet to get there
        while True:
            full_state = await context.watch_update_observable.pipe(first())
            if get_this_fleet(full_state).anchor_obj_id == planet_id:
                break
            logger.info(f"Still waiting for hammer to get to planet ID {planet_id}")

        # Step 3: attack! AAAAAAAAAAAaaAAaaaaa
        plan = BattlePlan(battlefield_id=planet_id, enemy_sovereign_ids=[planet_sovereign_id], objective=BattleObjective.SPACE_SUPREMACY)

        attack_req = AttackRequest(attacker_obj_id=planet_id, battle_plan=plan,
                                **context.base_request.dict(by_alias=False))
        partial_state = await context.client.attack(attack_req)
        context.register_response(partial_state)
        logger.info(f"Hammer arrived! We are attacking {planet_id}! RAAAAA")
        if get_this_fleet(context.state).battle_plan is None:
            logger.warning("we attacked but battleplan is none?")
            logger.info("\n".join(str(x) for x in partial_state))
        # Step 4: wait for battle to finish
        while True:
            full_state = await context.watch_update_observable.pipe(first())
            if get_this_fleet(full_state).battle_plan is None:
                hammer_queue.task_done()
                forces = context.get_forces(next(
                    world for world in context.state if isinstance(world, World) and world.id == planet_id).resources)
                if forces.space_forces <= 3:
                    logger.info(f"Probably conquered {planet_id} :)")
                    nail_queue.put_nowait((forces.ground_forces, planet_id))
                else:
                    logger.info(f"Whatever happened on planet id {planet_id} was a failure most likely :(")
                break
            logger.info(f"Invasion on {planet_id} is in progress")

async def manage_nail_fleet(context: AnacreonContext, fleet_id: int, nail_queue: asyncio.Queue):
    logger = logging.getLogger(f"Nail Fleet Manager (fleet ID {fleet_id})")
    gf: float
    planet_id: int

    while True:
        # Step 0: find the planet we're going to
        try:
            logger.info("Waiting for nail to get planet in queue")
            gf, planet_id = await nail_queue.get()
        except QueueEmpty:
            return

        logger.info(f"Going to nail planet ID {planet_id}")
        planet_sovereign_id: int = next(world.sovereign_id for world in context.state if isinstance(world, World) and world.id == planet_id)

        # Step 1: send fleet to the planet
        partial_state = await context.client.set_fleet_destination(SetFleetDestinationRequest(obj_id=fleet_id, dest=planet_id, **context.base_request.dict(by_alias=False)))
        context.register_response(partial_state)
        logger.info(f"Sent nail to planet ID {planet_id}")

        get_this_fleet = lambda state: next(fleet for fleet in state if isinstance(fleet, Fleet) and fleet.id == fleet_id)

        # Step 2: wait for the fleet to get there
        while True:
            full_state = await context.watch_update_observable.pipe(first())
            if get_this_fleet(full_state).anchor_obj_id == planet_id:
                break
            logger.info(f"Still waiting for nail to get to planet ID {planet_id}")


        # Step 3: attack! AAAAAAAAAAAaaAAaaaaa
        plan = BattlePlan(battlefield_id=planet_id, enemy_sovereign_ids=[planet_sovereign_id], objective=BattleObjective.INVASION)

        attack_req = AttackRequest(attacker_obj_id=planet_id, battle_plan=plan, **context.base_request.dict(by_alias=False))
        partial_state = await context.client.attack(attack_req)
        context.register_response(partial_state)
        logger.info(f"Nail arrived! We are attacking {planet_id}! RAAAAA")
        if get_this_fleet(context.state).battle_plan is None:
            logger.warning("we attacked but battleplan is none?")
            logger.info("\n".join(str(x) for x in partial_state))

        # Step 4: wait for battle to finish
        while True:
            full_state = await context.watch_update_observable.pipe(first())
            if get_this_fleet(full_state).battle_plan is None:
                nail_queue.task_done()
                logger.info(f"Probably conquered {planet_id} :)")
                break
            logger.info(f"Invasion on {planet_id} is in progress")


