import collections
import unittest
from pprint import pprint
from typing import Dict, TypeVar

from anacreonlib.types.type_hints import Location
from scripts.tasks import balance_trade_routes as btr

T = TypeVar("T", btr.ResourceImporterGraphNode, btr.ResourceExporterGraphNode)


def nodes(*args: T) -> Dict[int, T]:
    ret = {node.world_id: node for node in args}

    if len(ret) != len(args):
        raise ValueError(
            "Multiple graph nodes were attempting to use the same world id!"
        )

    return ret


def edges(*args: btr.ResourceGraphEdge) -> Dict[btr.PlanetPair, btr.ResourceGraphEdge]:
    ret = {
        btr.PlanetPair(edge.source_world_id, edge.target_world_id): edge
        for edge in args
    }

    if len(ret) != len(args):
        raise ValueError("There are duplicate edges in the mix!")

    return ret


# given: All worlds are in the same spot so that they can trade with each other
position_dict: Dict[int, Location] = collections.defaultdict(lambda: (0, 0))


class TestTradeWebBalancer(unittest.TestCase):
    def test_basic_connect_unconnected_worlds(self) -> None:
        """It sholud connect unconnected importers to exporters with capacity"""

        # given: two importers that are not importing any resources
        importers = nodes(
            btr.ResourceImporterGraphNode(0, 100, 0, 0),
            btr.ResourceImporterGraphNode(1, 100, 0, 50),
        )

        # and: an exporter that can supply both of them with enough + an exporter that is already busy
        exporters = nodes(btr.ResourceExporterGraphNode(2, 1000, 0), btr.ResourceExporterGraphNode(3, 2000, 2000))

        # and: there are no trade routes currently
        existing_edges: Dict[btr.PlanetPair, btr.ResourceGraphEdge] = dict()

        # when: i balance the trade routes
        adjusted_edges = btr.adjust_graph_edges(
            importers, exporters, position_dict, existing_edges
        )

        # then: both planets are importing enough resources
        self.assertGreaterEqual(
            adjusted_edges[btr.PlanetPair(2, 0)].resource_quantity, 100
        )
        self.assertGreaterEqual(
            adjusted_edges[btr.PlanetPair(2, 1)].resource_quantity, 100
        )

    def test_basic_connect_worlds_eating_stockpile(self) -> None:
        """It should connect worlds that are living of their stockpile to exporters"""
        # given: All worlds are in the same spot so that they can trade with each other
        position_dict: Dict[int, Location] = collections.defaultdict(lambda: (0, 0))

        # given: one importer that is eating completely off the stockpile, and one that is not
        importers = nodes(
            btr.ResourceImporterGraphNode(0, 100, 0, 40),
            btr.ResourceImporterGraphNode(1, 100, 0, 105),
        )

        # and: an exporter that can supply both of them with enough
        exporters = nodes(btr.ResourceExporterGraphNode(2, 1000, 0))

        # and: there are no trade routes currently
        existing_edges: Dict[btr.PlanetPair, btr.ResourceGraphEdge] = dict()

        # when: i balance the trade routes
        adjusted_edges = btr.adjust_graph_edges(
            importers, exporters, position_dict, existing_edges
        )

        # then: the planet that is not eating off the stockpile is importing
        self.assertGreaterEqual(
            adjusted_edges[btr.PlanetPair(2, 0)].resource_quantity, 100
        )

        # and: the planet that is eating off the stockpile is not importing (will continue to eat from stockpile)
        self.assertTrue(btr.PlanetPair(2, 1) not in adjusted_edges.keys())

    def test_basic_split_imports(self) -> None:
        """It should be willing to connect an unconnected world to several exporters to meet demand"""

        # given: one importer that needs a lot of resources
        importers = nodes(
            btr.ResourceImporterGraphNode(0, 10_000, 0, 0),
        )

        # and: multiple exporters that can fulfill this demand together, but not individually
        exporters = nodes(
            btr.ResourceExporterGraphNode(1, 5_000, 0),
            btr.ResourceExporterGraphNode(2, 5_000, 0),
        )

        # and: there are no trade routes currently
        existing_edges = edges()

        # when: i balance the trade routes
        adjusted_edges = btr.adjust_graph_edges(
            importers, exporters, position_dict, existing_edges
        )

        print()
        pprint(adjusted_edges)

        # then: all 3 exporters should have a trade route to the importer
        self.assertEqual(len(adjusted_edges), 3)

    def test_split_imports_for_overworked_exporters(self) -> None:
        """It should be willing to split imports to an importer that currently has only a single import route"""
        # TODO: fuller integration test for this part

        # given: one importer that needs a lot of resources, but is not able to import 100%
        importers = nodes(
            btr.ResourceImporterGraphNode(0, 10_000, 10_000, 0),
        )

        # and: multiple exporters that can fulfill this demand together, but not individually
        #      (one is close)
        exporters = nodes(
            btr.ResourceExporterGraphNode(1, 9_000, 10_000),
            btr.ResourceExporterGraphNode(2, 2_000, 0),
        )

        # and: the importer has one trade route, which is not sufficient
        existing_edges = edges(btr.ResourceGraphEdge(1, 0, 9_000))

        # when: i balance the trade routes
        adjusted_edges = btr.adjust_graph_edges(
            importers, exporters, position_dict, existing_edges
        )

        # then: all both exporters should have a trade route to the importer
        self.assertEqual(len(adjusted_edges), 2)
        self.assertEqual(adjusted_edges[btr.PlanetPair(2, 0)].resource_quantity, 1_000)
        self.assertEqual(adjusted_edges[btr.PlanetPair(1, 0)].resource_quantity, 9_000)

    def test_consolidating_imports_for_overworked_exporters(self) -> None:
        """"""
        # given: an importer that is not able to import 100%
        importers = nodes(btr.ResourceImporterGraphNode(0, 1_000, 1_000, 0))

        # and:
        exporters = nodes(
            btr.ResourceExporterGraphNode(
                2, 600, 1_000
            ),  # attempting to export the full 1000 to importer, not working
            btr.ResourceExporterGraphNode(3, 1_000, 0),
        )

        existing_edges = edges(btr.ResourceGraphEdge(2, 0, 900))

        adjusted_edges = btr.adjust_graph_edges(
            importers, exporters, position_dict, existing_edges
        )

        self.assertEqual(len(adjusted_edges), 1)
        self.assertEqual(adjusted_edges[btr.PlanetPair(3, 0)].resource_quantity, 1_000)

    def test_balancing_triple_split(self) -> None:
        """It should be able to balance imports for importers who are split between three or more exporters"""

        importers = nodes(
            btr.ResourceImporterGraphNode(0, 1000, 1000, 63)
        )

        exporters = nodes(
            btr.ResourceExporterGraphNode(1, 300, 400),
            btr.ResourceExporterGraphNode(2, 550, 400),
            btr.ResourceExporterGraphNode(3, 150, 200),
        )

        existing_edges = edges(
            btr.ResourceGraphEdge(1, 0, 300),
            btr.ResourceGraphEdge(2, 0, 400),
            btr.ResourceGraphEdge(3, 0, 150),            
        )

        actual_edges = btr.adjust_graph_edges(importers, exporters, position_dict, existing_edges)

        print(); pprint(actual_edges)
        self.assertEqual(len(actual_edges), 3)
        self.assertEqual(actual_edges[btr.PlanetPair(1, 0)].resource_quantity, 300)
        self.assertEqual(actual_edges[btr.PlanetPair(3, 0)].resource_quantity, 150)
        self.assertEqual(actual_edges[btr.PlanetPair(2, 0)].resource_quantity, 550)


if __name__ == "__main__":
    unittest.main()
