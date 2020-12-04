import asyncio
import logging
from typing import List, Union, Set

from anacreonlib.types.request_datatypes import TransferFleetRequest, SellFleetRequest
from anacreonlib.types.response_datatypes import Fleet, World
from anacreonlib.types.scenario_info_datatypes import ScenarioInfoElement
from anacreonlib.types.type_hints import Location

from scripts import utils
from scripts.context import AnacreonContext
from scripts.tasks import NameOrId
from scripts.tasks.fleet_manipulation_utils import fleet_walk, OrderedPlanetId


async def sell_stockpile_of_resource(context: AnacreonContext, transport_fleet_name_or_id: NameOrId,
                                     resource_name_or_unid: NameOrId, worlds_with_stockpile_name_or_id: Set[NameOrId],
                                     *, threshold: int = 10000):
    """

    :param context:
    :param transport_fleet_name_or_id:
    :param resource_name_or_unid:
    :param worlds_with_stockpile_name_or_id:
    :param threshold: The number
    :return:
    """

    def matches(obj: Union[World, Fleet], id_or_name_set: Set[NameOrId]) -> bool:
        """true if id/name set contains reference to the object"""
        try:
            return obj.name in id_or_name_set or obj.id in id_or_name_set
        except AttributeError:
            return False

    def name_or_id_set_to_id_list(name_id_set: Set[NameOrId]) -> List[Union[Fleet, World]]:
        if all(isinstance(fleet, int) for fleet in name_id_set):
            return [context.state_dict[obj_id] for obj_id in name_id_set]
        else:
            return [obj for obj in context.state if matches(obj, name_id_set)]

    transport_fleet_id: int = next(fleet.id for fleet in context.state
                                   if isinstance(fleet, Fleet)
                                   and transport_fleet_name_or_id in {fleet.name, fleet.id})

    worlds_with_stockpile: List[World] = name_or_id_set_to_id_list(worlds_with_stockpile_name_or_id)

    resource_elem: ScenarioInfoElement = next(el for el in context.game_info.scenario_info
                                              if resource_name_or_unid in {el.unid, el.id})

    assert resource_elem.is_cargo and resource_elem.mass

    logger_name = f"Sell resource (unid = {resource_elem.name_desc}) (fleet id = {transport_fleet_id})"
    logger = logging.getLogger(logger_name)

    mesophon_sov_id: int = next(el.id for el in context.game_info.sovereigns
                                if el.name is not None
                                and "mesophon" in el.name.lower())

    destination_queue: asyncio.Queue = asyncio.Queue()
    destination_queue.put_nowait(OrderedPlanetId(None, worlds_with_stockpile[0].id))

    def find_nearest_mesophon(pos: Location) -> World:
        return min((world for world in context.state
                    if isinstance(world, World)
                    and world.sovereign_id == int(mesophon_sov_id)
                    and utils.trait_inherits_from_trait(context.game_info.scenario_info, world.designation, 289)),
                   key=lambda w: utils.dist(pos, w.pos))

    walk = fleet_walk(context, transport_fleet_id, input_queue=destination_queue,
                      logger_name=logger_name)

    async for world_after_arrival in walk:
        if world_after_arrival.sovereign_id == int(context.base_request.sovereign_id):
            # remember to go to mesophon next
            destination_queue.put_nowait(OrderedPlanetId(None, find_nearest_mesophon(world_after_arrival.pos).id))

            # load up on the resource
            remaining_cargo_space = context.calculate_remaining_cargo_space(transport_fleet_id) * .98
            max_transportable_qty = remaining_cargo_space / resource_elem.mass
            qty_on_world = dict(utils.flat_list_to_tuples(world_after_arrival.resources))[resource_elem.id]

            if qty_on_world < threshold:
                break

            if qty_on_world > max_transportable_qty:
                qty_to_carry = max_transportable_qty
                destination_queue.put_nowait(OrderedPlanetId(None, world_after_arrival.id))
            else:
                qty_to_carry = qty_on_world

            logger.info(f"Putting {qty_to_carry:,} units of {resource_elem.name_desc} in fleet cargo hold")
            partial_state = await context.client.transfer_fleet(TransferFleetRequest(fleet_obj_id=transport_fleet_id,
                                                                                     dest_obj_id=world_after_arrival.id,
                                                                                     resources=[resource_elem.id,
                                                                                                qty_to_carry],
                                                                                     **context.auth))
            context.register_response(partial_state)

        elif world_after_arrival.sovereign_id == int(mesophon_sov_id):
            # sell the resource
            qty_in_fleet = dict(utils.flat_list_to_tuples(context.get_obj_by_id(transport_fleet_id).resources))[
                resource_elem.id]

            logger.info(f"Selling {qty_in_fleet:,} units of {resource_elem.name_desc}")

            partial_state = await context.client.sell_fleet(SellFleetRequest(
                fleet_id=transport_fleet_id,
                buyer_obj_id=world_after_arrival.id,
                resources=[resource_elem.id, qty_in_fleet],
                **context.auth
            ))
            context.register_response(partial_state)


        else:
            logger.error(
                f"Why are we at this planet (name: {world_after_arrival.name}, id: {world_after_arrival.id})? It is neither ours or Mesophon")

        await walk.asend(None)
