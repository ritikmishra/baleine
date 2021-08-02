"""Module containing function/class definitions for request services"""

import pathlib
from typing import Optional

from anacreonlib.anacreon import Anacreon

import scripts.creds
from fastapi.templating import Jinja2Templates


class AnacreonContextDependency:
    """Singleton injectable dependency for the AnacreonContext object"""

    def __init__(self) -> None:
        self._context: Optional[Anacreon] = None

    async def __call__(self) -> Anacreon:
        if self._context is None:
            self._context = await Anacreon.from_auth_token(
                auth_token=scripts.creds.ACCESS_TOKEN,
                game_id=scripts.creds.GAME_ID,
            )

            await self._context.get_objects()

        return self._context


anacreon_context = AnacreonContextDependency()


def format_num(number: float) -> str:
    return "{:,.2f}".format(number)


templates = Jinja2Templates(directory=str(pathlib.Path(__file__).parent / "templates"))
templates.env.filters["format_num"] = format_num
