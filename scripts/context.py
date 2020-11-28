import asyncio
import collections
import functools
import logging
from typing import List, Tuple, Callable

from anacreonlib.anacreon_async_client import AnacreonAsyncClient
from anacreonlib.types.request_datatypes import AnacreonApiRequest
from anacreonlib.types.response_datatypes import AnacreonObject, AnacreonObjectWithId, UpdateObject, World
from anacreonlib.types.scenario_info_datatypes import Category, ScenarioInfo, ScenarioInfoElement
from rx.subject import BehaviorSubject, Subject

from scripts.utils import flat_list_to_tuples, world_has_trait, trait_under_construction, type_supercedes_type

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

        self.game_info: ScenarioInfo

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
        self.game_info = await self.client.get_game_info(self.base_request.auth_token, self.base_request.game_id)
        for item in self.game_info.scenario_info:
            if item.attack_value is not None:
                attack_value = float(item.attack_value)

                if item.category in (Category.FIXED_UNIT, Category.ORBITAL_UNIT, Category.MANEUVERING_UNIT):
                    self.sf_calc[item.id] = attack_value
                    if item.category == Category.MANEUVERING_UNIT:
                        self.maneuvering_unit_calc[item.id] = attack_value
                    if item.id in self.missile_calc.keys():
                        self.missile_calc[item.id] = attack_value

                elif item.category == Category.GROUND_UNIT:
                    self.gf_calc[item.id] = attack_value

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

    def get_valid_improvement_list(self, world: World) -> List[ScenarioInfoElement]:
        """Returns a list of improvements that can be built"""
        valid_improvement_ids: List[ScenarioInfoElement] = []
        trait_dict = world.squashed_trait_dict
        this_world_has_trait: Callable[[int], bool] = functools.partial(world_has_trait, self.game_info.scenario_info, world)

        for improvement in self.game_info.scenario_info:
            if improvement.id is None or improvement.category is None:
                continue

            if improvement.category == Category.IMPROVEMENT \
                    and not improvement.npe_only \
                    and not improvement.designation_only \
                    and improvement.build_time is not None \
                    and improvement.id not in trait_dict.keys() \
                    and (improvement.min_tech_level is None or world.tech_level >= improvement.min_tech_level):
                if improvement.build_upgrade:
                    # Check if we have the predecessor structure.
                    has_predecessor_structure = any(this_world_has_trait(predecessor)
                                                    and not trait_under_construction(trait_dict, predecessor)
                                                    for predecessor in improvement.build_upgrade)
                    if not has_predecessor_structure:
                        continue
                if improvement.build_requirements:
                    # Check we have requirements. Requirements can be any trait.
                    requirement_missing = any(not this_world_has_trait(requirement_id)
                                              or trait_under_construction(trait_dict, requirement_id)
                                              for requirement_id in improvement.build_requirements)
                    if requirement_missing:
                        continue

                if improvement.build_exclusions:
                    # Check if we are banned from doing so
                    if any(this_world_has_trait(exclusion_id)
                           for exclusion_id in improvement.build_exclusions):
                        continue

                # Check if this trait would be a downgrade from an existing trait
                if any(type_supercedes_type(self.game_info.scenario_info, existing_trait_id, improvement.id)
                       for existing_trait_id in trait_dict.keys()):
                    continue

                # if this is a tech advancement structure, check if we can build it
                if improvement.role == "techAdvance":
                    if (improvement.tech_level_advance or 0) <= world.tech_level:
                        continue

                # we have not continue'd so it is ok to  build
                valid_improvement_ids.append(improvement)

        return valid_improvement_ids
