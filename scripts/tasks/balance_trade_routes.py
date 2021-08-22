import asyncio
import functools
import logging
from dataclasses import replace, dataclass

from anacreonlib.anacreon import Anacreon, ProductionInfo
from scripts import utils
import anacreonlib.exceptions
from anacreonlib.types.type_hints import Location
from typing import (
    Callable,
    Dict,
    List,
    Optional,
    Set,
    Tuple,
    Union,
)

from anacreonlib.types.request_datatypes import TradeRouteTypes
from anacreonlib.types.response_datatypes import OwnedWorld


WorldFilter = Callable[[OwnedWorld], bool]


# There is logic that depends on these dataclasses being frozen
# specifically, that shallow dict copies are sufficient to avoid having any
# changes to parameters accidentally leak outside of the function


@dataclass(frozen=True)
class ResourceGraphEdge:
    source_world_id: int
    target_world_id: int

    # Qty that was actually transferred, can be less than desired if there is a shortage
    resource_quantity: float


@dataclass(frozen=True)
class ResourceImporterGraphNode:
    world_id: int

    # How much of the resource we *need* to import
    # (i.e how much we are consuming)
    required_import_qty: float

    # How much of the resource we would like to consume/be importing
    # (i.e imported_optimal)
    actual_import_qty: float

    # How much of the resource we consumed (takes stockpiles into account)
    # (i.e anything we consumed but didn't import or produce locally comes from a stockpile)
    stockpile_consumed_qty: float


@dataclass(frozen=True)
class ResourceExporterGraphNode:
    world_id: int

    # How much of the resource we need to get off of this planet
    # (i.e how much is being produced, minus any local consumption)
    exportable_qty: float

    # How much we would like to export from this world (based off of trade route).
    # con be more than actual exports
    desired_export_qty: float

@dataclass(frozen=True, order=False)
class PlanetPair:
    src: int
    dst: int


async def balance_trade_routes(
    context: Anacreon,
    # filter: WorldFilter = lambda w: True,
    # dry_run: bool = False,
) -> None:
    logger = logging.getLogger("trade route balancer")

    # Step 1: get all worlds
    # Step 2: for each world
    # Step 2a: find out what resource it exports and how much of it the world produces
    # Step 2b: find out what resources the world needs and how much of it the world wants to import

    our_worlds: Dict[int, OwnedWorld] = {
        world.id: world
        for world in context.space_objects.values()
        if isinstance(world, OwnedWorld)
    }

    assert len(our_worlds) > 0

    resource_ids: Set[int] = set()
    for world in our_worlds.values():
        desig_id = world.designation
        desig = context.scenario_info_objects[desig_id]
        if desig.exports is not None:
            x = desig.exports
            resource_ids.update(desig.exports)

    logger.info("resource_id\tresource name")
    for resource_id in resource_ids:
        logger.info(
            f"{resource_id}\t{context.scenario_info_objects[resource_id].name_desc}"
        )

    # 260 is trillum
    # await balance_routes_for_one_resource(context, our_worlds, 260, dry_run)

    for resource_id in resource_ids:
        await balance_routes_for_one_resource(
            context, our_worlds, resource_id, dry_run=False
        )

@dataclass
class TradeRouteInfo:
    importer_id: int
    exporter_id: int
    alloc_type: str
    alloc_value: float
    res_id: int

