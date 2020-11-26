import asyncio
import collections
import logging
from typing import List, Tuple

from anacreonlib.anacreon_async_client import AnacreonAsyncClient
from anacreonlib.types.request_datatypes import AnacreonApiRequest
from anacreonlib.types.response_datatypes import AnacreonObject, AnacreonObjectWithId, UpdateObject
from rx.subject import BehaviorSubject, Subject

from scripts.utils import flat_list_to_tuples

MilitaryForces = collections.namedtuple("MilitaryForces", ["space_forces", "ground_forces", "missile_forces", "maneuvering_unit_forces"])
"""
:ivar space_forces: The space forces value as displayed in the anacreon web interface
:ivar ground_forces: The space forces value as displayed in the anacreon web interface
:ivar missile_forces: A subset of space forces; only accounts for defenses that shoot missiles
:ivar maneuvering_unit_forces: A subset of space forces; only accounts for maneuvering units (i.e ships)
"""

class AnacreonContext:
    def __init__(self, auth: AnacreonApiRequest):
        self._logger = logging.getLogger(str(self.__class__.__name__))
        self.client = AnacreonAsyncClient()
        self.base_request = auth
        self.auth = auth.dict(by_alias=False)

        self._state: List[AnacreonObject] = []
        self.any_update_observable = Subject()
        self.watch_update_observable = Subject()

        self.sf_calc = dict()
        self.gf_calc = dict()
        self.missile_calc = {1: 0.0, 244: 0.0, 142: 0.0}
        self.maneuvering_unit_calc = dict()

        self.watch_update_observable.subscribe(lambda _: self._logger.info("Watch update triggered!"))

    @staticmethod
    async def create(auth: AnacreonApiRequest):
        ret = AnacreonContext(auth)
        await ret._generate_force_calculation_dict()
        return ret

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
            def stringify(x):
                if isinstance(x, str):
                    return x
                return repr(type(x))
            self._logger.info(list(map(stringify, partial_state)))
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
        """
        Coroutine that runs forever, calling getObjects every so often
        """
        while True:
            wait_for_next_period = asyncio.create_task(asyncio.sleep(period))
            partial_state = await self.client.get_objects(self.base_request)
            full_state = self.register_response(partial_state)
            self.watch_update_observable.on_next(full_state)
            await wait_for_next_period

    def __del__(self):
        self.watch_update_observable.dispose()
        self.any_update_observable.dispose()

    async def _generate_force_calculation_dict(self) -> None:
        """
        Generate the dictionaries required to calculate space and ground force of an object

        :return: None
        """
        game_info = await self.client.get_game_info(self.base_request.auth_token, self.base_request.game_id)
        for item in game_info["scenarioInfo"]:
            try:
                attack_value = float(item["attackValue"])

                if item["category"] == "fixedUnit" or item["category"] == "orbitalUnit" or item["category"] == "maneuveringUnit":
                    self.sf_calc[int(item["id"])] = attack_value
                    if item["category"] == "maneuveringUnit":
                        self.maneuvering_unit_calc[int(item["id"])] = attack_value
                    if item["id"] in self.missile_calc.keys():
                        self.missile_calc[item["id"]] = attack_value

                elif item["category"] == "groundUnit":
                    self.gf_calc[int(item["id"])] = attack_value
            except KeyError:
                # There are 3 or 4 items in the scenario info that do not have a category
                continue

    def get_forces(self, resources: List[float]) -> MilitaryForces:
        """
        Calculate the space and ground force of something

        :param resources: The resources list of the object
        :return: A tuple of the form (space force, ground force)
        """
        if len(self.sf_calc) == 0 or len(self.gf_calc) == 0:
            raise ValueError("SF/GF values were not precalculated prior to calling AnacreonContext#get_forces")
        sf = 0.0
        gf = 0.0
        maneuveringunit_force = 0.0
        missile_force = 0.0

        for item_id, item_qty in flat_list_to_tuples(resources):
            if item_id in self.sf_calc.keys():  # x is the count of the resource
                sf += float(item_qty) * self.sf_calc[item_id]
                if item_id in self.maneuvering_unit_calc.keys():
                    maneuveringunit_force += float(item_qty) * self.maneuvering_unit_calc[item_id]
                if item_id in self.missile_calc.keys():
                    missile_force += float(item_qty) * self.missile_calc[item_id]
            elif item_id in self.gf_calc.keys():
                gf += float(item_qty) * self.gf_calc[item_id]
        return MilitaryForces(sf/100, gf/100, maneuveringunit_force/100, missile_force/100)