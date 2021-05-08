from __future__ import annotations
import asyncio
import collections
import dataclasses
import functools
import logging
from typing import (
    Any,
    Awaitable,
    DefaultDict,
    List,
    Sequence,
    Tuple,
    Callable,
    TypedDict,
    Union,
    Dict,
    Optional,
    cast,
    NamedTuple,
)

from anacreonlib.anacreon_async_client import AnacreonAsyncClient
from anacreonlib.types.request_datatypes import AnacreonApiRequest
from anacreonlib.types.response_datatypes import (
    AnacreonObject,
    AnacreonObjectWithId,
    UpdateObject,
    World,
    Trait,
    OwnedWorld,
    Fleet,
)
from anacreonlib.types.scenario_info_datatypes import (
    Category,
    ScenarioInfo,
    ScenarioInfoElement,
)
from rx.subject import BehaviorSubject, Subject

from scripts import utils
from scripts.utils import (
    T,
    flat_list_to_tuples,
    world_has_trait,
    trait_under_construction,
    type_supercedes_type,
)


class MilitaryForces(NamedTuple):
    space_forces: float
    ground_forces: float
    missile_forces: float
    maneuvering_unit_forces: float


"""
:ivar space_forces: The space forces value as displayed in the anacreon web interface
:ivar ground_forces: The space forces value as displayed in the anacreon web interface
:ivar missile_forces: A subset of space forces; only accounts for defenses that shoot missiles
:ivar maneuvering_unit_forces: A subset of space forces; only accounts for maneuvering units (i.e ships)
"""


@dataclasses.dataclass(eq=True)
class ProductionInfo:
    # resource_id: int = -1
    available: float = 0
    consumed: float = 0
    exported: float = 0
    imported: float = 0
    produced: float = 0
    consumed_optimal: float = 0
    exported_optimal: float = 0
    imported_optimal: float = 0
    produced_optimal: float = 0

    def __add__(self: ProductionInfo, other: ProductionInfo) -> ProductionInfo:
        return ProductionInfo(
            available=self.available + other.available,
            consumed=self.consumed + other.consumed,
            imported=self.imported + other.imported,
            exported=self.exported + other.exported,
            produced=self.produced + other.produced,
            consumed_optimal=self.consumed_optimal + other.consumed_optimal,
            exported_optimal=self.exported_optimal + other.exported_optimal,
            imported_optimal=self.imported_optimal + other.imported_optimal,
            produced_optimal=self.produced_optimal + other.produced_optimal,
        )

    def __sub__(self: ProductionInfo, other: ProductionInfo) -> ProductionInfo:
        return ProductionInfo(
            available=self.available - other.available,
            consumed=self.consumed - other.consumed,
            imported=self.imported - other.imported,
            exported=self.exported - other.exported,
            produced=self.produced - other.produced,
            consumed_optimal=self.consumed_optimal - other.consumed_optimal,
            exported_optimal=self.exported_optimal - other.exported_optimal,
            imported_optimal=self.imported_optimal - other.imported_optimal,
            produced_optimal=self.produced_optimal - other.produced_optimal,
        )


class RequestAuthInfo(TypedDict):
    auth_token: str
    game_id: str
    sovereign_id: Union[str, int]
    sequence: Optional[int]


