from __future__ import annotations

import abc
import asyncio
import dataclasses
import logging
from asyncio import Future
from itertools import chain
from scripts.tasks.simple_tasks import send_scouts_to_worlds
from typing import Any, Iterable, List, Set, Union, Optional, cast

from anacreonlib.anacreon import Anacreon, MilitaryForceInfo
from anacreonlib.types.response_datatypes import OwnedWorld, World, Fleet
from anacreonlib.types.type_hints import BattleObjective
import anacreonlib.utils

from scripts import utils
from scripts.tasks import NameOrId
from scripts.tasks.fleet_manipulation_utils import OrderedPlanetId
from scripts.utils import TermColors

from scripts.tasks.fleet_manipulation_utils_v2 import (
    attack_fleet_walk as attack_fleet_walk_v2,
)


@dataclasses.dataclass
class FleetBucket(abc.ABC):
    context: Anacreon
    fleet_identifiers: Set[int]
    output_bucket: Optional[FleetBucket]
    bucket_name: str = dataclasses.field(init=False)
    queue: "asyncio.Queue[OrderedPlanetId]" = dataclasses.field(
        default_factory=asyncio.PriorityQueue, init=False, repr=False
    )

    @abc.abstractmethod
    def _calculate_order(self, world: World) -> float:
        """Returns the priority of attacking this world for the priority queue"""
        raise NotImplementedError()

    @abc.abstractmethod
    def can_attack_world(self, world: World) -> bool:
        """
        Determines if fleets in this bucket are allowed to attack a certain world

        :param context: anacreon context
        :param forces: forces of the world we are thinking about attacking
        :param world: world we are about to attack
        :return: true if we can attack it, false otherwise
        """
        raise NotImplementedError()

    @abc.abstractmethod
    def should_decommission_fleet(self, fleet: Fleet) -> bool:
        """Determines if this fleet can continue or not"""
        raise NotImplementedError()

    @abc.abstractmethod
    async def _pilot_fleet(self, fleet_id: int) -> None:
        """Send a single fleet around doing its thing, conquering worlds or
        whatever

        Args:
            fleet_id (int): The fleet ID

        """
        raise NotImplementedError()

    def send_fleets_to_attack(self) -> "List[asyncio.Task[None]]":
        """Send all of the fleets in the bucket to go attack worlds in the input
        queue

        Returns:
            List[asyncio.Task[None]]: A list of the async tasks spawned. One task
            is spawned for each fleet
        """
        return [
            asyncio.create_task(self._pilot_fleet(fleet_id))
            for fleet_id in self.fleet_identifiers
        ]

    def add_world_to_queue(self, world: World) -> None:
        """Add a world to our input queue

        This will use our special sauce to rank worlds in order to add it to the
        priority queue

        Args:
            world (World): The world to add
        """
        self.queue.put_nowait(OrderedPlanetId(self._calculate_order(world), world.id))


@dataclasses.dataclass
class HammerFleetBucket(FleetBucket):
    output_bucket: FleetBucket
    bucket_name: str = "HAMMER"
    max_space_force: float = 50000

    def _calculate_order(
        self: HammerFleetBucket,
        world: World,
    ) -> float:
        # As a hammer, we like to attack worlds with low space forces first
        forces = self.context.calculate_forces(world)
        return forces.space_forces

    def can_attack_world(
        self: HammerFleetBucket,
        world: World,
    ) -> bool:
        """Determines if fleets in this bucket are allowed to attack a certain world"""
        forces = self.context.calculate_forces(world)
        return forces.space_forces <= self.max_space_force

    def should_decommission_fleet(self: HammerFleetBucket, fleet: Fleet) -> bool:
        """Determines if this fleet can continue or not"""
        fleet_forces = self.context.calculate_forces(fleet)
        return fleet_forces.space_forces < self.max_space_force

    async def _pilot_fleet(self: HammerFleetBucket, fleet_id: int) -> None:
        logger_name = f"{self.bucket_name} Fleet Manager (fleet ID {fleet_id})"
        logger = logging.getLogger(logger_name)

        async def on_attack_completed(world: World) -> None:
            planet_id = world.id
            forces = self.context.calculate_forces(world)

            if forces.space_forces <= 3:
                logger.info(f"Probably hammered {planet_id} :)")
                self.output_bucket.add_world_to_queue(world)

            else:
                logger.info(
                    f"Whatever happened on planet id {planet_id} was a failure most likely :("
                )

            this_fleet = self.context.space_objects[fleet_id]
            assert isinstance(this_fleet, Fleet)

            if self.should_decommission_fleet(this_fleet):
                logger.info(
                    "Deciding to decommission this fleet, presumably due to low forces"
                )
                return

        await attack_fleet_walk_v2(
            self.context,
            fleet_id,
            on_attack_completed,
            objective=BattleObjective.SPACE_SUPREMACY,
            input_queue=self.queue,
            input_queue_is_live=False,
            logger_name=logger_name,
        )


