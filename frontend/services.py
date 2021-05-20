"""Module containing function/class definitions for request services"""

import pathlib
from typing import Optional

import scripts.creds
from anacreonlib.types.request_datatypes import AnacreonApiRequest
from fastapi.templating import Jinja2Templates
from scripts.context import AnacreonContext


class AnacreonContextDependency:
    """Singleton injectable dependency for the AnacreonContext object"""

    def __init__(self) -> None:
        self._context: Optional[AnacreonContext] = None

    async def __call__(self) -> AnacreonContext:
        if self._context is None:
            self._context = await AnacreonContext.create(
                AnacreonApiRequest(
                    auth_token=scripts.creds.ACCESS_TOKEN,
                    game_id=scripts.creds.GAME_ID,
                    sovereign_id=scripts.creds.SOV_ID,
                )
            )

            await self._context.update_once()

        return self._context


anacreon_context = AnacreonContextDependency()

templates = Jinja2Templates(directory=str(pathlib.Path(__file__).parent / "templates"))
