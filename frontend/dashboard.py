import asyncio
import logging
import logging.handlers
from typing import Any, AsyncGenerator, Optional, cast

from fastapi import Request, Response
from fastapi.responses import HTMLResponse
from fastapi.routing import APIRouter
from fastapi.websockets import WebSocket, WebSocketDisconnect

from frontend.services import templates


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
    return templates.TemplateResponse("pages/dashboard.html", {"request": request})


@router.websocket("/log_stream")
async def stream_logs_ws_example(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        async for log_msg in stream_logs_async(None):
            await websocket.send_text(
                f'<span id="logs" hx-swap-oob="beforeend">{log_msg}</span>'
            )
    except WebSocketDisconnect:
        logging.info("closing websocket")
        await websocket.close()
