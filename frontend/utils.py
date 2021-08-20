from starlette.datastructures import ImmutableMultiDict
from typing import List

class LosslessMutableMultiDict:
    """Similar to the starlette multidict, but popping a key
    does not remove all instances of the key (i.e it never throws away data when
    you don't explicitly want that)
    """
    def __init__(self, multi_dict: ImmutableMultiDict) -> None:
        self._items = multi_dict.multi_items()

    def pop_single_key(self, key: str) -> str:
        try:
            idx, value = next((i, v) for i, (k, v) in enumerate(self._items) if k == key)
            del self._items[idx]
            return value
        except StopIteration:
            raise LookupError(f"Could not find value associated with key {key!r}")

    def pop_all_key(self, key: str) -> List[str]:
        new_items = []
        ret = []
        for k, v in self._items:
            if k == key:
                ret.append(v)
            else:
                new_items.append((k, v))

        self._items = new_items
        return ret

    def __len__(self) -> int:
        return len(self._items)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} items={self._items!r}>"