@dataclasses.dataclass
class AntiMissileHammerFleetBucket(HammerFleetBucket):
    bucket_name = "ANTIMISSILE"
    max_nonmissile_forces: float = 100

    def can_attack_world(self, world: World) -> bool:
        """Determines if fleets in this bucket are allowed to attack a certain world"""
        forces = self.context.calculate_forces(world)
        return (
            forces.space_forces <= self.max_space_force
            and (forces.space_forces - forces.missile_forces)
            < self.max_nonmissile_forces
        )


@dataclasses.dataclass
class NailFleetBucket(FleetBucket):
    bucket_name: str = "NAIL"
    max_ground_force: float = 100
    max_space_force: float = 1000
    output_bucket: None = None

    def _calculate_order(self, world: World) -> float:
        # As a hammer, we like to attack worlds with low ground forces first
        forces = self.context.calculate_forces(world)
        return forces.ground_forces

    def can_attack_world(self, world: World) -> bool:
        """Determines if fleets in this bucket are allowed to attack a certain world"""
        forces = self.context.calculate_forces(world)
        return (
            forces.space_forces <= self.max_space_force
            and forces.ground_forces <= self.max_ground_force
        )

    def should_decommission_fleet(self, fleet: Fleet) -> bool:
        """Determines if this fleet can continue or not"""
        fleet_forces = self.context.calculate_forces(fleet.resources)
        return (
            fleet_forces.space_forces < 2 * self.max_space_force
            or fleet_forces.ground_forces < 2 * self.max_ground_force
        )

    async def _pilot_fleet(self, fleet_id: int) -> None:
        logger_name = f"{self.bucket_name} Fleet Manager (fleet ID {fleet_id})"
        logger = logging.getLogger(logger_name)

        async def on_attack_completed(world: World) -> None:
            if world.sovereign_id == self.context._auth_info.sovereign_id:
                logger.info(f"Conquered the planet ID {world.id}")

            this_fleet = self.context.space_objects[fleet_id]
            assert isinstance(this_fleet, Fleet)

            if self.should_decommission_fleet(this_fleet):
                logger.info(
                    "Deciding to decommission this fleet, presumably due to low forces"
                )
                return

        await attack_fleet_walk_v2(
            self.context,
            fleet_id,
            on_attack_completed,
            objective=BattleObjective.INVASION,
            input_queue=self.queue,
            input_queue_is_live=True,
            logger_name=logger_name,
        )


async def conquer_independents_around_id(
    context: Anacreon,
    center_planet: Set[NameOrId],
    *,
    radius=250,
    generic_hammer_fleets: Set[NameOrId],
    nail_fleets: Set[NameOrId],
    anti_missile_hammer_fleets: Optional[Set[NameOrId]] = None,
) -> None:
    capitals = [
        world
        for world in context.space_objects.values()
        if isinstance(world, World)
        and world.efficiency > 20
        and (world.name in center_planet or world.id in center_planet)
    ]
    possible_victims = [
        world
        for world in context.space_objects.values()
        if isinstance(world, World)
        and world.sovereign_id == 1
        and world.resources is not None
        and any(
            0.0 < utils.dist(world.pos, capital.pos) <= radius for capital in capitals
        )
    ]

    await conquer_planets(
        context,
        possible_victims,
        generic_hammer_fleets=generic_hammer_fleets,
        nail_fleets=nail_fleets,
        anti_missile_hammer_fleets=anti_missile_hammer_fleets,
    )


async def conquer_planets(
    context: Anacreon,
    planets: Union[List[World], Set[NameOrId]],
    *,
    generic_hammer_fleets: Set[NameOrId],
    nail_fleets: Set[NameOrId],
    anti_missile_hammer_fleets: Optional[Set[NameOrId]] = None,
) -> None:
    def resolve_fleet_list(name_id_set: Set[NameOrId]) -> Set[int]:
        """Given a set of names or ids, create a set of IDs only"""
        if all(isinstance(fleet, int) for fleet in name_id_set):
            return cast(Set[int], name_id_set)
        else:
            ret: Set[int] = set()
            for name_or_id in name_id_set:
                if isinstance(name_or_id, int):
                    assert isinstance(
                        context.space_objects.get(name_or_id, None), Fleet
                    )
                    ret.add(name_or_id)
                else:
                    try:
                        # Find an object with the name
                        ret.add(
                            next(
                                obj.id
                                for obj in context.space_objects.values()
                                if isinstance(obj, Fleet)
                                and obj.sovereign_id == context.sov_id
                                and obj.name == name_or_id
                            )
                        )
                    except StopIteration as e:
                        raise ValueError(
                            f"Could not fleet with name {name_or_id!r}"
                        ) from e
            return ret

    nail_bucket = NailFleetBucket(
        context=context, fleet_identifiers=resolve_fleet_list(nail_fleets)
    )
    hammer_bucket = HammerFleetBucket(
        context=context,
        fleet_identifiers=resolve_fleet_list(generic_hammer_fleets),
        output_bucket=nail_bucket,
    )
    anti_missile_hammer_bucket = AntiMissileHammerFleetBucket(
        context=context,
        fleet_identifiers=resolve_fleet_list(anti_missile_hammer_fleets or set()),
        output_bucket=nail_bucket,
    )

    await conquer_planets_using_buckets(
        context,
        planets,
        fleet_buckets=[nail_bucket, anti_missile_hammer_bucket, hammer_bucket],
    )


