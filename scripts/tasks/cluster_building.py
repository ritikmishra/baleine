import asyncio
import collections
import dataclasses
import functools
import itertools
import logging
from collections import OrderedDict
from typing import Optional, List, Dict, Set, Callable, Tuple

from anacreonlib.exceptions import HexArcException
from anacreonlib.types.request_datatypes import DesignateWorldRequest, RenameObjectRequest, SetTradeRouteRequest, \
    TradeRouteTypes, StopTradeRouteRequest
from anacreonlib.types.response_datatypes import World, Trait, OwnedWorld, TradeRoute
from anacreonlib.types.scenario_info_datatypes import Category
from anacreonlib.types.type_hints import TechLevel, Location

from scripts import utils
from scripts.context import AnacreonContext, ProductionInfo
from scripts.utils import TermColors


def get_free_work_units(world: World, tech_level: TechLevel = 7, efficiency: float = .9):
    """
    Calculates the equilibrium number of work units that the world should have

    :param world: world in question
    :param tech_level: the equilibrium tech level to assume
    :param efficiency: the equilibrium efficiency to assume
    :return: the total amount of work units this planet will have in the long run, excluding work units decicated to survival structures
    """
    total_wu = 0
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
    10: 2.9491
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
    10: 2.7687
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
    10: 0.3718000000000001
}

abundant_resource_to_desig_id_map = {
    13: 15,  # aetherium
    50: 52,  # chronimium
    59: 61,  # chtholon
    131: 133,  # hexacarbide
    261: 263,  # trillum
}

TL_8_WORLD_CLASSES = {92, 271, 113}  # ocean, earth-like, underground planets can build planetary arcologies


async def build_cluster(context: AnacreonContext, center_world_id: int, radius: float = 200):
    logger = logging.getLogger("cluster builder")

    worlds = [world for world in context.state if isinstance(world, World)]
    center_world = next(world for world in worlds if world.id == center_world_id)
    worlds_in_cluster = [world
                         for world in worlds
                         if (world.sovereign_id == int(context.base_request.sovereign_id)
                             and utils.dist(world.pos, center_world.pos) <= radius)]

    logger.info(
        f"There are {len(worlds_in_cluster)} worlds in the cluster surrounding {center_world.name} (id {center_world.id})")

    world_has_trait: Callable[[World, int], bool] = functools.partial(utils.world_has_trait,
                                                                      context.game_info.scenario_info)
    for world in worlds_in_cluster:
        for abundant_trait_id, extractor_desig_id in abundant_resource_to_desig_id_map.items():
            if world_has_trait(world, abundant_trait_id) and world.designation != extractor_desig_id:
                try:
                    req = DesignateWorldRequest(source_obj_id=world.id, new_designation=extractor_desig_id,
                                                **context.auth)
                    partial_state = await context.client.designate_world(req)
                    logger.info(f"Designated {world.name} (id {world.id}) as resource extractor")
                except HexArcException:
                    req = RenameObjectRequest(obj_id=world.id,
                                              name=f"{world.id} future extractor {extractor_desig_id}",
                                              **context.auth)
                    partial_state = await context.client.rename_object(req)
                    logger.info(f"Marked {world.name} (id {world.id}) as resource extractor")
                context.register_response(partial_state or [])


async def connect_worlds_to_fnd(context: AnacreonContext, fnd_id: int, worlds: Optional[List[World]] = None):
    logger = logging.getLogger(f"connect foundation id {fnd_id}")

    fnd_world = next(world for world in context.state if isinstance(world, World) and world.id == fnd_id)
    if worlds is None:
        worlds = [world for world in context.state
                  if isinstance(world, World)
                  and utils.dist(world.pos, fnd_world.pos) <= 200
                  and world.sovereign_id == int(context.base_request.sovereign_id)
                  and world.id != fnd_id
                  and world.tech_level <= 7
                  and fnd_id not in world.trade_route_partners]

    if len(worlds) == 0:
        logger.info("Cannot connect new worlds to foundation")
        return

    for world in worlds:
        planet_can_build_planetary_arcology = any(
            utils.world_has_trait(context.game_info.scenario_info, world, tl_8_class)
            for tl_8_class in TL_8_WORLD_CLASSES
        )
        tech_level = 8 if planet_can_build_planetary_arcology else 7
        req = SetTradeRouteRequest(importer_id=world.id, exporter_id=fnd_id, alloc_type=TradeRouteTypes.TECH,
                                   alloc_value=str(tech_level), **context.auth)
        logger.info(f"Importing TL {tech_level} to world {world.name} (id {world.id})")
        print(req.json(by_alias=True))
        partial_state = await context.client.set_trade_route(req)
        # except HexArcException as e:
        #     logger.error(str(e))
        # else:
        #     print(partial_state)
        context.register_response(partial_state or [])