async def balance_routes_for_one_resource(
    context: Anacreon,
    our_worlds: Dict[int, OwnedWorld],
    resource_id: int,
    dry_run: bool = False,
) -> None:
    logger = logging.getLogger("balance_routes_for_one_resource")

    position_dict: Dict[int, Location] = {
        world_id: world.pos for world_id, world in our_worlds.items()
    }

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
    graph_nodes: Dict[
        int, Union[ResourceImporterGraphNode, ResourceExporterGraphNode]
    ] = dict()

    # Map from planet pair to trade route
    graph_edges: Dict[PlanetPair, ResourceGraphEdge] = dict()

    # Populate graph nodes
    for world_id, world_prod_info, produces_the_resource in production_info:
        if produces_the_resource:
            exportable_qty = world_prod_info.produced - world_prod_info.consumed_optimal
            graph_nodes[world_id] = ResourceExporterGraphNode(
                world_id=world_id,
                exportable_qty=exportable_qty,
                desired_export_qty=world_prod_info.exported_optimal,
            )
        else:
            # Use consumed_optimal over imported_optimal in case the trade routes are broken or non-existent
            graph_nodes[world_id] = ResourceImporterGraphNode(
                world_id=world_id,
                required_import_qty=world_prod_info.consumed_optimal,
                actual_import_qty=world_prod_info.imported_optimal,
                stockpile_consumed_qty=world_prod_info.consumed
                - world_prod_info.produced,
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

                        if pct_of_demand:
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
        if isinstance(node, ResourceExporterGraphNode)
    }

    # Importer worlds _only_ contain worlds that do not produce the resource
    importer_worlds = {
        world_id: node
        for world_id, node in graph_nodes.items()
        if isinstance(node, ResourceImporterGraphNode)
    }

    total_produced = sum(x.exportable_qty for x in exporter_worlds.values())
    total_desired_imports = sum(x.required_import_qty for x in importer_worlds.values())

    # new_edges = bootstrap_graph_edges(importer_worlds, exporter_worlds, position_dict)
    new_edges = adjust_graph_edges(
        importer_worlds, exporter_worlds, position_dict, graph_edges
    )

    requests: List[TradeRouteInfo] = compile_graph_edge_changes(
        context, resource_id, importer_worlds, graph_edges, new_edges
    )

    logger.info(
        f"{len(requests)} trade route requests desired to alter the {context.scenario_info_objects[resource_id].name_desc} ({resource_id=}) economy"
    )
    logger.info(
        f"\tFor resource {resource_id}, we are making {(total_produced - total_desired_imports)=} surplus per watch\n"
    )

    for i, req in enumerate(requests):
        # logger.info(f"req {i + 1} of {len(requests)}")
        # pprint(req)
        if not dry_run:
            try:
                await asyncio.sleep(1)
                await context.set_trade_route(
                    importer_id=req.importer_id,
                    exporter_id=req.exporter_id,
                    alloc_type=req.alloc_type,
                    alloc_value=req.alloc_value,
                    res_type_id=req.res_id
                )
            except asyncio.exceptions.TimeoutError:
                requests.append(req)
            except anacreonlib.exceptions.HexArcException:
                pass


def bootstrap_graph_edges(
    importers: Dict[int, ResourceImporterGraphNode],
    exporters: Dict[int, ResourceExporterGraphNode],
    position_dict: Dict[int, Location],
) -> Dict[PlanetPair, ResourceGraphEdge]:
    """Given resource importers and resource exporters, figure out what the trade routes should look like

    This does not consider any information about the current state of trade routes, so it can end up creating a lot of orders

    Args:
        importers (Dict[int, ResourceImporterGraphNode]): map from world id to resource importer node
        exporters (Dict[int, ResourceExporterGraphNode]): map from world id to resource exporter node
        position_dict (Dict[int, Location]): map from world id to location

    Returns:
        Dict[PlanetPair, ResourceGraphEdge]: map from planet pair to desired trade route
    """

    # Create shallow copy (deep copy not needed bc graph nodes are immutable)
    importers = {
        importer_id: replace(importer, actual_import_qty=0)
        for importer_id, importer in importers.items()
    }
    exporters = {
        exporter_id: replace(exporter, desired_export_qty=0)
        for exporter_id, exporter in exporters.items()
    }

    edges: Dict[PlanetPair, ResourceGraphEdge] = dict()

    def importer_key(foo: Tuple[int, ResourceImporterGraphNode]) -> float:
        """Sort importers by required import qty"""
        return foo[1].required_import_qty

    def exporter_key(foo: Tuple[int, ResourceExporterGraphNode]) -> float:
        """Sort exporters by exportable_qty"""
        return foo[1].exportable_qty

    for exporter_id, exporter_data in sorted(
        exporters.items(), key=exporter_key, reverse=True
    ):
        # dict of all importers within range
        nearby_importers = {
            importer_id: importer
            for importer_id, importer in importers.items()
            if utils.dist(position_dict[importer_id], position_dict[exporter_id]) < 200
        }

        # Sort importers by max demand
        nearby_importers_sorted = sorted(
            nearby_importers.items(),
            key=importer_key,
            reverse=True,
        )

        for importer_id, importer_data in nearby_importers_sorted:
            # Check that the exporter has enough capacity and that the importer still needs resources
            if exporter_data.exportable_qty < 0.10 * importer_data.required_import_qty:
                break
            elif importer_data.required_import_qty <= importer_data.actual_import_qty:
                continue

            amount_to_import = min(
                importer_data.required_import_qty, exporter_data.exportable_qty
            )

            # Update exporter data
            exporter_data = replace(
                exporter_data,
                exportable_qty=exporter_data.exportable_qty - amount_to_import,
            )

            # Update + save importer data
            importer_data = replace(
                importer_data,
                actual_import_qty=importer_data.actual_import_qty + amount_to_import,
            )
            importers[importer_id] = importer_data

            # Schedule trade route for creation
            edges[PlanetPair(exporter_id, importer_id)] = ResourceGraphEdge(
                exporter_id, importer_id, amount_to_import
            )

        # Write updated exporter data back to dict
        exporters[exporter_id] = exporter_data

    return edges


