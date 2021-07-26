import asyncio
import collections
import dataclasses
import itertools
import logging
from collections import OrderedDict
from math import fabs
from typing import Optional, List, Dict, Set, Callable, Tuple

from anacreonlib.exceptions import HexArcException
from anacreonlib.types.request_datatypes import (
    DesignateWorldRequest,
    RenameObjectRequest,
    SetTradeRouteRequest,
    TradeRouteTypes,
    StopTradeRouteRequest,
)
from anacreonlib.client_wrapper import AnacreonClientWrapper, ProductionInfo
from anacreonlib.types.response_datatypes import World, Trait, OwnedWorld, TradeRoute
from anacreonlib.types.scenario_info_datatypes import Category, ScenarioInfoElement
from anacreonlib.types.type_hints import TechLevel, Location
from rx.operators import first

from scripts import utils
from scripts.utils import TermColors


def get_free_work_units(
    world: World, tech_level: TechLevel = 7, efficiency: float = 0.9
) -> float:
    """
    Calculates the equilibrium number of work units that the world should have

    :param world: world in question
    :param tech_level: the equilibrium tech level to assume
    :param efficiency: the equilibrium efficiency to assume
    :return: the total amount of work units this planet will have in the long run, excluding work units decicated to survival structures
    """
    total_wu = 0.0
    for trait in world.traits:
        if isinstance(trait, Trait):
            total_wu += trait.work_units

    return total_wu


# def calculate_resource_production(world: World, resource: )

food_consumption_per_million_pop = {
    1: 0.198,
    2: 0.2673,
    3: 0.3608,
    4: 0.4873,
    5: 0.6578,
    6: 0.8877,
    7: 1.199,
    8: 1.6181,
    9: 2.1846,
    10: 2.9491,
}

durable_goods_consumption_per_million_pop = {
    1: 0.0,
    2: 0.0,
    3: 0.0,
    4: 0.165,
    5: 0.264,
    6: 0.42240000000000005,
    7: 0.6754,
    8: 1.0813000000000001,
    9: 1.7303000000000002,
    10: 2.7687,
}

luxury_consumption_per_million_pop = {
    1: 0.0,
    2: 0.0,
    3: 0.0,
    4: 0.0,
    5: 0.0,
    6: 0.0,
    7: 0.0143,
    8: 0.041800000000000004,
    9: 0.12430000000000001,
    10: 0.3718000000000001,
}

abundant_resource_to_desig_id_map = {
    13: 15,  # aetherium
    50: 52,  # chronimium
    59: 61,  # chtholon
    131: 133,  # hexacarbide
    261: 263,  # trillum
}

TL_8_WORLD_CLASSES = {
    92,
    271,
    113,
}  # ocean, earth-like, underground planets can build planetary arcologies


def get_preferred_resource_desig(
    context: AnacreonClientWrapper, world: World
) -> Optional[int]:
    """
    If this planet is abundant in any resources, recommend that it is designated as a
    resource extractor for that resource
    :return: None if planet is not abundant in any resources, or the preferred desig id if it is.
    """
    return next(
        (
            extractor_desig_id
            for abundant_trait_id, extractor_desig_id in abundant_resource_to_desig_id_map.items()
            if utils.world_has_trait(
                context.game_info.scenario_info, world, abundant_trait_id
            )
        ),
        None,
    )


