"""The thermostat itself, for EDGE Heat units.

One register per concept, and nothing duplicated:

- `hvac_mode` is register 32 alone - on or off. Frost is a *preset*, not an HVAC
  mode: the stat is still heating, just to a different target.
- `preset_mode` is register 33 - change over, schedule, hold, advanced, away,
  frost - which is the whole of what that register means.
- `target_temperature` reads register **7**, the live effective setpoint,
  whatever mode the stat is in. Register 34 is only the last override that was
  written and goes stale the moment a schedule period starts.
- Setting a temperature writes register **34**, "Over right and Hold Set
  temperature".

Two behaviours are the thermostat's own and are deliberately left alone:

- Writing register 34 while in Schedule mode is an override until the next
  period - exactly what someone dragging a thermostat expects - so we do *not*
  force register 33 to Hold as a side effect.
- Turning the climate off leaves register 33 untouched, so turning it back on
  resumes the preset it was in.

There is no climate entity for a Timer: it has no temperature sensor at all.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    MODE_FOR_PRESET,
    MODE_HOLD,
    OPERATION_MODES,
    MODEL_HEAT,
    PRESET_FOR_MODE,
    REG_CURRENT_SETPOINT,
    REG_HOLD_SETPOINT,
    REG_ONOFF,
    REG_OPERATION_MODE,
    REG_RELAY,
    REG_ROOM_TEMP,
    REG_SENSOR_SELECTION,
    SETPOINT_MAX_C,
    SETPOINT_MAX_F,
    SETPOINT_MIN_C,
    SETPOINT_MIN_F,
    SETPOINT_STEP_C,
    SETPOINT_STEP_F,
)
from .coordinator import EdgeConfigEntry, EdgeCoordinator, UnitConfig
from .decode import decode_optional_temperature, encode_temperature
from .hub import EdgeConnectionError

# Register 25 decides which probe the stat controls from. Selections 0 and 3
# include the built-in sensor, so register 3 is right for them; the rest need
# redirecting. Where a floor probe and an air probe are both fitted the air one
# is what a room is controlled to, with the floor acting only as a limit.
#
# The manual never says which probe register 3 itself reflects under each
# selection, so this is an open hardware-verification item (see CLAUDE.md) - and
# the fallback below means getting it wrong degrades to the built-in reading
# rather than to nothing.
_CURRENT_TEMP_REGISTER = {
    1: 5,  # remote air sensors only
    2: 4,  # remote floor only - the only probe there is
    4: 5,  # floor and remote: control to the air, limit on the floor
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EdgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        EdgeClimate(coordinator, unit) for unit in coordinator.climate_units()
    )


class EdgeClimate(CoordinatorEntity[EdgeCoordinator], ClimateEntity):
    """An EDGE Heat thermostat."""

    _attr_has_entity_name = True
    _attr_name = None  # the device name is the entity name
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT]
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: EdgeCoordinator, unit: UnitConfig) -> None:
        super().__init__(coordinator)
        self.unit = unit
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{unit.unit_id}_climate"
        )
        self._attr_device_info = coordinator.device_info(unit)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    @property
    def _words(self) -> dict[int, int] | None:
        data = self.coordinator.unit_data(self.unit.unit_id)
        return None if data is None else data.words

    @property
    def available(self) -> bool:
        words = self._words
        return super().available and words is not None and REG_ONOFF in words

    @property
    def fahrenheit(self) -> bool:
        data = self.coordinator.unit_data(self.unit.unit_id)
        return bool(data and data.fahrenheit)

    @property
    def temperature_unit(self) -> str:
        return (
            UnitOfTemperature.FAHRENHEIT
            if self.fahrenheit
            else UnitOfTemperature.CELSIUS
        )

    @property
    def min_temp(self) -> float:
        return SETPOINT_MIN_F if self.fahrenheit else SETPOINT_MIN_C

    @property
    def max_temp(self) -> float:
        return SETPOINT_MAX_F if self.fahrenheit else SETPOINT_MAX_C

    @property
    def target_temperature_step(self) -> float:
        return SETPOINT_STEP_F if self.fahrenheit else SETPOINT_STEP_C

    @property
    def current_temperature(self) -> float | None:
        words = self._words
        if words is None:
            return None
        number = _CURRENT_TEMP_REGISTER.get(words.get(REG_SENSOR_SELECTION, 0), REG_ROOM_TEMP)
        value = decode_optional_temperature(
            number, words.get(number), self.fahrenheit
        )
        if value is None and number != REG_ROOM_TEMP:
            # The selected probe is unfitted or faulty; the built-in sensor is
            # still a better answer than nothing.
            value = decode_optional_temperature(
                REG_ROOM_TEMP, words.get(REG_ROOM_TEMP), self.fahrenheit
            )
        return value

    @property
    def target_temperature(self) -> float | None:
        words = self._words
        if words is None:
            return None
        return decode_optional_temperature(
            REG_CURRENT_SETPOINT, words.get(REG_CURRENT_SETPOINT), self.fahrenheit
        )

    @property
    def hvac_mode(self) -> HVACMode | None:
        words = self._words
        if words is None or REG_ONOFF not in words:
            return None
        return HVACMode.HEAT if words[REG_ONOFF] else HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        words = self._words
        if words is None or REG_ONOFF not in words:
            return None
        if not words[REG_ONOFF]:
            return HVACAction.OFF
        relay = words.get(REG_RELAY)
        if relay is None:
            return None
        return HVACAction.HEATING if relay else HVACAction.IDLE

    @property
    def preset_mode(self) -> str | None:
        words = self._words
        if words is None:
            return None
        # An undocumented mode reads unknown, not an invented preset.
        return OPERATION_MODES[MODEL_HEAT].get(words.get(REG_OPERATION_MODE, -1))

    @property
    def preset_modes(self) -> list[str]:
        """Only the modes this stat will actually accept right now.

        Under program mode 03, "None programmable", there is no weekly program,
        and the stat silently refuses the modes that depend on one - so offering
        all six would be offering three controls that do nothing. See
        `EdgeCoordinator.allowed_operation_modes`, which also keeps the current
        mode on the list whatever it is, so the reading stays honest.
        """
        allowed = self.coordinator.allowed_operation_modes(self.unit.unit_id)
        return [PRESET_FOR_MODE[mode] for mode in allowed if mode in PRESET_FOR_MODE]

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    async def _write(self, register: int, value: int) -> None:
        try:
            await self.coordinator.hub.async_write_register(
                self.unit.unit_id, register, value
            )
        except EdgeConnectionError as err:
            raise HomeAssistantError(
                f"Writing to {self.unit.name} failed: {err}"
            ) from err
        # Never optimistic: the thermostat may clamp what we sent, and the
        # read-back is how the UI ends up showing what it actually kept. This
        # stat alone, and undebounced - dragging a setpoint is a burst of
        # writes, and `async_request_refresh()` would defer all but the first of
        # them for ten seconds, which is the card visibly snapping back.
        #
        # It matters more here than anywhere else: a set writes register 34 and
        # `target_temperature` reads register 7, so nothing at all moves in the
        # UI until the stat has been asked again.
        await self.coordinator.async_refresh_unit(self.unit.unit_id, settle=True)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        await self._write(REG_HOLD_SETPOINT, encode_temperature(temperature))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        await self._write(REG_ONOFF, 1 if hvac_mode == HVACMode.HEAT else 0)

    async def async_turn_on(self) -> None:
        await self._write(REG_ONOFF, 1)

    async def async_turn_off(self) -> None:
        await self._write(REG_ONOFF, 0)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        mode = MODE_FOR_PRESET.get(preset_mode)
        if mode is None:
            raise ValueError(f"{preset_mode!r} is not a preset of this thermostat")
        if mode not in self.coordinator.allowed_operation_modes(self.unit.unit_id):
            # The stat would refuse this write and say nothing. Better to say so.
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="mode_not_available",
                translation_placeholders={
                    "mode": preset_mode,
                    "name": self.unit.name,
                    "available": ", ".join(self.preset_modes),
                },
            )
        if mode == MODE_HOLD and not self.coordinator.hold_duration(self.unit.unit_id):
            # Hold without a duration is refused by the thermostat - hardware
            # 2026-08-13, the FC06 echo comes back holding the old mode. The
            # duration has to be written first, which is what `set_hold` is for.
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="hold_needs_a_duration",
                translation_placeholders={"name": self.unit.name},
            )
        await self._write(REG_OPERATION_MODE, mode)
