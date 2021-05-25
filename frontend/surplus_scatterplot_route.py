from dataclasses import dataclass
from typing import List, Optional, Tuple, TypedDict

import scripts
from anacreonlib.types.response_datatypes import OwnedWorld, Trait
from anacreonlib.types.scenario_info_datatypes import Category, ScenarioInfoElement
from fastapi import Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.routing import APIRouter
from scripts.context import AnacreonContext, ProductionInfo

from frontend.services import anacreon_context, templates


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


def find_total_produced_consumed(
    context: AnacreonContext, resource_id: int
) -> ProductionInfo:
    our_worlds = (world for world in context.state if isinstance(world, OwnedWorld))
    total = ProductionInfo()
    for world in our_worlds:
        prod_info = context.generate_production_info(world).get(
            resource_id, ProductionInfo()
        )
        total += prod_info

    return total


def find_total_stockpile(context: AnacreonContext, resource_id: int) -> float:
    our_worlds = (world for world in context.state if isinstance(world, OwnedWorld))
    total = 0.0
    for world in our_worlds:
        resource_id_to_qty_map = dict(
            scripts.utils.flat_list_to_tuples(world.resources)
        )
        total += resource_id_to_qty_map.get(resource_id, 0.0)
    return total


router = APIRouter(prefix="/resource_scatterplot")


@router.get("/", name="scatterplot_root")
def resource_scatterplot_trillum(
    context: AnacreonContext = Depends(anacreon_context),
) -> RedirectResponse:
    trillum_res_id = context.get_scn_info_el_unid("core.trillum").id
    assert trillum_res_id is not None

    return RedirectResponse(f"/resource_scatterplot/{trillum_res_id}")


@router.get("/{resource_id}", response_class=HTMLResponse)
async def resource_scatterplot(
    request: Request,
    resource_id: int,
    context: AnacreonContext = Depends(anacreon_context),
) -> Response:
    plot = create_resource_scatterplot(context, resource_id)

    resoure_aggregate_prod_info = find_total_produced_consumed(context, resource_id)

    total_stockpile = find_total_stockpile(context, resource_id)

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
            "prod_info": resoure_aggregate_prod_info,
            "total_stockpile": total_stockpile,
            "commodity_resources": commodity_resources,
        },
    )