def adjust_graph_edges(
    importers: Dict[int, ResourceImporterGraphNode],
    exporters: Dict[int, ResourceExporterGraphNode],
    position_dict: Dict[int, Location],
    existing_edges: Dict[PlanetPair, ResourceGraphEdge],
) -> Dict[PlanetPair, ResourceGraphEdge]:
    """Tries to only minorly adjust the graph edges to account for planets that can't satisfy their imports"""

    logger = logging.getLogger("adjust_graph_edges")

    importers = importers.copy()
    exporters = exporters.copy()

    ret = existing_edges.copy()

    # exporters that are currently trying to export more than they produce
    # we will try to resolve this
    overworked_exporters = sorted(
        (
            (deficit, exporter_id, exporter)
            for exporter_id, exporter in exporters.items()
            if (deficit := (exporter.desired_export_qty - exporter.exportable_qty)) > 0
        ),
        reverse=True,
    )

    overworked_exporter_ids = list(map(lambda x: x[1], overworked_exporters))

    def most_surplusiest_nearby_exporter(
        importer_id: int,
    ) -> Optional[Tuple[float, int]]:
        """returns tuple of the surplus and the id (in that order for sorting purposes)"""

        # list of tuple of (surplus, id, location)
        exporter_positions: List[Tuple[float, int, Location]] = sorted(
            (
                (
                    exporter.exportable_qty - exporter.desired_export_qty,
                    world_id,
                    position_dict[world_id],
                )
                for world_id, exporter in exporters.items()
            ),
            reverse=True,
        )

        exporter_surpluses = [
            (surplus, world_id)
            for surplus, world_id, location in exporter_positions
            if surplus > 0 and utils.dist(location, position_dict[importer_id]) < 200
        ]

        if len(exporter_surpluses) == 0:
            return None

        return exporter_surpluses[0]

    for deficit, exporter_id, exporter in overworked_exporters:
        importers_to_this_exporter = {
            pair.dst: importers[pair.dst]
            for pair in ret.keys()
            if pair.src == exporter_id
        }

        # while there is a deficit
        while deficit > 0:
            # sort importers by the max surplus of nearby other exporters
            # pick the top one and switch it over

            try:
                (
                    surplusiest_exporter_surplus,
                    surplusiest_exporter_id,
                    importer_id,
                ) = max(
                    (x[0], x[1], importer_id)
                    for importer_id in importers_to_this_exporter.keys()
                    if (x := most_surplusiest_nearby_exporter(importer_id)) is not None
                )
            except ValueError:
                break  # break out of while loop
            else:
                assert surplusiest_exporter_surplus > 0
                assert exporter_id in overworked_exporter_ids
                assert surplusiest_exporter_id != exporter_id
                # assert surplusiest_exporter_id not in overworked_exporter_ids

                # how much is this guy importing from us right now??
                importer = importers[importer_id]
                pair = PlanetPair(exporter_id, importer_id)
                amount_to_possibly_get_back = ret[pair].resource_quantity

                # we can afford to switch over this importer to the other exporter
                if (
                    amount_to_possibly_get_back > 0
                    and amount_to_possibly_get_back < surplusiest_exporter_surplus
                ):
                    # then switch it over!

                    # 1. cancel existing trade routes from importer to other planets
                    del ret[pair]

                    # 2. schedule new trade route
                    pair_with_swapped = PlanetPair(surplusiest_exporter_id, importer_id)
                    if pair_with_swapped in ret:
                        ret[pair_with_swapped] = replace(
                            (current_edge := ret[pair_with_swapped]),
                            resource_quantity=current_edge.resource_quantity
                            + amount_to_possibly_get_back,
                        )
                    else:
                        ret[pair_with_swapped] = ResourceGraphEdge(
                            surplusiest_exporter_id,
                            importer_id,
                            amount_to_possibly_get_back,
                        )

                    # 3. we got back some!
                    deficit -= amount_to_possibly_get_back
                    exporter = replace(
                        exporter,
                        desired_export_qty=exporter.desired_export_qty
                        - amount_to_possibly_get_back,
                    )

                    # 4. the other person lost some
                    exporters[surplusiest_exporter_id] = replace(
                        (surplusiest_exporter := exporters[surplusiest_exporter_id]),
                        desired_export_qty=surplusiest_exporter.desired_export_qty
                        + amount_to_possibly_get_back,
                    )

                    logger.info(
                        f"({exporter_id}) - swapping {importer_id=} to import {amount_to_possibly_get_back} from {surplusiest_exporter_id=}"
                    )

                del importers_to_this_exporter[importer_id]

        # write back exporter changes
        exporters[exporter_id] = exporter

    del overworked_exporters

    # now we check on planets that are not connected to any exporter
    unconnected_importers = sorted(
        (
            importer
            for importer_id, importer in importers.items()
            if (importer.stockpile_consumed_qty + importer.actual_import_qty)
            < importer.required_import_qty
            and importer.required_import_qty != 0
            # and all(importer_id != pair.dst for pair in ret.keys())
        ),
        key=lambda imp: imp.required_import_qty,
    )

    for importer in unconnected_importers:
        importer_id = importer.world_id
        maybe_nearby_exporter = most_surplusiest_nearby_exporter(importer_id)

        if maybe_nearby_exporter is None:
            continue

        (
            surplusiest_exporter_surplus,
            surplusiest_exporter_id,
        ) = maybe_nearby_exporter

        amount_to_import = importer.required_import_qty - importer.actual_import_qty

        if surplusiest_exporter_surplus > amount_to_import > 0:
            logger.info(
                f"connecting previously unconnected {importer_id=} to import {amount_to_import} from exporter id {surplusiest_exporter_id}"
            )
            ret[PlanetPair(surplusiest_exporter_id, importer_id)] = ResourceGraphEdge(
                surplusiest_exporter_id, importer_id, amount_to_import
            )

            exporters[surplusiest_exporter_id] = replace(
                (old_exporter_val := exporters[surplusiest_exporter_id]),
                desired_export_qty=old_exporter_val.desired_export_qty
                + amount_to_import,
            )
            importers[importer_id] = replace(
                importer,
                actual_import_qty=importer.actual_import_qty + amount_to_import,
            )

    return ret