class AnacreonContext:
    def __init__(self, auth: AnacreonApiRequest):
        self._logger = logging.getLogger(str(self.__class__.__name__))
        self.client: AnacreonAsyncClient = AnacreonAsyncClient()
        self.base_request: AnacreonApiRequest = auth

        self._state: List[AnacreonObject] = []
        self._state_dict: Dict[int, AnacreonObjectWithId] = {}

        self.any_update_observable = Subject()
        self.watch_update_observable = Subject()

        self.game_info: ScenarioInfo
        self.scenario_info_objects: Dict[int, ScenarioInfoElement]

        # these dicts map from resource id to attack value
        self.sf_calc: Dict[int, float] = dict()
        self.gf_calc: Dict[int, float] = dict()
        self.missile_calc: Dict[int, float] = {1: 0.0, 142: 0.0, 27: 0.0, 38: 0.0}
        self.maneuvering_unit_calc: Dict[int, float] = dict()

        self.watch_update_observable.subscribe(
            lambda _: self._logger.info("Watch update triggered!")
        )

    @property
    def auth(self) -> RequestAuthInfo:
        return cast(RequestAuthInfo, self.base_request.dict(by_alias=False))

    @staticmethod
    async def create(auth: AnacreonApiRequest) -> AnacreonContext:
        ret = AnacreonContext(auth)
        await ret._generate_force_calculation_dict()
        ret.trait_inherits_from_trait = functools.partial(
            utils.trait_inherits_from_trait, ret.game_info.scenario_info
        )
        return ret

    @property
    def state(self) -> List[AnacreonObject]:
        return self._state

    @state.setter
    def state(self, new_state: List[AnacreonObject]) -> None:
        self._state = new_state
        self._state_dict = {
            obj.id: obj for obj in new_state if isinstance(obj, AnacreonObjectWithId)
        }
        self.any_update_observable.on_next(self._state)

    @property
    def state_dict(self) -> Dict[int, AnacreonObjectWithId]:
        return self._state_dict

    def trait_inherits_from_trait(
        self, child_trait: Union[Trait, int], parent_trait: Union[Trait, int]
    ) -> bool:
        raise NotImplementedError(
            "To use this method, you need to create your AnacreonContext using `AnacreonContext.create`"
        )

    def register_response(
        self, partial_state: List[AnacreonObject]
    ) -> List[AnacreonObject]:
        """
        Update game state based on partial state update

        :param partial_state: The partial state update obtained by calling an API endpoint
        :return: The entire game state
        """
        replaced_ids = set(
            obj.id for obj in partial_state if isinstance(obj, AnacreonObjectWithId)
        )
        new_update = next(
            (obj for obj in partial_state if isinstance(obj, UpdateObject)), None
        )
        if new_update is None:
            self._logger.info("Could not find update object inside ")

            def stringify(x: Any) -> str:
                if isinstance(x, str):
                    return x
                return repr(type(x))

            self._logger.info(list(map(stringify, partial_state)))
        else:
            self.base_request.sequence = new_update.sequence

        def obj_was_refreshed(obj: AnacreonObject) -> bool:
            if isinstance(obj, AnacreonObjectWithId):
                return obj.id in replaced_ids
            elif isinstance(obj, UpdateObject):
                return new_update is not None
            return False

        full_state = [obj for obj in self.state if not obj_was_refreshed(obj)]
        full_state.extend(partial_state)
        self.state = full_state
        self._logger.debug("Integrated partial state")
        return self.state

    async def periodically_update_objects(self, *, period: int = 60) -> None:
        """
        Coroutine that runs forever, calling getObjects every so often
        """
        while True:
            sleep: Awaitable[None] = asyncio.sleep(period)
            sleep_future = asyncio.create_task(sleep)
            try:
                partial_state = await self.client.get_objects(self.base_request)
                full_state = self.register_response(partial_state)
                self.watch_update_observable.on_next(full_state)
            except Exception as e:
                self._logger.error(
                    f"Issue doing watch update! Message: {str(e)}", exc_info=True
                )
            await sleep_future

    def __del__(self) -> None:
        self.watch_update_observable.dispose()
        self.any_update_observable.dispose()

    async def _generate_force_calculation_dict(self: AnacreonContext) -> None:
        """
        Generate the dictionaries required to calculate space and ground force of an object

        :return: None
        """
        self.game_info = await self.client.get_game_info(
            self.base_request.auth_token, self.base_request.game_id
        )
        self.scenario_info_objects = {
            obj.id: obj for obj in self.game_info.scenario_info if obj.id is not None
        }
        for item in self.game_info.scenario_info:
            if item.attack_value is not None:
                assert item.id is not None
                attack_value = float(item.attack_value)

                if (
                    item.category
                    in (
                        Category.FIXED_UNIT,
                        Category.ORBITAL_UNIT,
                        Category.MANEUVERING_UNIT,
                    )
                    and item.cargo_space is None
                ):
                    self.sf_calc[item.id] = attack_value
                    if item.category == Category.MANEUVERING_UNIT:
                        self.maneuvering_unit_calc[item.id] = attack_value
                    if item.id in self.missile_calc.keys():
                        self.missile_calc[item.id] = attack_value

                elif item.category == Category.GROUND_UNIT:
                    self.gf_calc[item.id] = attack_value

    def get_forces(self, resources: Sequence[float]) -> MilitaryForces:
        """
        Calculate the space and ground force of something

        :param resources: The resources list of the object
        :return: A tuple of the form (space force, ground force)
        """
        if len(self.sf_calc) == 0 or len(self.gf_calc) == 0:
            raise ValueError(
                "SF/GF values were not precalculated prior to calling AnacreonContext#get_forces"
            )
        sf = 0.0
        gf = 0.0
        maneuveringunit_force = 0.0
        missile_force = 0.0

        for item_id, item_qty in cast(
            List[Tuple[int, float]], flat_list_to_tuples(resources)
        ):
            if item_id in self.sf_calc.keys():  # x is the count of the resource
                sf += float(item_qty) * self.sf_calc[item_id]
                if item_id in self.maneuvering_unit_calc.keys():
                    maneuveringunit_force += (
                        float(item_qty) * self.maneuvering_unit_calc[item_id]
                    )
                if item_id in self.missile_calc.keys():
                    missile_force += float(item_qty) * self.missile_calc[item_id]
            elif item_id in self.gf_calc.keys():
                gf += float(item_qty) * self.gf_calc[item_id]
        return MilitaryForces(
            sf / 100, gf / 100, maneuveringunit_force / 100, missile_force / 100
        )

    def get_valid_improvement_list(self, world: World) -> List[ScenarioInfoElement]:
        """Returns a list of improvements that can be built"""
        valid_improvement_ids: List[ScenarioInfoElement] = []
        trait_dict = world.squashed_trait_dict
        this_world_has_trait: Callable[[int], bool] = functools.partial(
            world_has_trait, self.game_info.scenario_info, world
        )

        for improvement in self.game_info.scenario_info:
            if improvement.id is None or improvement.category is None:
                continue

            if (
                improvement.category == Category.IMPROVEMENT
                and not improvement.npe_only
                and not improvement.designation_only
                and improvement.build_time is not None
                and improvement.id not in trait_dict.keys()
                and (
                    improvement.min_tech_level is None
                    or world.tech_level >= improvement.min_tech_level
                )
            ):
                if improvement.build_upgrade:
                    # Check if we have the predecessor structure.
                    has_predecessor_structure = any(
                        this_world_has_trait(predecessor)
                        and not trait_under_construction(trait_dict, predecessor)
                        for predecessor in improvement.build_upgrade
                    )
                    if not has_predecessor_structure:
                        continue
                if improvement.build_requirements:
                    # Check we have requirements. Requirements can be any trait.
                    requirement_missing = any(
                        not this_world_has_trait(requirement_id)
                        or trait_under_construction(trait_dict, requirement_id)
                        for requirement_id in improvement.build_requirements
                    )
                    if requirement_missing:
                        continue

                if improvement.build_exclusions:
                    # Check if we are banned from doing so
                    if any(
                        this_world_has_trait(exclusion_id)
                        for exclusion_id in improvement.build_exclusions
                    ):
                        continue

                # Check if this trait would be a downgrade from an existing trait
                if any(
                    type_supercedes_type(
                        self.game_info.scenario_info, existing_trait_id, improvement.id
                    )
                    for existing_trait_id in trait_dict.keys()
                ):
                    continue

                # if this is a tech advancement structure, check if we can build it
                if improvement.role == "techAdvance":
                    if (improvement.tech_level_advance or 0) <= world.tech_level:
                        continue

                # we have not continue'd so it is ok to  build
                valid_improvement_ids.append(improvement)

        return valid_improvement_ids

    def get_obj_by_id(self, id: int) -> Optional[AnacreonObjectWithId]:
        try:
            return self.state_dict[id]
        except KeyError:
            return None

    def get_scn_info_el_name(self, res_id: int) -> str:
        return self.scenario_info_objects[res_id].name_desc or str(res_id)

    def generate_production_info(
        self, world: Union[World, int]
    ) -> Dict[int, ProductionInfo]:
        """
        Generate info that can be found in the production tab of a planet
        :param world: The planet ID or the planet object
        :param refresh: Whether or not to refresh the internal game objects cache
        :return: A list of all the things that the planet has produced, imported, exported, etc
        """

        # This is more or less exactly how the game client calculates production as well
        # I ported this from JavaScript
        if isinstance(world, int):
            maybe_world_obj = self.get_obj_by_id(world)
            if maybe_world_obj is None or not isinstance(maybe_world_obj, World):
                raise LookupError(f"Could not find world with id {world}")
            worldobj: World = maybe_world_obj
        else:
            worldobj = world
        assert isinstance(worldobj, World)

        result: DefaultDict[int, ProductionInfo] = collections.defaultdict(
            ProductionInfo
        )

        flat_list_to_4tuples = cast(
            Callable[
                [Sequence[Union[int, float, None]]],
                List[Tuple[int, float, float, Optional[float]]],
            ],
            functools.partial(utils.flat_list_to_n_tuples, 4),
        )
        flat_list_to_3tuples = cast(
            Callable[
                [Sequence[Union[int, float, None]]],
                List[Tuple[int, float, Optional[float]]],
            ],
            functools.partial(utils.flat_list_to_n_tuples, 3),
        )

        resource_id: int
        optimal: float
        actual: Optional[float]

        if isinstance(worldobj, OwnedWorld):
            # First we take into account the base consumption of the planet
            for resource_id, optimal, actual in flat_list_to_3tuples(
                worldobj.base_consumption
            ):
                entry = result[resource_id]

                entry.consumed_optimal += optimal

                if actual is None:
                    entry.consumed += optimal
                else:
                    entry.consumed += actual

        for i, trait in enumerate(worldobj.traits):
            # Next, we take into account what our structures are consuming
            if isinstance(trait, Trait):
                if trait.production_data:
                    for resource_id, optimal, actual in flat_list_to_3tuples(
                        trait.production_data
                    ):
                        entry = result[resource_id]

                        if optimal > 0.0:
                            entry.produced_optimal += optimal
                            if actual is None:
                                entry.produced += optimal
                            else:
                                entry.produced += actual
                        else:
                            entry.consumed_optimal += -optimal
                            if actual is None:
                                entry.consumed += -optimal
                            else:
                                entry.consumed += -actual

        if worldobj.trade_routes:
            # Finally, we account for trade routes
            for trade_route in worldobj.trade_routes:
                exports: Optional[List[Optional[float]]] = None
                imports: Optional[List[Optional[float]]] = None
                if trade_route.reciprocal:
                    # The data for this trade route belongs to another planet
                    partner_obj = self.get_obj_by_id(trade_route.partner_obj_id)
                    # would be sorta dumb if our trade route partner didn't actually exist
                    assert partner_obj is not None and isinstance(partner_obj, World)

                    # would also be dumb if our trade route partner didn't have any trade routes
                    assert (
                        partner_trade_routes := partner_obj.trade_route_partners
                    ) is not None

                    partner_trade_route = partner_trade_routes[worldobj.id]
                    imports = partner_trade_route.exports
                    exports = partner_trade_route.imports
                else:
                    exports = trade_route.exports
                    imports = trade_route.imports

                if exports is not None:
                    for resource_id, _, optimal, actual in flat_list_to_4tuples(
                        exports
                    ):
                        entry = result[resource_id]

                        if actual is None:
                            entry.exported += optimal
                        else:
                            entry.exported += actual

                        entry.exported_optimal += optimal

                if imports is not None:
                    for resource_id, _, optimal, actual in flat_list_to_4tuples(
                        imports
                    ):
                        entry = result[resource_id]

                        if actual is None:
                            entry.imported += optimal
                        else:
                            entry.imported += actual

                        entry.imported_optimal += optimal

                if worldobj.resources:
                    for resource_id, resource_qty in cast(
                        List[Tuple[int, float]], flat_list_to_tuples(worldobj.resources)
                    ):
                        if resource_qty > 0:
                            result[resource_id].available = resource_qty

        return {int(k): v for k, v in result.items()}

    def calculate_remaining_cargo_space(self, fleet: Union[Fleet, int]) -> float:
        if isinstance(fleet, int):
            fleet_id = fleet
            maybe_fleet = self.get_obj_by_id(fleet)
            if isinstance(maybe_fleet, Fleet):
                fleet = maybe_fleet
            else:
                raise LookupError(f"Could not find fleet with id {fleet_id}")

        fleet_resources = cast(
            List[Tuple[int, float]], utils.flat_list_to_tuples(fleet.resources)
        )

        remaining_cargo_space: float = 0
        for res_id, qty in fleet_resources:
            res_info: ScenarioInfoElement = self.scenario_info_objects[res_id]
            if res_info.cargo_space:
                remaining_cargo_space += res_info.cargo_space * qty
            elif res_info.is_cargo and res_info.mass:
                remaining_cargo_space -= res_info.mass * qty

        return remaining_cargo_space

    def get_scn_info_el_unid(self, unid: str) -> ScenarioInfoElement:
        return next(v for k, v in self.scenario_info_objects.items() if v.unid == unid)
