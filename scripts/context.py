import asyncio
import logging
from typing import List

from anacreonlib.anacreon_async_client import AnacreonAsyncClient
from anacreonlib.types.request_datatypes import AnacreonApiRequest
from anacreonlib.types.response_datatypes import AnacreonObject, AnacreonObjectWithId, UpdateObject
from rx.subject import BehaviorSubject, Subject


class AnacreonContext:
    def __init__(self, auth: AnacreonApiRequest):
        self._logger = logging.getLogger(str(self.__class__.__name__))
        self.client = AnacreonAsyncClient()
        self.base_request = auth
        self.auth = auth.dict(by_alias=False)

        self._state: List[AnacreonObject] = []
        self.any_update_observable = Subject()
        self.watch_update_observable = Subject()

        self.watch_update_observable.subscribe(lambda _: self._logger.info("Watch update triggered!"))

    @property
    def state(self):
        return self._state

    @state.setter
    def state(self, new_state):
        self._state = new_state
        self.any_update_observable.on_next(self._state)

    def register_response(self, partial_state: List[AnacreonObject]) -> List[AnacreonObject]:
        """
        Update game state based on partial state update

        :param partial_state: The partial state update obtained by calling an API endpoint
        :return: The entire game state
        """
        replaced_ids = set(obj.id for obj in partial_state if isinstance(obj, AnacreonObjectWithId))
        new_update = next((obj for obj in partial_state if isinstance(obj, UpdateObject)), None)
        if new_update is None:
            self._logger.info("Could not find update object inside ")
        else:
            self.base_request.sequence = new_update.sequence

        def obj_was_refreshed(obj: AnacreonObject) -> bool:
            if isinstance(obj, AnacreonObjectWithId):
                obj: AnacreonObjectWithId
                return obj.id in replaced_ids
            elif isinstance(obj, UpdateObject):
                return new_update is not None
            return False

        full_state = [obj for obj in self.state if not obj_was_refreshed(obj)]
        full_state.extend(partial_state)
        self.state = full_state
        self._logger.debug("Integrated partial state")
        return self.state

    async def periodically_update_objects(self, *, period: int = 60):
        while True:
            wait_for_next_period = asyncio.create_task(asyncio.sleep(period))
            partial_state = await self.client.get_objects(self.base_request)
            full_state = self.register_response(partial_state)
            self.watch_update_observable.on_next(full_state)
            await wait_for_next_period

    def __del__(self):
        self.watch_update_observable.dispose()
        self.any_update_observable.dispose()