def compile_graph_edge_changes(
    context: Anacreon,
    resource_id: int,
    importers: Dict[int, ResourceImporterGraphNode],
    old_graph_edges: Dict[PlanetPair, ResourceGraphEdge],
    new_graph_edges: Dict[PlanetPair, ResourceGraphEdge],
) -> List[TradeRouteInfo]:
    """Turns changes in the import graph for one resource into request bodies that can then be sent to the Anacreon API

    Args:
        context (Anacreon): context (used only for context.auth)
        resource_id (int): Resource ID for which the graph edges represent trading
        importers (Dict[int, ResourceImporterGraphNode]): A dict of all importer planets on the trade graph
        old_graph_edges (Dict[PlanetPair, ResourceGraphEdge]): The trage graph that currently exists
        new_graph_edges (Dict[PlanetPair, ResourceGraphEdge]): The trade graph that we would like to switch to

    Returns:
        List[TradeRouteInfo]: All the trade route changes that need to be made in order to apply the new edges
    """
    logger = logging.getLogger("apply_graph_edge_changes")

    create_trade_route_request = functools.partial(
        TradeRouteInfo,
        alloc_type=TradeRouteTypes.CONSUMPTION,
        res_type_id=resource_id,
    )

    edges_to_delete: Set[PlanetPair] = set()
    edges_to_add_or_modify: Dict[PlanetPair, ResourceGraphEdge] = dict()

    for pair, edge in old_graph_edges.items():
        if pair not in new_graph_edges.keys() and edge.resource_quantity != 0:
            edges_to_delete.add(pair)

    for pair, edge in new_graph_edges.items():
        edge_is_new = pair not in old_graph_edges and edge.resource_quantity > 0
        edge_modifies_old_edge = (
            pair in old_graph_edges and old_graph_edges[pair] != edge
        )
        if edge_is_new or edge_modifies_old_edge:
            edges_to_add_or_modify[pair] = edge

    requests: List[TradeRouteInfo] = []
    for edge_to_delete in edges_to_delete:
        requests.append(
            create_trade_route_request(
                importer_id=edge_to_delete.dst,
                exporter_id=edge_to_delete.src,
                alloc_value="0.0",
            )
        )

    for pair, edge_to_add in edges_to_add_or_modify.items():
        importer = importers[pair.dst]

        raw_percent = (
            edge_to_add.resource_quantity / importer.required_import_qty
        ) * 100

        # some value like "40.0"
        percent = str(
            round(
                raw_percent + 0.1 if raw_percent != 0 else 0,
                1,
            )
        )
        requests.append(
            create_trade_route_request(
                exporter_id=edge_to_add.source_world_id,
                importer_id=edge_to_add.target_world_id,
                alloc_value=percent,
            )
        )

    return requests
