import math
import asyncio
import logging
from pprint import pprint
from typing import Callable, Counter, List, Mapping, Optional, Protocol, Union, Set
from anacreonlib.anacreon import MilitaryForceInfo

from anacreonlib.types.request_datatypes import TransferFleetRequest, SellFleetRequest
from anacreonlib.types.response_datatypes import Fleet, OwnedWorld, Trait, World
from anacreonlib.types.scenario_info_datatypes import ScenarioInfoElement
from anacreonlib.types.type_hints import Location

from scripts import utils
from scripts.tasks import NameOrId
from scripts.tasks.fleet_manipulation_utils import OrderedPlanetId
from scripts.tasks.fleet_manipulation_utils_v2 import fleet_walk as fleet_walk_v2

from anacreonlib import Anacreon

from shared import param_types


class HasNameAndId(Protocol):
    name: str
    id: int


async def sell_stockpile_of_resource(
    context: Anacreon,
    transport_fleet_id: param_types.OurFleetId,
    resource_id: param_types.CommodityId,
    worlds_with_stockpile_ids: List[param_types.CommodityId],
    *,
    threshold: int = 10000,
):
    """

    :param context:
    :param transport_fleet_name_or_id:
    :param resource_name_or_unid:
    :param worlds_with_stockpile_name_or_id:
    :param threshold: The number
    :return:
    """

    jumpbeacon_trait_ids = [
        elt.id
        for elt in context.game_info.scenario_info
        if elt.is_jump_beacon and elt.id is not None
    ]

    our_jump_beacon_worlds = [
        world
        for world in context.space_objects.values()
        if isinstance(world, OwnedWorld)
        and any(
            (trait := world.squashed_trait_dict.get(jumpbeacon_trait_id, None))
            is not None
            and (not isinstance(trait, Trait) or trait.build_complete is None)
            for jumpbeacon_trait_id in jumpbeacon_trait_ids
        )
    ]

    def matches(obj: HasNameAndId, id_or_name_set: Set[NameOrId]) -> bool:
        """true if id/name set contains reference to the object"""
        try:
            return obj.name in id_or_name_set or obj.id in id_or_name_set
        except AttributeError:
            return False

    def name_or_id_set_to_id_list(
        name_id_set: Set[NameOrId],
    ) -> List[Union[Fleet, World]]:
        if all(isinstance(fleet, int) for fleet in name_id_set):
            return [context.space_objects[int(obj_id)] for obj_id in name_id_set]
        else:
            return [
                obj
                for obj in context.space_objects.values()
                if matches(obj, name_id_set)
            ]

    worlds_with_stockpile: List[World] = [
        context.space_objects[w_id] for w_id in worlds_with_stockpile_ids
    ]

    resource_elem: ScenarioInfoElement = context.scenario_info_objects[resource_id]
    assert resource_elem.id is not None

    assert resource_elem.is_cargo and resource_elem.mass

    logger_name = f"Sell resource (unid = {resource_elem.name_desc}) (fleet id = {transport_fleet_id})"
    logger = logging.getLogger(logger_name)

    mesophon_sov_id: int = next(
        el.id
        for el in context.game_info.sovereigns
        if el.name is not None and "mesophon" in el.name.lower()
    )

    # Queue of worlds that have a stockpile on them
    world_queue: "asyncio.Queue[OrderedPlanetId]" = asyncio.PriorityQueue()
    for world in worlds_with_stockpile:
        assert world.resources is not None
        amount_of_resource_on_world = dict(utils.flat_list_to_tuples(world.resources))[
            resource_elem.id
        ]
        world_queue.put_nowait(OrderedPlanetId(-amount_of_resource_on_world, world.id))

    # Queue of worlds to send the fleet to
    destination_queue: "asyncio.Queue[OrderedPlanetId]" = asyncio.Queue()
    destination_queue.put_nowait(OrderedPlanetId(0, world_queue.get_nowait().id))

    def find_nearest_mesophon(pos: Location) -> World:
        return min(
            (
                world
                for world in context.space_objects.values()
                if isinstance(world, World)
                and world.sovereign_id == int(mesophon_sov_id)
                and utils.trait_inherits_from_trait(
                    context.game_info.scenario_info, world.designation, 289
                )
                and any(
                    utils.dist(world.pos, jumpbeacon.pos) < 250
                    for jumpbeacon in our_jump_beacon_worlds
                )
            ),
            key=lambda w: utils.dist(pos, w.pos),
        )

    class Done(Exception):
        pass

    async def on_arrival_at_world(world_after_arrival: World) -> None:
        if world_after_arrival.sovereign_id == int(context._auth_info.sovereign_id):
            # remember to go to mesophon next
            destination_queue.put_nowait(
                OrderedPlanetId(0, find_nearest_mesophon(world_after_arrival.pos).id)
            )

            # load up on the resource
            remaining_cargo_space = (
                context.calculate_remaining_cargo_space(transport_fleet_id) * 0.98
            )
            max_transportable_qty = remaining_cargo_space / resource_elem.mass
            qty_on_world = world_after_arrival.resource_dict.get(resource_elem.id, 0)

            if qty_on_world < threshold:
                # When `attack_fleet_walk_v2` calls us, this exception will bubble up and
                raise Done()

            if qty_on_world > max_transportable_qty:
                qty_to_carry = max_transportable_qty
                destination_queue.put_nowait(OrderedPlanetId(0, world_after_arrival.id))
            else:
                qty_to_carry = qty_on_world
                world_queue.task_done()
                destination_queue.put_nowait(world_queue.get_nowait())

            logger.info(
                f"Putting {qty_to_carry:,} units of {resource_elem.name_desc} in fleet cargo hold"
            )
            await context.transfer_fleet(
                transport_fleet_id,
                world_after_arrival.id,
                [resource_elem.id, qty_to_carry],
            )

        elif world_after_arrival.sovereign_id == int(mesophon_sov_id):
            # sell the resource
            qty_in_fleet = dict(
                utils.flat_list_to_tuples(
                    context.space_objects[transport_fleet_id].resources
                )
            )[resource_elem.id]

            logger.info(f"Selling {qty_in_fleet:,} units of {resource_elem.name_desc}")

            await context.sell_fleet(
                fleet_id=transport_fleet_id,
                buyer_obj_id=world_after_arrival.id,
                resources=[resource_elem.id, qty_in_fleet],
            )

        else:
            logger.error(
                f"Why are we at this planet (name: {world_after_arrival.name}, id: {world_after_arrival.id})? It is neither ours or Mesophon"
            )

    try:
        await fleet_walk_v2(
            context,
            transport_fleet_id,
            on_arrival_at_world,
            input_queue=destination_queue,
            logger_name=logger_name,
        )
    except Done:
        pass


