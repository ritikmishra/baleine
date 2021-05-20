import asyncio
from dataclasses import dataclass
import json
import pathlib
from asyncio.tasks import Task
from pprint import pprint
from typing import Dict, List, Optional, Tuple, TypedDict

import scripts.creds
from anacreonlib.types.request_datatypes import AnacreonApiRequest
from anacreonlib.types.response_datatypes import OwnedWorld, Trait

from anacreonlib.types.scenario_info_datatypes import Category, ScenarioInfoElement
from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from rx.operators import first
from scripts.context import AnacreonContext, ProductionInfo
from scripts.tasks.simple_tasks import dump_scn_to_json

import scripts.utils
from scripts.utils import TermColors

import logging

logging.basicConfig(
    level=logging.INFO,
    format=f"{TermColors.OKCYAN}%(asctime)s{TermColors.ENDC} - %(name)s - {TermColors.BOLD}%(levelname)s{TermColors.ENDC} - {TermColors.OKGREEN}%(message)s{TermColors.ENDC}",
)

app = FastAPI()

templates = Jinja2Templates(directory=str(pathlib.Path(__file__).parent / "templates"))


class AnacreonContextDependency:
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

            categories: List[Tuple[int, ScenarioInfoElement]] = [
                (id, obj)
                for id, obj in self._context.scenario_info_objects.items()
                if obj.category == Category.COMMODITY or obj.attack_value is not None
            ]

            app.state.RESOURCE_CATEGORIES = categories

        return self._context


anacreon_context = AnacreonContextDependency()


@app.on_event("startup")
async def setup_periodic_update() -> None:
    context = await anacreon_context()
    # TODO: what happens to this task on application exit?
    asyncio.create_task(context.periodically_update_objects())


class ScatterPlot(TypedDict):
    x: List[float]
    y: List[float]
    text: Optional[List[str]]
    hovertext: Optional[List[str]]
    color: Optional[List[float]]


@dataclass
class ScatterPlotPoint:
    x: float
    y: float
    text: str = ""
    hovertext: str = ""
    color: float = 0

    def __post_init__(self) -> None:
        if self.text and self.hovertext:
            self.hovertext = f"{self.text}<br />{self.hovertext}"


def plot_points_to_plot(points: List[ScatterPlotPoint]) -> ScatterPlot:
    x, y, text, hovertext, color = [], [], [], [], []

    for point in points:
        x.append(point.x)
        y.append(point.y)
        text.append(point.text)
        hovertext.append(point.hovertext)
        color.append(point.color)

    return ScatterPlot(
        x=x,
        y=y,
        text=text if any(text) else None,
        hovertext=hovertext if any(hovertext) else None,
        color=color if any(color) else None,
    )


def create_resource_scatterplot(
    context: AnacreonContext, resource_id: int
) -> ScatterPlot:
    worlds = [world for world in context.state if isinstance(world, OwnedWorld)]

    points: List[ScatterPlotPoint] = []
    for world in worlds:
        exportable_ids = scripts.utils.get_world_primary_industry_products(world) or []
        prod_info = context.generate_production_info(world).get(
            resource_id, ProductionInfo()
        )

        world_primary_industry = next(
            (
                trait
                for trait in world.traits
                if isinstance(trait, Trait) and trait.is_primary
            ),
            None,
        )
        world_primary_industry_name = (
            context.scenario_info_objects[world_primary_industry.trait_id].name_desc
            if world_primary_industry is not None
            else None
        )

        exportable_item_names = [
            context.scenario_info_objects[res_id].name_desc or "(unknown)"
            for res_id in exportable_ids
        ] or ["(No exports)"]

        if resource_id not in exportable_ids:
            surplus = -prod_info.produced

        else:
            surplus = (
                prod_info.produced
                - prod_info.exported_optimal
                - prod_info.consumed_optimal
            )

        points.append(
            ScatterPlotPoint(
                x=world.pos[0],
                y=world.pos[1],
                text=f"{world.name} (id: {world.id})",
                hovertext=f"Surplus: {surplus}<br  />Industry name: {world_primary_industry_name}<br  />Exported ids: {'<br  />- '.join(exportable_item_names)}",
                color=surplus,
            )
        )

    return plot_points_to_plot(points)


@app.get("/resource_scatterplot")
def resource_scatterplot_trillum(
    context: AnacreonContext = Depends(anacreon_context),
) -> RedirectResponse:
    trillum_res_id = context.get_scn_info_el_unid("core.trillum").id
    assert trillum_res_id is not None

    return RedirectResponse(f"/resource_scatterplot/{trillum_res_id}")


@app.get("/resource_scatterplot/{resource_id}", response_class=HTMLResponse)
async def resource_scatterplot(
    request: Request,
    resource_id: int,
    context: AnacreonContext = Depends(anacreon_context),
) -> Response:
    plot = create_resource_scatterplot(context, resource_id)

    commodity_resources: List[Tuple[int, ScenarioInfoElement]] = [
        (id, obj)
        for id, obj in context.scenario_info_objects.items()
        if obj.category == Category.COMMODITY or obj.attack_value is not None
    ]

    return templates.TemplateResponse(
        "pages/resource_viewer.html",
        {
            "request": request,
            "resource_id": resource_id,
            "plot": plot,
            "commodity_resources": commodity_resources,
        },
    )