async def conquer_planets_using_buckets(
    context: Anacreon,
    planets: Union[List[World], Set[NameOrId]],
    *,
    fleet_buckets: List[FleetBucket],
) -> None:
    """Conquer all listed planets using fleets in the provided buckets

    Args:
        context (Anacreon): API client
        planets (Union[List[World], Set[NameOrId]]): The worlds to conquer
        fleet_buckets (List[FleetBucket]): A list of fleet buckets, in reverse order of stages of conquest.
            That is, you should put the invading buckets first
    """
    logger = logging.getLogger("Conquer planets")

    # Step 0: ensure that we are working with are all planet objects
    planets_we_could_attack: List[World]
    if all(isinstance(obj, World) for obj in planets):
        planets_we_could_attack = list(cast(Iterable[World], planets))

    else:
        planets_we_could_attack = [
            world
            for world in context.space_objects.values()
            if isinstance(world, World)
            and (world.name in planets or world.id in planets)
        ]

    # Step 1: ensure that we have ids for all the fleets
    logger.info("we are going to conquer the following planets")
    fstr = (
        TermColors.BOLD
        + "{0!s:60}"
        + TermColors.ENDC
        + "{1!s:10}{2!s:10}{3!s:10}{4!s:10}{5!s:10}"
    )
    logger.info(fstr.format("name", "gf", "sf", "missilef", "mode", "id"))

    # Step 2: Sort them into queues.
    for world in planets_we_could_attack:
        if world.resources is not None:
            force = context.calculate_forces(world.resources)
            for bucket in fleet_buckets:
                if bucket.can_attack_world(world):
                    bucket.add_world_to_queue(world)
                    logger.info(
                        fstr.format(
                            world.name,
                            force.ground_forces,
                            force.space_forces,
                            force.missile_forces,
                            bucket.bucket_name,
                            world.id,
                        )
                    )
                    break  # break out of bucket iteration loop

    input("Press [ENTER] to continue, or Ctrl+C to cancel")
    # Step 3: fire up coroutines
    def future_callback(fut: asyncio.Future[Any]) -> None:
        logger.info("A future has completed!")
        if (exc := fut.exception()) is not None:
            logger.error("Error occured on future!", exc_info=exc)

    logger.info("Firing up coroutines . . .")
    fleet_bucket_futures: "List[asyncio.Task[None]]" = []

    for bucket in fleet_buckets:
        fleet_bucket_futures.extend(bucket.send_fleets_to_attack())

    for future in fleet_bucket_futures:
        future.add_done_callback(future_callback)

    logger.info("Coroutines turned on, waiting for queues to empty . . .")
    await asyncio.gather(*(bucket.queue.join() for bucket in fleet_buckets))

    logger.info(
        "Queues are empty, waiting five minutes before forcefully cancelling futures"
    )
    await asyncio.sleep(5 * 60)
    for future in fleet_bucket_futures:
        if not future.done():
            logger.warning("Had to cancel a coroutine ... why wasn't it done?")
            future.cancel()


async def find_nearby_independent_worlds(context: Anacreon) -> List[World]:
    """Find independent worlds that are jumpship-accessible to us

    Args:
        context (Anacreon): API Client

    Returns:
        List[World]: A list of independent worlds that are jumpship accessible
        to us
    """
    jump_beacon_trait_ids = {
        e.id
        for e in context.game_info.scenario_info
        if e.is_jump_beacon and e.id is not None
    }

    jump_beacon_location = [
        world.pos
        for world in context.space_objects.values()
        if isinstance(world, OwnedWorld)
        and any(
            anacreonlib.utils.world_has_trait(
                context.game_info.scenario_info, world, trait_id
            )
            for trait_id in jump_beacon_trait_ids
        )
    ]

    return [
        world
        for world in context.space_objects.values()
        if isinstance(world, World)
        and world.sovereign_id == 1 # Is a sovereign world
        and any(utils.dist(world.pos, jump_beacon_pos) <= 250 for jump_beacon_pos in jump_beacon_location) # Is in distance
    ]
