import asyncio

from frontend.services import anacreon_context
from frontend import surplus_scatterplot_route, dashboard
from fastapi import FastAPI

from scripts.utils import TermColors

import logging

logging.basicConfig(
    level=logging.INFO,
    format=f"{TermColors.OKCYAN}%(asctime)s{TermColors.ENDC} - %(name)s - {TermColors.BOLD}%(levelname)s{TermColors.ENDC} - {TermColors.OKGREEN}%(message)s{TermColors.ENDC}",
)

app = FastAPI()


@app.on_event("startup")
async def setup_periodic_update() -> None:
    context = await anacreon_context()
    # TODO: what happens to this task on application exit?
    context.call_get_objects_periodically()


app.include_router(surplus_scatterplot_route.router)
app.include_router(dashboard.router)
