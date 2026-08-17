"""Writable numeric settings.

Limits come from `const.NUMBERS`, transcribed from the manual, in both display
units - a °F stat stores 45-63 in its frost register where a °C one stores 7-17,
so a single set of limits would let the user write a value the stat then clamps.

Never widen a range without checking the manual: the thermostat accepts an
out-of-range write silently and just keeps something else.
"""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import NUMBERS, TEMPERATURE_DELTA
from .coordinator import EdgeConfigEntry, EdgeCoordinator, UnitConfig
from .entity import EdgeControlEntity
from .registers import KIND_TEMP, Reg


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EdgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        EdgeNumber(coordinator, unit, reg)
        for unit, reg in coordinator.entity_registers()
        if coordinator.platform_for(unit, reg) == "number"
    )


class EdgeNumber(EdgeControlEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(
        self, coordinator: EdgeCoordinator, unit: UnitConfig, reg: Reg
    ) -> None:
        super().__init__(coordinator, unit, reg)
        self.spec = NUMBERS[unit.model][reg.number]
        if self.spec.category == "config":
            self._attr_entity_category = EntityCategory.CONFIG
        if not self.spec.enabled:
            self._attr_entity_registry_enabled_default = False
        self._unit: str | None = None
        if reg.kind == KIND_TEMP and reg.number not in TEMPERATURE_DELTA:
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
        elif self.spec.unit == "min":
            self._unit = UnitOfTime.MINUTES

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.reg.kind == KIND_TEMP:
            return (
                UnitOfTemperature.FAHRENHEIT
                if self.fahrenheit
                else UnitOfTemperature.CELSIUS
            )
        return self._unit

    @property
    def native_min_value(self) -> float:
        return self.spec.limits(self.fahrenheit)[0]

    @property
    def native_max_value(self) -> float:
        return self.spec.limits(self.fahrenheit)[1]

    @property
    def native_step(self) -> float:
        return self.spec.limits(self.fahrenheit)[2]

    @property
    def native_value(self) -> float | None:
        value = self.decoded_value()
        return None if value is None else float(value)

    async def async_set_native_value(self, value: float) -> None:
        await self.async_write(value)