async def rally_ground_forces_to_planet(
    context: Anacreon,
    transport_fleet_id: param_types.OurFleetId,
    destination_planet_id: param_types.OurWorldId,
    *,
    threshold_pct: float = 0.1,
) -> None:
    logger = logging.getLogger("rally ground forces to planet")
    # sorted by resource quantity descending
    get_resource_qty: Callable[
        [OwnedWorld], float
    ] = lambda owned_world: context.calculate_forces(owned_world).ground_forces

    ground_force_resources: Mapping[int, ScenarioInfoElement] = {
        scn_id: context.scenario_info_objects[scn_id]
        for scn_id in context._force_calculator.gf_calc.keys()
    }

    foo = sorted(
        (
            world
            for world in context.space_objects.values()
            if isinstance(world, OwnedWorld)
            and world.id != destination_planet_id
            and context.calculate_forces(world).ground_forces > 0
        ),
        key=get_resource_qty,
    )

    our_worlds_with_resource = [world.id for world in foo]

    for world in foo:
        logger.info(f"{world.name!r}\t{get_resource_qty(world)}\t\t{world.id}")

    input()

    input_queue: "asyncio.Queue[OrderedPlanetId]" = asyncio.Queue()
    input_queue.put_nowait(OrderedPlanetId(0, our_worlds_with_resource.pop()))

    async def on_arrival_at_world(world: World) -> None:
        if world.id == destination_planet_id:
            # We are here to unload!
            to_transfer = {
                res_id: -qty
                for res_id, qty in utils.flat_list_to_tuples(
                    context.space_objects[transport_fleet_id].resources
                )
                if res_id in ground_force_resources.keys()
            }
            logging.info(f"Unloading objects: {to_transfer!r}")
            await context.transfer_fleet(transport_fleet_id, world.id, to_transfer)

            # Go to next planet
            input_queue.put_nowait(OrderedPlanetId(0, our_worlds_with_resource.pop()))

        else:
            # We are here to load up!
            fleet = context.space_objects[transport_fleet_id]
            assert isinstance(fleet, Fleet)

            fleet_total_cargo_space = sum(
                cargo_space * ship_qty
                for ship_id, ship_qty in utils.flat_list_to_tuples(fleet.resources)
                if (cargo_space := context.scenario_info_objects[ship_id].cargo_space)
                is not None
            )
            fleet_remaining_cargo_space = min(
                context.calculate_remaining_cargo_space(fleet),
                math.ceil(0.98 * fleet_total_cargo_space),
            )

            available_gf_resources = {
                res_id: res_qty
                for res_id, res_qty in world.resource_dict.items()
                if res_id in ground_force_resources
            }
            to_transfer = {scn_id: 0 for scn_id in ground_force_resources.keys()}

            # Figure out what the most we can take is
            for res_id, qty in available_gf_resources.items():
                res_mass = ground_force_resources[res_id].mass
                assert res_mass is not None

                qty_to_take = min(
                    math.floor(fleet_remaining_cargo_space / res_mass), math.floor(qty)
                )

                cargo_used = qty_to_take * res_mass
                fleet_remaining_cargo_space -= cargo_used
                assert fleet_remaining_cargo_space >= 0

                to_transfer[res_id] = qty_to_take
                available_gf_resources[res_id] -= qty_to_take

                if qty_to_take < qty:
                    break

            # Take it
            logging.info(
                f"Loading objects {to_transfer!r} from world id {world.id} (name: {world.name!r})"
            )
            await context.transfer_fleet(transport_fleet_id, world.id, to_transfer)
            remaining_space = context.calculate_remaining_cargo_space(
                transport_fleet_id
            )
            if remaining_space > 0.20 * fleet_total_cargo_space:
                logging.info("going to next planet")
                # We took everything from this planet and have space left over
                # so we go to the next planet
                input_queue.put_nowait(
                    OrderedPlanetId(0, our_worlds_with_resource.pop())
                )
            else:
                logging.info("going to unload")
                # We need to go unload, and come back and revisit this planet
                input_queue.put_nowait(OrderedPlanetId(0, destination_planet_id))
                our_worlds_with_resource.append(world.id)

    await fleet_walk_v2(
        context,
        transport_fleet_id,
        on_arrival_at_world,
        input_queue=input_queue,
        input_queue_is_live=False,  # we will make sure there is always one item in the queue
    )

    # architecture - intern for 3 years
    # long hours, male dominated, paid that much

    # total_qty = sum(map(get_resource_qty, our_worlds_with_resource))

    # if resource_qty is None:
    #     resource_qty = total_qty

    return None