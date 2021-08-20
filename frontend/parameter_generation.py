"""
This module contains utilities for generating HTML frontend + form processing code
from function signatures

There are 3 components
1. The actual newtype/type annotation
2. A method to generate an HTML frontend form entry dealy bob
    - This is separate from the labeling
3. A method to parse the actual result into a proper object

"""

import typing
import functools
from uuid import uuid4
import abc
from collections.abc import Mapping
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Dict,
    Generic,
    Iterable,
    List,
    Mapping,
    NamedTuple,
    NewType,
    Protocol,
    Sequence,
    Tuple,
    Type,
    TypeVar,
    cast,
)
import logging
from anacreonlib.anacreon import Anacreon
from anacreonlib.types.response_datatypes import Fleet, OwnedWorld, World
from anacreonlib.types.scenario_info_datatypes import Category
from fastapi.param_functions import Form
from .utils import LosslessMutableMultiDict

OurFleetId = NewType("OurFleetId", int)
OurWorldId = NewType("OurWorldId", int)
AnyWorldId = NewType("AnyWorldId", int)
CommodityId = NewType("CommodityId", int)

T = TypeVar("T")


class FormInputBase(abc.ABC, Generic[T]):
    def __init__(self) -> None:
        pass

    @abc.abstractmethod
    def get_html(self, name: str, func_id: int) -> str:
        pass

    @abc.abstractmethod
    def _parse_from_text(self, text: str) -> T:
        pass

    def parse_form_response(self, form_response: LosslessMutableMultiDict, name: str) -> T:
        return self._parse_from_text(form_response.pop_single_key(name))


TPRIM = TypeVar("TPRIM", str, int, float)


class PrimitiveSelector(FormInputBase[TPRIM]):
    def __init__(self, tt: Type[TPRIM]) -> None:
        self._type: Type[TPRIM] = tt

    def get_html(self, name: str, func_id: int) -> str:
        if self._type is str:
            return f'<input class="input" name={name} type="text" >'
        elif self._type is int:
            return f'<input class="input" name={name} type="number" step="1">'
        elif self._type is float:
            return f'<input class="input" name={name} type="number">'
        else:
            raise ValueError(f"Unsupported primitive type: {self._type!r}")

    def _parse_from_text(self, text: str) -> TPRIM:
        return self._type(text)


class ListSelector(FormInputBase[List[T]], Generic[T]):
    def __init__(self, child_selector: FormInputBase[T], init_qty: int = 1) -> None:
        super().__init__()
        self.child_selector = child_selector
        self._init_qty = 1

    def get_html(self, name: str, func_id: int) -> str:
        ret = ""
        ret += self.child_selector.get_html(name, func_id)
        # FIXME add func id
        ret += f"""
        <button class="button is-light is-small" 
                type="button"
                hx-post="/api/list_func_param/get_new_row"
                hx-vals='{{"func_id": {func_id}, "param_name": "{name}"}}'
                hx-params="func_id, param_name"
                hx-target="this"
                hx-swap="beforebegin">
            ➕ Add
        </button>"""
        return ret

    def _parse_from_text(self, text: str) -> List[T]:
        # Yes, this violates the Liskov Substitution Principle
        # No, I don't care
        raise NotImplementedError

    def parse_form_response(self, form_response: LosslessMutableMultiDict, name: str) -> List[T]:
        ret = []
        while True:
            try:
                ret.append(self.child_selector.parse_form_response(form_response, name))
            except LookupError:
                break
            
        return ret


T1 = TypeVar("T1")
T2 = TypeVar("T2")


class TupleSelector(FormInputBase[Tuple[T1, T2]], Generic[T1, T2]):
    def __init__(
        self, child_1_selector: FormInputBase[T1], child_2_selector: FormInputBase[T2]
    ) -> None:
        super().__init__()
        self._child_1_selector: FormInputBase[T1] = child_1_selector
        self._child_2_selector: FormInputBase[T2] = child_2_selector

    def get_html(self, name: str, func_id: int) -> str:
        return f"""
        <div class="columns">
            <div class="column is-6">{self._child_1_selector.get_html(name + "_0", func_id)}</div>
            <div class="column is-6">{self._child_2_selector.get_html(name + "_1", func_id)}</div>
        </div>
        """

    def _parse_from_text(self, text: str) -> Tuple[T1, T2]:
        raise NotImplementedError

    def parse_form_response(self, form_response: LosslessMutableMultiDict, name: str) -> Tuple[T1, T2]:
        first = self._child_1_selector.parse_form_response(form_response, name + "_0")
        second = self._child_2_selector.parse_form_response(form_response, name + "_1")
        return (first, second)