def find_best_foundation_world(context: AnacreonClientWrapper) -> List[Tuple[int, int]]:
    """
    Find the world which is in trading distance range to the highest number of our planets

    returns list of (world_id, neighbor_world_count tuples)
    """
    university_designation = next(
        x
        for x in context.scenario_info_objects.values()
        if x.unid == "core.universityDesignation"
    )

    our_worlds = {
        world.id: world
        for world in context.space_objects.values()
        if isinstance(world, OwnedWorld)
    }

    fnd_worlds = {
        world_id: world
        for world_id, world in our_worlds.items()
        if world.designation == university_designation.id
    }

    def not_near_existing_fnd(world: OwnedWorld) -> bool:
        return all(utils.dist(world.pos, fnd.pos) > 200 for fnd in fnd_worlds.values())

    unconnected_worlds = {
        world_id: world
        for world_id, world in our_worlds.items()
        if not_near_existing_fnd(world)
    }

    def count_nearby_worlds(world: OwnedWorld) -> int:
        count = 0
        for other_id, other in unconnected_worlds.items():
            if world.id != other_id and utils.dist(other.pos, world.pos) < 200:
                count += 1

        return count

    world_counts = {
        world_id: count_nearby_worlds(world) for world_id, world in our_worlds.items()
    }

    return sorted(world_counts.items(), key=lambda wc: wc[1], reverse=True)


async def designate_low_tl_worlds(context: AnacreonClientWrapper) -> None:
    """
    Goes through all of our worlds and designates them if they are undesignated and low tech level
    :param context:
    :return: none
    """
    logger = logging.getLogger("Designate low TL worlds")

    autonomous_desig_id: int = context.game_info.find_by_unid(
        "core.autonomousDesignation"
    ).id
    cgaf_desig_id: int = context.game_info.find_by_unid(
        "core.consumerGoodsDesignation"
    ).id

    worlds_to_designate: List[OwnedWorld] = [
        world
        for world in context.space_objects.values()
        if isinstance(world, OwnedWorld)
        and world.tech_level < 5
        and world.designation == autonomous_desig_id
    ]

    for world in worlds_to_designate:
        preferred_desig = get_preferred_resource_desig(context, world) or cgaf_desig_id
        if (
            context.scenario_info_objects[preferred_desig].min_tech_level or 10
        ) > world.tech_level:
            preferred_desig = cgaf_desig_id
        try:
            logger.info(
                f"going to designate {world.name} (id {world.id}) as desig id {preferred_desig}"
            )
            await context.designate_world(world.id, preferred_desig)
        except HexArcException as e:
            logger.error(
                f"Encountered exception trying to designate world name `{world.name}` id {world.id}"
            )
            logger.error(e)


async def build_cluster(
    context: AnacreonClientWrapper, center_world_id: int, radius: float = 200
) -> None:
    logger = logging.getLogger("cluster builder")

    worlds = [world for world in context.space_objects.values() if isinstance(world, World)]
    center_world = next(world for world in worlds if world.id == center_world_id)
    worlds_in_cluster = [
        world
        for world in worlds
        if (
            isinstance(world, OwnedWorld)
            and utils.dist(world.pos, center_world.pos) <= radius
        )
    ]

    logger.info(
        f"There are {len(worlds_in_cluster)} worlds in the cluster surrounding {center_world.name} (id {center_world.id})"
    )

    for world in worlds_in_cluster:
        extractor_desig_id = get_preferred_resource_desig(context, world)
        if extractor_desig_id is not None and world.designation != extractor_desig_id:
            try:
                await context.designate_world(world.id, extractor_desig_id)
                logger.info(
                    f"Designated {world.name} (id {world.id}) as resource extractor"
                )
            except HexArcException:
                await context.rename_object(world.id, f"{world.id} future extractor {extractor_desig_id}")
                logger.info(
                    f"Marked {world.name} (id {world.id}) as resource extractor"
                )


async def connect_worlds_to_fnd(
    context: AnacreonClientWrapper, fnd_id: int, worlds: Optional[List[World]] = None
) -> None:
    logger = logging.getLogger(f"connect foundation id {fnd_id}")

    fnd_world = context.space_objects[fnd_id]
    assert isinstance(fnd_world, OwnedWorld)

    if worlds is None:
        worlds = [
            world
            for world in context.space_objects.values()
            if isinstance(world, OwnedWorld)
            and utils.dist(world.pos, fnd_world.pos) <= 200
            and world.id != fnd_id
            and world.tech_level <= 7
            and fnd_id not in (world.trade_route_partners or {})
        ]

    if len(worlds) == 0:
        logger.info("Cannot connect new worlds to foundation")
        return

    for world in worlds:
        planet_can_build_planetary_arcology = any(
            utils.world_has_trait(context.game_info.scenario_info, world, tl_8_class)
            for tl_8_class in TL_8_WORLD_CLASSES
        )
        tech_level = 8 if planet_can_build_planetary_arcology else 7
        logger.info(f"Importing TL {tech_level} to world {world.name} (id {world.id})")
        await context.set_trade_route(
            importer_id=world.id,
            exporter_id=fnd_id,
            alloc_type=TradeRouteTypes.TECH,
            alloc_value=str(tech_level)
        )


