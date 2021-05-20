import asyncio
import io
import logging
from typing import AsyncGenerator

from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.routing import APIRouter


async def some_random_task() -> None:
    logger = logging.getLogger("some random task")
    for i in range(10):
        await asyncio.sleep(0.6)
        logger.info(f"doing some work! {i}")


router = APIRouter(prefix="/logstreamtest")


async def periodic_read(
    task: asyncio.Task, period: float = 0.5
) -> AsyncGenerator[str, None]:
    """This generator yields the content that was added to stdout as long as some task is alive.

    Args:
        task (asyncio.Task): Some task that is emitting stuff to sys.stdout.
        period (float, optional): How often (in seconds) to check that new things have been printed. Defaults to 0.5.

    Yields:
        str: new content added to stdout
    """
    root_logger = logging.getLogger()

    log_capture_string = io.StringIO()
    ch = logging.StreamHandler(log_capture_string)
    ch.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    ch.setFormatter(formatter)

    root_logger.addHandler(ch)

    last_read_pos = 0

    while not task.done():
        await asyncio.sleep(period)
        log_capture_string.seek(last_read_pos)
        to_yield = log_capture_string.read()
        yield to_yield
        last_read_pos += len(to_yield)


@router.get("/")
async def stream_logs() -> StreamingResponse:
    return StreamingResponse(
        periodic_read(asyncio.create_task(some_random_task()), 1.0)
    )