class DictSelector(FormInputBase[Dict[T1, T2]], Generic[T1, T2]):
    def __init__(
        self, key_selector: FormInputBase[T1], value_selector: FormInputBase[T2]
    ) -> None:
        super().__init__()
        self.child_selector: ListSelector[Tuple[T1, T2]] = ListSelector(
            TupleSelector(key_selector, value_selector)
        )

    def get_html(self, name: str, func_id: int) -> str:
        return self.child_selector.get_html(name, func_id)

    def _parse_from_text(self, text: str) -> Dict[T1, T2]:
        raise NotImplementedError

    def parse_form_response(self, form_response: LosslessMutableMultiDict, name: str) -> Dict[T1, T2]:
        items: List[Tuple[T1, T2]] = self.child_selector.parse_form_response(
            form_response, name
        )
        print(repr(items))
        ret: Dict[T1, T2] = {}
        for k, v in items:
            ret[k] = v
        return ret


U = TypeVar("U")


@dataclass
class NameAndValue(Generic[U]):
    name: str
    value: U


class ObjectSelector(FormInputBase[U], Generic[U]):
    def __init__(self, objects: Mapping[int, NameAndValue[U]]) -> None:
        self._objects = objects

    def get_html(self, name: str, func_id: int) -> str:
        objects = "\n".join(
            (
                # FIXME: this is vulnerable to XSS
                f'<option value="{id}">{obj.name} (id {obj.value})</option>'
                for id, obj in self._objects.items()
            )
        )

        datalist_id = f"{name}_{uuid4()}"

        return f"""
            <input class="input" list="{datalist_id}" name="{name}">
            <datalist id="{datalist_id}">{objects}</datalist>
        """

    def _parse_from_text(self, text: str) -> U:
        return self._objects[int(text)].value


OurFleetsSelector: Callable[
    [Anacreon], ObjectSelector[OurFleetId]
] = lambda context: ObjectSelector(
    {
        f.id: NameAndValue(f.name, OurFleetId(f.id))
        for f in context.space_objects.values()
        if isinstance(f, Fleet) and f.sovereign_id == context.sov_id
    },
)

OurWorldSelector: Callable[
    [Anacreon], ObjectSelector[OurWorldId]
] = lambda context: ObjectSelector(
    {
        w.id: NameAndValue(w.name, OurWorldId(w.id))
        for w in context.space_objects.values()
        if isinstance(w, OwnedWorld)
    },
)

AnyWorldSelector: Callable[
    [Anacreon], ObjectSelector[AnyWorldId]
] = lambda context: ObjectSelector(
    {
        w.id: NameAndValue(w.name, AnyWorldId(w.id))
        for w in context.space_objects.values()
        if isinstance(w, OwnedWorld)
    },
)

CommoditySelector: Callable[
    [Anacreon], ObjectSelector[CommodityId]
] = lambda context: ObjectSelector(
    {
        c.id: NameAndValue(c.name, CommodityId(c.id))
        for c in context.game_info.scenario_info
        if (c.category == Category.COMMODITY or c.attack_value is not None)
        and (c.name is not None and c.id is not None)
    }
)


def get_selector(context: Anacreon, val_type: type) -> FormInputBase[Any]:
    get = functools.partial(get_selector, context)
    if val_type == OurWorldId:
        return OurWorldSelector(context)
    elif val_type == OurFleetId:
        return OurFleetsSelector(context)
    elif val_type == AnyWorldId:
        return AnyWorldSelector(context)
    elif val_type == CommodityId:
        return CommoditySelector(context)
    elif val_type in (str, int, float):
        return PrimitiveSelector(val_type)
    elif typing.get_origin(val_type) is list:
        type_param = typing.get_args(val_type)[0]
        return ListSelector(get(type_param))
    elif typing.get_origin(val_type) is tuple:
        t1, t2 = typing.get_args(val_type)
        return TupleSelector(get(t1), get(t2))
    elif typing.get_origin(val_type) is dict:
        k, v = typing.get_args(val_type)
        return DictSelector(get(k), get(v))
    else:
        raise TypeError(f"unspported type as dash func param: {val_type!r}")


# ---------------------


# ---------------------


async def fake_send_fleets(
    context: Anacreon,
    resources: Dict[AnyWorldId, int],
    fleet_id: OurFleetId,
    fleets: List[OurFleetId]
) -> None:
    print(
        f"""
    Context: {context!r}
    resources: {resources!r}
    fleet_id: {fleet_id!r}
    fleets: {fleets!r}
    """
    )
