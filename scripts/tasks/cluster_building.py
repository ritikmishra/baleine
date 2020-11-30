import collections
import functools
import logging
from collections import Callable
from typing import Optional, List

from anacreonlib.exceptions import HexArcException
from anacreonlib.types.request_datatypes import DesignateWorldRequest, RenameObjectRequest, SetTradeRouteRequest, \
    TradeRouteTypes
from anacreonlib.types.response_datatypes import World, Trait
from anacreonlib.types.type_hints import TechLevel

from scripts import utils
from scripts.context import AnacreonContext


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
