import functools
import asyncio
import logging
import logging.handlers

from anacreonlib import AnacreonClientWrapper
from fastapi.param_functions import Depends
from typing import Any, AsyncGenerator, Callable, Coroutine, List, Optional, Tuple, cast

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


@router.post("/api/run_task/{action_idx}", name="run_task", status_code=204)
async def run_anacreon_task(
    action_idx: int,
    context: AnacreonClientWrapper = Depends(anacreon_context),
) -> None:
    dashboard_func = dashboard_functions[action_idx]

    async def wrapper() -> None:
        await dashboard_func.func(context=context)

    asyncio.create_task(wrapper())


@router.websocket("/log_stream", name="log_stream")
async def stream_logs_ws_example(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        async for log_msg in stream_logs_async(None):
            await websocket.send_text(
                f'<span id="logs" hx-swap-oob="beforeend">{log_msg}</span>'
            )
    finally:
        logging.info("closing websocket")
        await websocket.close()
        logging.info("closed websocket")