@dataclasses.dataclass(eq=True, frozen=True)
class WorldIdLocationPair:
    id: int
    name: str
    pos: Location


@dataclasses.dataclass
class NeedsProvidesInfo:
    """
    Contains information on what a class needs to import, and can provide to other worlds
    dicts are maps between
    """
    needs: Dict[int, float] = dataclasses.field(default_factory=dict)
    provides: Dict[int, float] = dataclasses.field(default_factory=dict)


async def decentralized_trade_route_manager(context: AnacreonContext, *, dry_run: bool = False,
                                            clean_slate: bool = False, throttle: float = 0):
    """Manage a system of decentralized trade routes"""
    logger = logging.getLogger("decentralized trade route manager")

    our_worlds: Dict[int, World] = {world.id: world for world in context.state if
                                    isinstance(world, World) and world.sovereign_id == int(
                                        context.base_request.sovereign_id)}

    logger.info(f"Altering the trade routes of {len(our_worlds)} worlds")
    needs_provides_data: Dict[WorldIdLocationPair, NeedsProvidesInfo] = {}

    """Tuples should have ids in sorted order"""
    cancelled_trade_routes: Dict[Tuple[int, int], StopTradeRouteRequest] = dict()

    trade_routes: Dict[WorldIdLocationPair, List[SetTradeRouteRequest]] = collections.defaultdict(list)

    def resource_providers_near_planet(res_id: int, world_id_pos: WorldIdLocationPair):
        filtered_providers = OrderedDict(sorted(((k, v)
                                                 for k, v in needs_provides_data.items()
                                                 if res_id in v.provides.keys()
                                                 and k != world_id_pos
                                                 and utils.dist(world_id_pos.pos, k.pos) < 200),
                                                key=lambda t: utils.dist(world_id_pos.pos, t[0].pos)))
        return filtered_providers

    foundation_worlds = set(world.id for world in our_worlds.values() if
                            context.scenario_info_objects[world.designation].unid == "core.university")
    logger.info(f"There are {len(foundation_worlds)} foundation worlds")
    # Step 1: Identify what each planet needs
    for world_id, world in our_worlds.items():
        # world_prod_data is a dict from resources to prod data.
        # we filter out things with attack value in case the planet is building GDMs/such
        world_prod_data: Dict[int, ProductionInfo] = {res_id: prod_data
                                                      for res_id, prod_data in
                                                      context.generate_production_info(world).items()
                                                      if context.scenario_info_objects[res_id].attack_value is None}

        world_desig = context.scenario_info_objects[world.designation]
        world_exports: Set[int] = set(world_desig.exports or [])
        world_needs_provides = NeedsProvidesInfo()

        assert world_desig.category == Category.DESIGNATION

        for res_id, prod_data in world_prod_data.items():
            if res_id in world_exports:
                world_needs_provides.provides[res_id] = (prod_data.produced - prod_data.consumed_optimal)
                if not clean_slate:
                    world_needs_provides.provides[res_id] -= prod_data.exported_optimal
            else:
                world_needs_provides.needs[res_id] = prod_data.consumed_optimal
                if not clean_slate:
                    world_needs_provides.needs[res_id] -= prod_data.imported_optimal

        world_id_loc = WorldIdLocationPair(id=world.id, pos=world.pos, name=world.name)
        needs_provides_data[world_id_loc] = world_needs_provides

        if clean_slate and world.trade_route_partners is not None:
            trade_partner_id: int
            trade_route: TradeRoute
            for trade_partner_id, trade_route in world.trade_route_partners.items():
                if trade_route.reciprocal:
                    trade_route = our_worlds[trade_route.partner_obj_id].trade_route_partners[world.id]
                if ((cancel_key := tuple(sorted((world_id, trade_partner_id)))) not in cancelled_trade_routes.keys()
                        and trade_route.import_tech is None
                        and trade_route.export_tech is None):
                    logger.info(
                        f"Cancelling a trade route between {world.name} and {our_worlds[trade_partner_id].name}")
                    cancelled_trade_routes[cancel_key] = StopTradeRouteRequest(planet_id_a=world_id,
                                                                               planet_id_b=trade_partner_id,
                                                                               **context.auth)

    # Step 2: Figure out imports
    for world_id_pos, needs_provides in needs_provides_data.items():

        needed_resources = needs_provides.needs
        for i, needed_res_id in enumerate(needed_resources.keys()):
            total_needed_qty = needed_resources[needed_res_id]
            if total_needed_qty <= 0:
                continue
            logger.info(
                f"Planet Name {world_id_pos.name:35} (id {world_id_pos.id!s:6}) needs resource {context.get_scn_info_el_name(needed_res_id):20} qty {total_needed_qty}")

            # For each of our needs, find a nearby planet that can fulfill our needs
            res_providers = resource_providers_near_planet(needed_res_id, world_id_pos)

            for provider_id_pos, provider_needs_provides in res_providers.items():

                # If this planet can provide us some stuff, import from them
                provideable_resources = provider_needs_provides.provides
                if total_needed_qty * 0.1 <= provideable_resources[needed_res_id]:
                    # The planet can provide us with everything we need
                    qty_to_import = min(needed_resources[needed_res_id], provideable_resources[needed_res_id])

                    provideable_resources[needed_res_id] -= qty_to_import
                    needed_resources[needed_res_id] -= qty_to_import
                    percent_qty_satisfied = str(round((qty_to_import / total_needed_qty) * 100 + 0.1, 1))

                    logger.info(
                        f"\t - importing {qty_to_import!s:7} (%{percent_qty_satisfied!s:4}) from planet {provider_id_pos.name:35}")
                    trade_routes[world_id_pos].append(
                        SetTradeRouteRequest(importer_id=world_id_pos.id,
                                             alloc_type=TradeRouteTypes.CONSUMPTION,
                                             exporter_id=provider_id_pos.id,
                                             alloc_value=percent_qty_satisfied,
                                             res_type_id=needed_res_id,
                                             **context.auth)
                    )

                    if needed_resources[needed_res_id] <= 0:
                        break

    logger.info(f"Going to cancel {len(cancelled_trade_routes)} trade routes")
    for i, cancel_order in enumerate(cancelled_trade_routes.values()):
        logger.info(f"Cancelled trade route {i}/{len(cancelled_trade_routes)}")
        if not dry_run:
            partial_state = await context.client.stop_trade_route(cancel_order)
            context.register_response(partial_state)
            logger.info(repr(cancel_order))

    total_traderoutes_made = 0
    total_number_of_trade_routes = len(list(itertools.chain(*trade_routes.values())))
    logger.info(f"Going to make {total_number_of_trade_routes} trade routes!")
    # Step 3: import!
    for import_orders in trade_routes.values():
        for trade_route_order in import_orders:
            logger.info(f"Created trade route {total_traderoutes_made}/{total_number_of_trade_routes}")
            if not dry_run:
                partial_state = await context.client.set_trade_route(trade_route_order)
                context.register_response(partial_state)
                logger.info(repr(trade_route_order))
            total_traderoutes_made += 1
            if not dry_run and throttle is not None:
                await asyncio.sleep(throttle)

    logger.info("Complete!")


