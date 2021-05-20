import asyncio
import io
import logging
import logging.handlers
from typing import Any, AsyncGenerator, cast

from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.routing import APIRouter


async def some_random_task() -> None:
    logger = logging.getLogger("some random task")
    for i in range(10):
        await asyncio.sleep(0.7)
        logger.info(f"doing some work! {i}")


router = APIRouter(prefix="/logstreamtest")


async def stream_logs_polling(
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


async def stream_logs_async(
    task: asyncio.Task, period: float = 0.5
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

    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    ch.setFormatter(formatter)

    root_logger.addHandler(ch)

    while not task.done():
        log_record = await logs.get()
        yield log_record.msg + "\n"


@router.get("/")
async def stream_logs_example() -> StreamingResponse:
    return StreamingResponse(
        stream_logs_async(asyncio.create_task(some_random_task()), 1.0)
    )
