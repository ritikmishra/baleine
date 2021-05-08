from dataclasses import dataclass
import logging
from typing import List

from anacreonlib.exceptions import HexArcException
from anacreonlib.types.request_datatypes import AlterImprovementRequest
from anacreonlib.types.response_datatypes import World

from scripts.context import AnacreonContext


@dataclass(frozen=True)
class ConstructionOrder:
    planet_id: int
    planet_name: str
    improvement_id: int
    improvement_name: str


async def build_habitats_spaceports(context: AnacreonContext) -> None:
    """
    Builds habitat structures and spaceports on all planets on which they can be built
    :param context: AnacreonContext
    :return: None
    """
    logger = logging.getLogger("build habitats and spaceports")

    construction_orders: List[ConstructionOrder] = []

    logger.debug("Beginning to iterate through planets")
    for planet in context.state:
        if isinstance(planet, World):
            if int(planet.sovereign_id) == int(context.base_request.sovereign_id):
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
            partial_state = await context.client.build_improvement(
                AlterImprovementRequest(
                    source_obj_id=construction_order.planet_id,
                    improvement_id=construction_order.improvement_id,
                    **context.auth,
                )
            )
            context.register_response(partial_state)
        except HexArcException as e:
            logger.error("Could not build improvement! " + str(e))

    if len(construction_orders) == 0:
        logger.info("No structures to build")
    logger.debug("Done building improvements across the empire")