async def calculate_resource_deficit(context: AnacreonContext, *,
                                     predicate: Optional[Callable[[OwnedWorld], bool]] = None):
    """
    Print out aggregated resource production info across all of our worlds

    :param context: context
    :param predicate:
    :return:
    """
    logger = logging.getLogger("calculate resource deficit/surplus")

    aggregate_prod_info: Dict[int, ProductionInfo] = collections.defaultdict(lambda: ProductionInfo())

    our_worlds = [world for world in context.state if isinstance(world, OwnedWorld)]
    if predicate is not None:
        our_worlds = [world for world in context.state if predicate(world)]

    for world in our_worlds:
        world_prod_info = context.generate_production_info(world)
        for res_id, res_prod_info in world_prod_info.items():
            aggregate_prod_info[res_id] += res_prod_info

    logger.info(f"{TermColors.BOLD}{'res_name':20}{'surplus':10}{TermColors.ENDC}")
    for res_id, prod_info in aggregate_prod_info.items():
        res_name = context.get_scn_info_el_name(res_id)
        surplus = prod_info.produced - prod_info.consumed
        watches_sustainable_for = prod_info.available / surplus if surplus < 0 else "forever"
        color = TermColors.FAIL if surplus < 0 else TermColors.OKGREEN
        logger.info(f"{res_name:20}{color}{surplus!s:10}{TermColors.ENDC}{watches_sustainable_for!s:14}")
