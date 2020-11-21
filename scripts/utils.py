import math
from enum import Enum
from typing import List

from anacreonlib.types.type_hints import Location


def flat_list_to_tuples(exploration: List[List[float]]) -> List[Location]:
    """
    Convert a list of the form `[1, 2, 3, 4, ...]` into the list of tuples `[(1, 2), (3, 4)]`
    """
    ret: List[Location] = []
    for contour in exploration:
        # pair up successive elements
        ret.extend(zip(contour[::2], contour[1::2]))
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
