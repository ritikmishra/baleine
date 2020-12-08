from __future__ import annotations

import abc
import asyncio
import dataclasses
import logging
from asyncio import Future
from itertools import chain
from typing import List, Set, Union, Optional

from anacreonlib.types.response_datatypes import World, Fleet
from anacreonlib.types.type_hints import BattleObjective

from scripts import utils
from scripts.context import AnacreonContext, MilitaryForces
from scripts.tasks import NameOrId
from scripts.tasks.fleet_manipulation_utils import OrderedPlanetId, attack_fleet_walk
from scripts.utils import TermColors


@dataclasses.dataclass
class FleetBucket(abc.ABC):
    fleet_identifiers: Set[NameOrId]
    output_bucket: Optional[FleetBucket]
    bucket_name: str = dataclasses.field(init=False)
    queue: asyncio.Queue = dataclasses.field(default_factory=asyncio.PriorityQueue, init=False, repr=False)

    @abc.abstractmethod
    def calculate_order(self, context: AnacreonContext, forces: MilitaryForces, world: World) -> float:
        """Returns the priority of attacking this world for the priority queue"""
        raise NotImplementedError()

    @abc.abstractmethod
    def can_attack_world(self, context: AnacreonContext, forces: MilitaryForces, world: World) -> bool:
        """
        Determines if fleets in this bucket are allowed to attack a certain world

        :param context: anacreon context
        :param forces: forces of the world we are thinking about attacking
        :param world: world we are about to attack
        :return: true if we can attack it, false otherwise
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def should_decommission_fleet(self, context: AnacreonContext, fleet: Fleet) -> bool:
        """Determines if this fleet can continue or not"""
        raise NotImplementedError

    @abc.abstractmethod
    async def pilot_fleet(self, context: AnacreonContext, fleet_id: int):
        raise NotImplementedError()


@dataclasses.dataclass
class HammerFleetBucket(FleetBucket):
    output_bucket: FleetBucket
    bucket_name: str = "HAMMER"
    max_space_force: float = 50000

    def calculate_order(self, context: AnacreonContext, forces: MilitaryForces, world: World):
        return forces.space_forces

    def can_attack_world(self, context: AnacreonContext, forces: MilitaryForces, world: World):
        """Determines if fleets in this bucket are allowed to attack a certain world"""
        return forces.space_forces <= self.max_space_force

    def should_decommission_fleet(self, context: AnacreonContext, fleet: Fleet):
        """Determines if this fleet can continue or not"""
        fleet_forces = context.get_forces(fleet.resources)
        return fleet_forces.space_forces < self.max_space_force

    async def pilot_fleet(self, context: AnacreonContext, fleet_id: int):
        logger_name = f"{self.bucket_name} Fleet Manager (fleet ID {fleet_id})"
        logger = logging.getLogger(logger_name)

        fleet_walk_gen = attack_fleet_walk(context, fleet_id, objective=BattleObjective.SPACE_SUPREMACY,
                                           input_queue=self.queue, output_queue=self.output_bucket.queue,
                                           logger_name=logger_name)

        async for world_state_after_attacked in fleet_walk_gen:
            planet_id = world_state_after_attacked.id
            forces = context.get_forces(world_state_after_attacked.resources)

            if forces.space_forces <= 3:
                logger.info(f"Probably conquered {planet_id} :)")
                await fleet_walk_gen.asend(
                    self.output_bucket.calculate_order(context, forces, world_state_after_attacked))
            else:
                await fleet_walk_gen.asend(None)
                logger.info(f"Whatever happened on planet id {planet_id} was a failure most likely :(")

            this_fleet = next(fleet for fleet in context.state if isinstance(fleet, Fleet) and fleet.id == fleet_id)
            if self.should_decommission_fleet(context, this_fleet):
                logger.info("Deciding to decommission this fleet, presumably due to low forces")
                return


@dataclasses.dataclass
class AntiMissileHammerFleetBucket(HammerFleetBucket):
    bucket_name = "ANTIMISSILE"
    max_nonmissile_forces: float = 100

    def can_attack_world(self, context: AnacreonContext, forces: MilitaryForces, world: World):
        """Determines if fleets in this bucket are allowed to attack a certain world"""
        return (forces.space_forces <= self.max_space_force
                and (forces.space_forces - forces.missile_forces) < self.max_nonmissile_forces)


@dataclasses.dataclass
class NailFleetBucket(FleetBucket):
    bucket_name: str = "NAIL"
    max_ground_force: float = 100
    max_space_force: float = 1000
    output_bucket: None = None

    def calculate_order(self, context: AnacreonContext, forces: MilitaryForces, world: World) -> float:
        return forces.ground_forces

    def can_attack_world(self, context: AnacreonContext, forces: MilitaryForces, world: World):
        """Determines if fleets in this bucket are allowed to attack a certain world"""
        return forces.space_forces <= self.max_space_force and forces.ground_forces <= self.max_ground_force

    def should_decommission_fleet(self, context: AnacreonContext, fleet: Fleet):
        """Determines if this fleet can continue or not"""
        fleet_forces = context.get_forces(fleet.resources)
        return (fleet_forces.space_forces < 2 * self.max_space_force
                or fleet_forces.ground_forces < 2 * self.max_ground_force)

    async def pilot_fleet(self, context: AnacreonContext, fleet_id: int):
        logger_name = f"{self.bucket_name} Fleet Manager (fleet ID {fleet_id})"
        logger = logging.getLogger(logger_name)

        fleet_walk_gen = attack_fleet_walk(context, fleet_id, objective=BattleObjective.INVASION,
                                           input_queue=self.queue,
                                           input_queue_is_live=True, logger_name=logger_name)

        async for world_state_after_attacked in fleet_walk_gen:
            await fleet_walk_gen.asend(None)
            if world_state_after_attacked.sovereign_id == context.base_request.sovereign_id:
                logger.info(f"Conquered the planet ID {world_state_after_attacked.id}")

            this_fleet = next(fleet for fleet in context.state if isinstance(fleet, Fleet) and fleet.id == fleet_id)
            if self.should_decommission_fleet(context, this_fleet):
                logger.info("Deciding to decommission this fleet, presumably due to low forces")
                return


async def conquer_independents_around_id(context: AnacreonContext, center_planet: Set[NameOrId], *, radius=250,
                                         **kwargs):
    capitals = [world for world in context.state
                if isinstance(world, World)
                and world.efficiency > 20
                and (world.name in center_planet or world.id in center_planet)]
    possible_victims = [world for world in context.state
                        if isinstance(world, World)
                        and world.sovereign_id == 1
                        and world.resources is not None
                        and any(0 < utils.dist(world.pos, capital.pos) <= radius for capital in capitals)]

    return await conquer_planets(context, possible_victims, **kwargs)


async def conquer_planets(context: AnacreonContext, planets: Union[List[World], Set[NameOrId]], *,
                          generic_hammer_fleets: Set[NameOrId], nail_fleets: Set[NameOrId],
                          anti_missile_hammer_fleets: Set[NameOrId] = None):
    nail_bucket = NailFleetBucket(fleet_identifiers=nail_fleets)
    hammer_bucket = HammerFleetBucket(fleet_identifiers=generic_hammer_fleets, output_bucket=nail_bucket)
    anti_missile_hammer_bucket = AntiMissileHammerFleetBucket(fleet_identifiers=(anti_missile_hammer_fleets or set()),
                                                              output_bucket=nail_bucket)

    return await conquer_planets_using_buckets(context, planets,
                                               fleet_buckets=[nail_bucket, anti_missile_hammer_bucket, hammer_bucket])


async def conquer_planets_using_buckets(context: AnacreonContext, planets: Union[List[World], Set[NameOrId]], *,
                                        fleet_buckets: List[FleetBucket]):
    """
    Conquer all planets belonging to a certain list

    :param context: The anacreon context
    :param planets: The planets to invade
    :param fleet_buckets: Fleet buckets specifying stages of conquest
    """
    logger = logging.getLogger("Conquer planets")

    # Step 0: ensure that we are working with are all planet objects
    planet_objects: List[World]
    if all(isinstance(obj, World) for obj in planets):
        planet_objects = list(planets)

    else:
        planet_objects = [world for world in context.state if
                          isinstance(world, World) and (world.name in planets or world.id in planets)]

    # Step 1: ensure that we have ids for all the fleets
    def matches(obj: Union[World, Fleet], id_or_name_set: Set[NameOrId]) -> bool:
        """true if id/name set contains reference to the object"""
        return obj.name in id_or_name_set or obj.id in id_or_name_set

    fleets = [fleet for fleet in context.state if isinstance(fleet, Fleet)]

    def name_or_id_set_to_id_list(name_id_set: Set[NameOrId]) -> List[int]:
        if all(isinstance(fleet, int) for fleet in name_id_set):
            return list(name_id_set)
        else:
            return [fleet.id for fleet in fleets if matches(fleet, name_id_set)]

    fleet_ids_by_bucket: List[List[int]] = [name_or_id_set_to_id_list(bucket.fleet_identifiers) for bucket in
                                            fleet_buckets]

    logger.info("we are going to conquer the following planets")
    fstr = TermColors.BOLD + "{0!s:60}" + TermColors.ENDC + "{1!s:10}{2!s:10}{3!s:10}{4!s:10}{5!s:10}"
    logger.info(fstr.format("name", "gf", "sf", "missilef", "mode", "id"))

    # Step 2: Sort them into queues.
    for world in planet_objects:
        if world.resources is not None:
            force = context.get_forces(world.resources)
            for i, bucket in enumerate(fleet_buckets):
                if bucket.can_attack_world(context, force, world):
                    bucket.queue.put_nowait(OrderedPlanetId(bucket.calculate_order(context, force, world), world.id))
                    logger.info(
                        fstr.format(world.name, force.ground_forces, force.space_forces, force.missile_forces,
                                    bucket.bucket_name, world.id)
                    )
                    break  # break out of bucket iteration loop

    # Step 3: fire up coroutines
    def future_callback(fut: asyncio.Future):
        logger.info("A future has completed!")
        if fut.exception() is not None:
            logger.error("Error occured on future!")
            logger.error(fut.exception())

    logger.info("Firing up coroutines . . .")
    fleet_bucket_futures: List[List[Future]] = []

    for i, bucket in enumerate(fleet_buckets):
        futures_for_current_bucket = []
        for fleet_id in fleet_ids_by_bucket[i]:
            future = asyncio.create_task(bucket.pilot_fleet(context, fleet_id))
            future.add_done_callback(future_callback)
            futures_for_current_bucket.append(future)
        fleet_bucket_futures.append(futures_for_current_bucket)

    logger.info("Coroutines turned on, waiting for queues to empty . . .")
    await asyncio.gather(*(bucket.queue.join() for bucket in fleet_buckets))

    logger.info("Queues are empty")
    for future in chain(*fleet_bucket_futures):
        if not future.done():
            logger.warning("Had to cancel a coroutine ... why wasn't it done?")
            future.cancel()
