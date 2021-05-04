import logging
import collections
from pprint import pprint
from dataclasses import replace, dataclass
from scripts import utils
from typing import (
    Callable,
    DefaultDict,
    Dict,
    Generic,
    List,
    Optional,
    Set,
    Tuple,
    TypeVar,
)

from anacreonlib.types.response_datatypes import OwnedWorld, TradeRoute

from scripts.context import AnacreonContext, ProductionInfo

WorldFilter = Callable[[OwnedWorld], bool]


@dataclass(frozen=True)
class ResourceGraphEdge:
    source_world_id: int
    target_world_id: int
    resource_quantity: float


@dataclass(frozen=True)
class ResourceImporterGraphNode:
    world_id: int

    # How much of the resource we *need* to import
    required_import_qty: float

    #
    import_deficit: float = 0


@dataclass(frozen=True)
class ResourceExporterGraphNode:
    world_id: int

    # How much of the resource we need to get off of this planet
    exportable_qty: float

    # How much we would like to export from this world (based off of trade route)
    desired_export_qty: float


@dataclass(frozen=True)
class ResourceGraphNode:
    world_id: int
    exportable_qty: Optional[float]
    required_import_qty: Optional[float]

    def __post_init__(self) -> None:
        assert (self.exportable_qty is None) != (self.required_import_qty is None)


@dataclass(frozen=True, order=False)
class PlanetPair:
    src: int
    dst: int


async def balance_trade_routes(
    context: AnacreonContext, filter: WorldFilter = lambda w: True
) -> None:
    logger = logging.getLogger("trade route balancer")

    # Step 1: get all worlds
    # Step 2: for each world
    # Step 2a: find out what resource it exports and how much of it the world produces
    # Step 2b: find out what resources the world needs and how much of it the world wants to import

    our_worlds: Dict[int, OwnedWorld] = {
        world.id: world
        for world in context.state
        if isinstance(world, OwnedWorld) and filter(world)
    }

    assert len(our_worlds) > 0

    resource_ids: Set[int] = set()
    for world in our_worlds.values():
        desig_id = world.designation
        desig = context.scenario_info_objects[desig_id]
        if desig.exports is not None:
            x = desig.exports
            resource_ids.update(desig.exports)
    for resource_id in resource_ids:
        print(f"{resource_id}\t{context.scenario_info_objects[resource_id].name_desc}")

    await balance_routes_for_one_resource(context, our_worlds, 260)  # 260 is trillum


async def balance_routes_for_one_resource(
    context: AnacreonContext, our_worlds: Dict[int, OwnedWorld], resource_id: int
) -> None:
    def designation_exports_the_resource(desig_id: int) -> bool:
        return resource_id in (context.scenario_info_objects[desig_id].exports or [])

    empty_prod_info = ProductionInfo()

    production_info = [
        (
            world_id,
            context.generate_production_info(world).get(resource_id, empty_prod_info),
            designation_exports_the_resource(world.designation),
        )
        for world_id, world in our_worlds.items()
    ]

    # Map from world id to graph node
    graph_nodes: Dict[int, ResourceGraphNode] = dict()

    # Map from planet pair to trade route
    graph_edges: Dict[PlanetPair, ResourceGraphEdge] = dict()

    # Populate graph nodes
    for world_id, world_prod_info, produces_the_resource in production_info:
        if produces_the_resource:
            exportable_qty: Optional[float] = (
                world_prod_info.produced - world_prod_info.consumed_optimal
            )
            required_import_qty: Optional[float] = None
        else:
            exportable_qty = None
            required_import_qty = world_prod_info.consumed_optimal

        graph_nodes[world_id] = ResourceGraphNode(
            world_id, exportable_qty, required_import_qty
        )

    # Populate graph edges
    for world_id, world in our_worlds.items():
        if world.trade_route_partners:
            for trading_partner_id, trade_route in world.trade_route_partners.items():
                if trade_route.reciprocal:
                    # Data for this trade route is attached to the partner planet
                    trading_partner_trade_routes = our_worlds[
                        trading_partner_id
                    ].trade_route_partners
                    assert (
                        trading_partner_trade_routes is not None
                    ), "trading partner did not have any trade routes??"
                    actual_trade_route = trading_partner_trade_routes[world_id]

                    # we are importing what they are exporting, etc
                    imports, exports = (
                        actual_trade_route.exports,
                        actual_trade_route.imports,
                    )
                else:
                    imports, exports = trade_route.imports, trade_route.exports

                # Now we have the items that we are importing
                # Look for the one resource ID that matters
                if imports is not None:
                    for (
                        traded_res_id,
                        pct_of_demand,
                        optimal_import_qty,
                        actual_import_qty,
                    ) in utils.flat_list_to_n_tuples(4, imports):
                        assert traded_res_id is not None, "traded_res_id was None"
                        if int(traded_res_id) != resource_id:
                            continue

                        if actual_import_qty is not None:
                            amount_transferred = actual_import_qty
                        elif optimal_import_qty is not None:
                            amount_transferred = optimal_import_qty
                        else:
                            amount_transferred = 0

                        graph_edges[
                            PlanetPair(trading_partner_id, world_id)
                        ] = ResourceGraphEdge(
                            trading_partner_id, world_id, amount_transferred
                        )
                        break

    # Bare info without taking actual transmission into account

    # Exporter worlds _only_ contain worlds that produce the resource
    exporter_worlds = {
        world_id: node
        for world_id, node in graph_nodes.items()
        if node.exportable_qty is not None and node.required_import_qty is None
    }

    # Importer worlds _only_ contain worlds that do not produce the resource
    importer_worlds = {
        world_id: node
        for world_id, node in graph_nodes.items()
        if world_id not in exporter_worlds
    }

    print("### nodes ###")
    pprint(exporter_worlds)
    pprint(importer_worlds)

    print("\n\n### edges ###")
    pprint(graph_edges)

    # Exporter world info, but exportable_qty represents surplus capacity
    exporters_leftover = {**exporter_worlds}

    # Importer world info, but required_import_qty represents amount required
    importers_leftover = {**importer_worlds}

    # Populate importers/exporters leftover
    for planet_pair, graph_edge in graph_edges.items():
        # Should always work
        exporter = exporters_leftover[graph_edge.source_world_id]
        assert exporter.exportable_qty is not None
        exporters_leftover[graph_edge.source_world_id] = replace(
            exporter,
            exportable_qty=exporter.exportable_qty - graph_edge.resource_quantity,
        )

        # Could throw if the target_world_id is actually an exporter and the trade route is errorenous
        try:
            importer = importers_leftover[graph_edge.target_world_id]
        except KeyError:
            pass
        else:
            assert importer.required_import_qty is not None
            importers_leftover[graph_edge.target_world_id] = replace(
                importer,
                required_import_qty=importer.required_import_qty
                - graph_edge.resource_quantity,
            )

    export_surplus = sum(x.exportable_qty or 0 for x in exporters_leftover.values())
    import_deficit = sum(
        max(x.required_import_qty or 0, 0) for x in importers_leftover.values()
    )
    print("### exporters ###")
    pprint(exporters_leftover)

    print("### importers ###")
    pprint(importers_leftover)

    print(f"{export_surplus=}")
    print(f"{import_deficit=}")
    print(f"{(export_surplus - import_deficit)=}")
