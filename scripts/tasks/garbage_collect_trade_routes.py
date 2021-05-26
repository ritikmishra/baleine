from dataclasses import astuple
import logging
from pprint import pprint

from anacreonlib.types.request_datatypes import StopTradeRouteRequest
from scripts.tasks.balance_trade_routes import PlanetPair
from typing import List, Set, Union
from scripts import utils
from anacreonlib.types.response_datatypes import OwnedWorld, TradeRoute
from scripts.context import AnacreonContext


async def garbage_collect_trade_routes(context: AnacreonContext) -> None:
    logger = logging.getLogger("garbage_trade_routes")

    our_worlds = {
        world.id: world for world in context.state if isinstance(world, OwnedWorld)
    }

    garbage_trade_routes: Set[PlanetPair] = set()
    for world_id, world in our_worlds.items():
        if (planet_trade_routes := world.trade_route_partners) is not None:
            for partner_id, trade_route in planet_trade_routes.items():
                if trade_route.reciprocal:
                    # FIXME: will crash on mesophon trade route
                    partners_of_partner = our_worlds[partner_id].trade_route_partners
                    assert partners_of_partner is not None
                    trade_route = partners_of_partner[world_id]

                if (
                    is_trade_route_garbage(trade_route)
                    and (pair := PlanetPair(world_id, partner_id))
                    not in garbage_trade_routes
                    and PlanetPair(partner_id, world_id) not in garbage_trade_routes
                ):
                    garbage_trade_routes.add(pair)

    for i, pair in enumerate(garbage_trade_routes):
        req = StopTradeRouteRequest(
            planet_id_a=pair.src, planet_id_b=pair.dst, **context.auth
        )

        logger.info(
            f"({i + 1} of {len(garbage_trade_routes)}) Cancelling trade route for planet pair {pair}"
        )

        partial_state = await context.client.stop_trade_route(req)
        context.register_response(partial_state)


def is_trade_route_garbage(route: TradeRoute) -> bool:
    def unidirectional_garbage(
        resource_transfer_info: List[Union[float, None]]
    ) -> bool:
        for (
            traded_res_id,
            pct_of_demand,
            optimal_transfer_qty,
            actual_transfer_qty,
        ) in utils.flat_list_to_n_tuples(4, resource_transfer_info):
            if pct_of_demand or optimal_transfer_qty:
                return False

        return True

    if route.reciprocal:
        raise LookupError(
            "passed in a reciprocal trade route, could not find imports/exports"
        )

    garbage_imports = route.imports is None or unidirectional_garbage(route.imports)
    garbage_exports = route.exports is None or unidirectional_garbage(route.exports)
    return (
        garbage_imports
        and garbage_exports
        and route.export_tech is None
        and route.import_tech is None
    )