@dataclasses.dataclass(eq=True, frozen=True)
class WorldIdLocationPair:
    id: int
    name: str
    pos: Location


@dataclasses.dataclass
class NeedsProvidesInfo:
    """
    Contains information on what a class needs to import, and can provide to other worlds
    dicts are maps between resource ID and quantity
    """

    needs: Dict[int, float] = dataclasses.field(
        default_factory=lambda: collections.defaultdict(lambda: 0)
    )
    provides: Dict[int, float] = dataclasses.field(
        default_factory=lambda: collections.defaultdict(lambda: 0)
    )


async def calculate_resource_deficit(
    context: AnacreonClientWrapper,
    *,
    exports_only: bool = True,
    predicate: Optional[Callable[[OwnedWorld], bool]] = None,
) -> None:
    """
    Print out aggregated resource production info across all of our worlds

    :param context: context
    :param predicate:
    :return:
    """
    logger = logging.getLogger("calculate resource deficit/surplus")

    aggregate_prod_info: Dict[int, ProductionInfo] = collections.defaultdict(
        lambda: ProductionInfo()
    )

    if len(context.space_objects) == 0:
        await context.wait_for_any_update()

    our_worlds = [world for world in context.space_objects.values() if isinstance(world, OwnedWorld)]
    if predicate is not None:
        our_worlds = [world for world in our_worlds if predicate(world)]

    for world in our_worlds:
        exports = None
        if exports_only:
            world_desig: ScenarioInfoElement = context.scenario_info_objects[
                world.designation
            ]
            exports = world_desig.exports

        if exports_only:
            world_prod_info = {
                res_id: res_prod
                for res_id, res_prod in context.generate_production_info(world).items()
                if (exports is not None and res_id in exports)
                or (
                    exports is None
                    and context.scenario_info_objects[res_id].attack_value is not None
                )
            }
        else:
            world_prod_info = context.generate_production_info(world)

        for res_id, res_prod_info in world_prod_info.items():
            if res_id == 260:
                logger.info(
                    f"Taking trillum production on planet {world.name} (id {world.id}) into account"
                )
                logger.info(res_prod_info)
            aggregate_prod_info[res_id] += res_prod_info

    row_fstr = "{!s:40}{color}{!s:15}" + TermColors.ENDC + "{!s:15}{!s:15}"
    logger.info(
        f"{TermColors.BOLD}{row_fstr.format('res_name', 'surplus', 'sustainability', 'stockpile', color=TermColors.OKGREEN)}{TermColors.ENDC}"
    )
    for res_id, prod_info in aggregate_prod_info.items():
        res_name = context.scenario_info_objects[res_id].name
        surplus = prod_info.produced - prod_info.consumed
        if exports_only:
            surplus -= prod_info.exported
        watches_sustainable_for = (
            str(round(prod_info.available / surplus, 1)) if surplus < 0 else "forever"
        )
        color = TermColors.FAIL if surplus < 0 else TermColors.OKBLUE
        # logger.info(f"{res_name:40}{color:4}{surplus:10.1f}{TermColors.ENDC:4}{watches_sustainable_for!s:>10}{prod_info.available!s:10}")
        logger.info(
            row_fstr.format(
                res_name,
                str(round(surplus, 1)),
                watches_sustainable_for,
                "{:,}".format(prod_info.available),
                color=color,
            )
        )
