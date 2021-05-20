import math
from typing import List, Dict, Optional, Sequence, TypeVar, Union, Tuple, cast, overload

from anacreonlib.types.response_datatypes import World, Trait
from anacreonlib.types.scenario_info_datatypes import ScenarioInfoElement
from anacreonlib.types.type_hints import Location

T = TypeVar("T")
U = TypeVar("U")


def flat_list_to_tuples(lst: Sequence[T]) -> List[Tuple[T, T]]:
    """
    Convert a list of the form `[1, 2, 3, 4, ...]` into the list of tuples `[(1, 2), (3, 4)]`
    """
    return list(zip(lst[::2], lst[1::2]))


def flat_list_to_n_tuples(n: int, lst: List[T]) -> List[Tuple[T, ...]]:
    zip_args = [lst[i::n] for i in range(n)]
    return list(zip(*zip_args))


def dict_to_flat_list(dict: Dict[T, U]) -> List[Union[T, U]]:
    """
    Convert a dict to a flat list alternating between keys and values
    :param dict: A dict e.g `{1: 2, 3: 4, 5: 6}`
    :return: A list alternating between keys and values (e.g `[1, 2, 3, 4, 5, 6]`)
    """
    ret: List[Union[T, U]] = []
    for k, v in dict.items():
        ret.append(k)
        ret.append(v)
    return ret


def _diff_squared(a: float, b: float) -> float:
    return (a - b) * (a - b)


def dist(pointA: Location, pointB: Location) -> float:
    dist2 = sum(map(_diff_squared, pointA, pointB))
    return math.sqrt(dist2)


def world_has_trait(
    scninfo: List[ScenarioInfoElement],
    world: World,
    target_trait_id: int,
    include_world_characteristics: bool = True,
) -> bool:
    """
    Returns true if a world has the trait or a trait inheriting from it
    :param target_trait_id: The ID of the trait
    :param world: The dictionary representing the world
    :return: bool
    """
    trait_dict = world.squashed_trait_dict

    trait_id: int
    for trait_id in trait_dict.keys():
        if target_trait_id == trait_id:
            return True
        elif trait_inherits_from_trait(scninfo, trait_id, target_trait_id):
            return True

    if include_world_characteristics:
        characteristic_traits = (world.world_class, world.designation, world.culture)
        characteristic_inherits_from_target = any(
            trait_inherits_from_trait(scninfo, characteristic, target_trait_id)
            for characteristic in characteristic_traits
        )

        if (
            target_trait_id in characteristic_traits
            or characteristic_inherits_from_target
        ):
            return True

    return False


def trait_inherits_from_trait(
    scninfo: List[ScenarioInfoElement],
    child_trait: Union[Trait, int],
    parent_trait: Union[Trait, int],
) -> bool:
    """
    Checks if trait_a inherits from trait_b i.e
    trait_a extends trait_b
    trait_a inheritsFrom trait_b
    :param child_trait:
    :param parent_trait:
    :return:
    """
    try:
        # looks up up trait in scenario info based on id. information from Trait object is not used.
        childs_parent_list = next(
            trait.inherit_from
            for trait in scninfo
            if trait.id == child_trait
            or (isinstance(child_trait, Trait) and trait.id == child_trait.trait_id)
        )
        if childs_parent_list is None:
            return False

        return parent_trait in childs_parent_list or any(
            trait_inherits_from_trait(scninfo, trait, parent_trait)
            for trait in childs_parent_list
        )
    except StopIteration as e:
        raise ValueError("child trait was not found in scenario info") from e
    except KeyError:
        return False


def trait_under_construction(
    squashed_trait_dict: Dict[int, Union[int, Trait]], trait_id: int
) -> bool:
    """Returns true if a trait/structure on a world is in the process of being built"""
    if trait_id not in squashed_trait_dict.keys():
        return False  # world doesn't have it at all
    trait = squashed_trait_dict[trait_id]
    if isinstance(trait, int):
        return False  # its a simple structure, world has it, and its built
    if trait.build_complete is not None:
        return True

    return False


def world_has_fully_built_trait(
    scninfo: List[ScenarioInfoElement], world: World, target_trait_id: int
) -> bool:
    return world_has_trait(
        scninfo, world, target_trait_id, True
    ) and trait_under_construction(world.squashed_trait_dict, target_trait_id)


def type_supercedes_type(
    scninfo: List[ScenarioInfoElement],
    later_improvement_id: int,
    earlier_improvement_id: int,
) -> bool:
    """Returns True if trait_a needs trait_b to be built first at some point"""
    later_improvement = scninfo[later_improvement_id]
    if later_improvement.build_upgrade is not None:
        return earlier_improvement_id in later_improvement.build_upgrade or any(
            type_supercedes_type(scninfo, trait, earlier_improvement_id)
            for trait in later_improvement.build_upgrade
        )
    return False


def get_world_primary_industry_products(world: World) -> Optional[List[int]]:
    primary_industry = next(
        (
            trait
            for trait in world.traits
            if isinstance(trait, Trait) and trait.is_primary
        ),
        None,
    )

    if primary_industry is None or primary_industry.production_data is None:
        return None

    return [
        cast(int, res_id)
        for res_id, pct_alloc, cannot_build in flat_list_to_n_tuples(
            3, primary_industry.build_data
        )
    ]


class TermColors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKCYAN = "\033[96m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
