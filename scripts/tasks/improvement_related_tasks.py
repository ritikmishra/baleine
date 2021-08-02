from dataclasses import dataclass
import logging
from typing import List

from anacreonlib import Anacreon
from anacreonlib.exceptions import HexArcException
from anacreonlib.types.request_datatypes import AlterImprovementRequest
from anacreonlib.types.response_datatypes import OwnedWorld, World


@dataclass(frozen=True)
class ConstructionOrder:
    planet_id: int
    planet_name: str
    improvement_id: int
    improvement_name: str


async def build_habitats_spaceports(context: Anacreon) -> None:
    """
    Builds habitat structures and spaceports on all planets on which they can be built
    :param context: Anacreon
    :return: None
    """
    logger = logging.getLogger("build habitats and spaceports")

    construction_orders: List[ConstructionOrder] = []

    logger.debug("Beginning to iterate through planets")
    for planet in context.space_objects.values():
        if isinstance(planet, OwnedWorld):
            valid_improvements = context.get_valid_improvement_list(planet)
            for trait in valid_improvements:
                try:
                    if (
                        trait.role == "lifeSupport"
                        or trait.unid == "core.spaceport"
                    ):
                        planet_name = planet.name
                        structure_name = trait.name_desc
                        assert trait.id is not None and structure_name is not None
                        construction_orders.append(
                            ConstructionOrder(
                                planet.id, planet_name, trait.id, structure_name
                            )
                        )
                except KeyError:
                        pass

    for construction_order in construction_orders:
        logger.info(
            f"Building a {construction_order.improvement_name} on planet {construction_order.planet_name} (planet ID {construction_order.planet_id} )"
        )
        try:
            await context.build_improvement(
                improvement_id=construction_order.improvement_id,
                world_obj_id=construction_order.planet_id
            )
        except HexArcException as e:
            logger.error("Could not build improvement! " + str(e))

    if len(construction_orders) == 0:
        logger.info("No structures to build")
    logger.debug("Done building improvements across the empire")
