import math
from typing import List, Dict, TypeVar, Union

from anacreonlib.types.type_hints import Location

T = TypeVar('T')
U = TypeVar('U')


def flat_list_to_tuples(lst: List[float]) -> List[Location]:
    """
    Convert a list of the form `[1, 2, 3, 4, ...]` into the list of tuples `[(1, 2), (3, 4)]`
    """
    return list(zip(lst[::2], lst[1::2]))


def dict_to_flat_list(dict: Dict[T, U]) -> List[Union[T, U]]:
    """
    Convert a dict to a flat list alternating between keys and values
    :param dict: A dict e.g `{1: 2, 3: 4, 5: 6}`
    :return: A list alternating between keys and values (e.g `[1, 2, 3, 4, 5, 6]`)
    """
    ret = []
    for k, v in dict.items():
        ret.append(k)
        ret.append(v)
    return ret


def dist(pointA: Location, pointB: Location) -> float:
    dist2 = sum(map(lambda a, b: (a - b) * (a - b), pointA, pointB))
    return math.sqrt(dist2)


class TermColors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
