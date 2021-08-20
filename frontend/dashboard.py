from .utils import LosslessMutableMultiDict
from frontend.parameter_generation import AnyWorldId, AnyWorldSelector, DictSelector, ListSelector, ObjectSelector, OurFleetId, OurFleetsSelector, OurWorldId, OurWorldSelector, PrimitiveSelector, fake_send_fleets, get_selector
import functools
import asyncio
import logging
import logging.handlers

from anacreonlib.anacreon import Anacreon
from websockets.exceptions import ConnectionClosedOK
from fastapi.param_functions import Body, Depends, Form
import typing
from typing import Any, AsyncGenerator, Callable, Coroutine, Dict, List, Optional, Tuple, cast
from fastapi import Request, Response, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRouter
from fastapi.websockets import WebSocket

from frontend.services import anacreon_context, templates

from dataclasses import dataclass

from scripts.tasks import (
    improvement_related_tasks,
    garbage_collect_trade_routes,
    balance_trade_routes,
    simple_tasks,
)


@dataclass
class DashboardFunction:
    name: str
    func: Callable[..., Coroutine[Any, Any, Any]]
    concurrent_allowed: bool = False

    def __post_init__(self) -> None:
        logger = logging.getLogger("run_anacreon_task")
        self.lock: Optional[asyncio.Lock] = None

        if not self.concurrent_allowed:
            lock = asyncio.Lock()

            # wrapper around the function that can only be executed once
            old_func = self.func

            @functools.wraps(old_func)
            async def new_func(*args: Any, **kwargs: Any) -> Any:
                if lock.locked():
                    logger.error(f"cannot concurrently execute task {repr(self.name)}")
                else:
                    async with lock:
                        logger.info(f"Starting execution of task {self.name}")
                        await old_func(*args, **kwargs)
                    logger.info(f"Done executing task '{self.name}'")

            self.func = new_func
            self.lock = lock


dashboard_functions: List[DashboardFunction] = [
    DashboardFunction(
        "Auto-build structures", improvement_related_tasks.build_habitats_spaceports
    ),
    DashboardFunction(
        "Garbage-collect trade routes",
        garbage_collect_trade_routes.garbage_collect_trade_routes,
    ),
    DashboardFunction(
        "Balance trade routes", balance_trade_routes.balance_trade_routes
    ),
    DashboardFunction(
        "Deallocate defense structures",
        simple_tasks.zero_out_defense_structure_allocation,
    ),
    DashboardFunction(
        "Fake send fleets",
        fake_send_fleets
    )
]


async def stream_logs_async(
    task: Optional[asyncio.Task], period: float = 0.5
) -> AsyncGenerator[str, None]:
    """This generator yields the content that was added to stdout as long as some task is alive.
    This adds a logging handler that adds new log messages to a queue. Each time a new message arrives
    in the queue, it is sent to the client.

    Args:
        task (asyncio.Task): Some task that is emitting stuff to sys.stdout.
        period (float, optional): How often (in seconds) to check that new things have been printed. Defaults to 0.5.

    Yields:
        str: new content added to stdout
    """
    root_logger = logging.getLogger()

    logs: "asyncio.Queue[logging.LogRecord]" = asyncio.Queue()
    ch = logging.handlers.QueueHandler(cast(Any, logs))
    ch.setLevel(logging.DEBUG)

    # make it look good in the browser
    html_formatter = logging.Formatter(
        '<span><span class="cyan">%(asctime)s</span> - %(name)s - <b>%(levelname)s</b> - <span class="green">%(message)s</span></span>'
    )
    ch.setFormatter(html_formatter)

    root_logger.addHandler(ch)

    while task is None or not task.done():
        log_record = await logs.get()
        yield log_record.msg + "\n"


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> Response:
    return templates.TemplateResponse(
        "pages/dashboard.html", {"request": request, "actions": dashboard_functions}
    )


@router.post("/api/run_task/{func_idx}", name="run_task")
async def run_parametrized_anacreon_task(
    func_idx: int,
    request: Request,
    context: Anacreon = Depends(anacreon_context)
) -> HTMLResponse:
    logger = logging.getLogger("run_parametrized_anacreon_task")
    form_data = LosslessMutableMultiDict(await request.form())
    func = dashboard_functions[func_idx].func
    logger.info(f"Running parametrized task id {func_idx} (name: {func.__name__!r})")
    type_hints = typing.get_type_hints(func)
    if "return" in type_hints:
        del type_hints["return"]
    kwargs: Dict[str, Any] = {}
    for name, type in type_hints.items():
        if type == Anacreon:
            kwargs[name] = context
            continue
        
        selector = get_selector(context, type)
        kwargs[name] = selector.parse_form_response(form_data, name)

    if len(form_data) > 0:
        logger.warning(f"Some form data was left unprocessed!")
        logger.warning(f"{form_data!r}")
    
    asyncio.create_task(func(**kwargs))
    return HTMLResponse("")

@router.post("/api/cancel_task", name="cancel_task", response_class=HTMLResponse)
async def hide_task_modal() -> HTMLResponse:
    return HTMLResponse("")

@router.get("/api/get_action_params/{func_idx}", name="get_action_params", response_class=HTMLResponse)
async def get_action_params(
    func_idx: int,
    request: Request,
    context: Anacreon = Depends(anacreon_context)
) -> HTMLResponse:
    func = dashboard_functions[func_idx].func
    type_hints = typing.get_type_hints(func)
    if "return" in type_hints:
        del type_hints["return"]
    @dataclass
    class Param:
        name: str
        markup: str

    params: List[Param] = []
    for name, type in type_hints.items():
        if type == Anacreon:
            continue
        
        selector = get_selector(context, type)
        params.append(Param(name, selector.get_html(name, func_idx)))
    
    return templates.TemplateResponse(
        "action_form.html",
        {
            "request": request,
            "title": func.__name__,
            "params": params,
            "func_idx": func_idx
        }
    )

@router.post("/api/list_func_param/get_new_row", name="get_new_row", response_class=HTMLResponse)
async def get_new_row(
    context: Anacreon = Depends(anacreon_context),
    func_id: int = Form(...),
    param_name: str = Form(...),
) -> HTMLResponse:
    func = dashboard_functions[func_id].func
    func_type_hints = typing.get_type_hints(func)
    selector = get_selector(context, func_type_hints[param_name])
    assert isinstance(selector, (ListSelector, DictSelector))
    if isinstance(selector, ListSelector):
        child_selector = selector.child_selector
    elif isinstance(selector, DictSelector):
        child_selector = selector.child_selector.child_selector
    else:
        raise Exception("unreachable")
    
    html = child_selector.get_html(param_name, func_id)
    return HTMLResponse(f"""
    <div class="columns is-vcentered">
        <div class="column">
            {html}
        </div>
        <div class="column is-narrow">
            <button class="delete is-small" type="button" hx-post="/api/cancel_task" hx-target="closest div.columns"></button>
        </div>
    </div>
    """)

@router.websocket("/log_stream", name="log_stream")
async def stream_logs_ws_example(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        async for log_msg in stream_logs_async(None):
            await websocket.send_text(
                f'<span id="logs" hx-swap-oob="beforeend">{log_msg}</span>'
            )
    except ConnectionClosedOK:
        # Not an error case
        pass
    finally:
        logging.info("closing websocket")
        await websocket.close()
        logging.info("closed websocket")
