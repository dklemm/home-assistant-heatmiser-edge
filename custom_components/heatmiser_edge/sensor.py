"""Readings.

Everything read-only that isn't boolean lands here, plus the writable registers
in `READ_ONLY_RW` whose write semantics the manual doesn't establish well enough
to trust (the keypad password, the communications id, the display unit).

A register with a documented legend becomes an ENUM sensor rather than a bare
number: "Period 2" beats "2". Undocumented values report unknown rather than an
invented option, which Home Assistant would reject as not in `options`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import MATCH_ALL, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ENUMS, OPERATION_MODES, PROGRAM_MODE_LABELS
from .coordinator import EdgeConfigEntry, EdgeCoordinator, UnitConfig
from .entity import EdgeEntity
from .registers import KIND_ENUM, KIND_TEMP, Reg
from .schedule import format_week, usable_periods

# Register 33's legend depends on the model, so it is not in ENUMS with the rest.
_MODE_REGISTERS = (9, 33)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EdgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            *(
                EdgeSensor(coordinator, unit, reg)
                for unit, reg in coordinator.entity_registers()
                if coordinator.platform_for(unit, reg) == "sensor"
            ),
            *(EdgeScheduleSensor(coordinator, unit) for unit in coordinator.units),
        ]
    )


class EdgeSensor(EdgeEntity, SensorEntity):
    """One readable register."""

    def __init__(
        self, coordinator: EdgeCoordinator, unit: UnitConfig, reg: Reg
    ) -> None:
        super().__init__(coordinator, unit, reg)
        self._labels = _labels_for(unit, reg)
        # Firmware versions, the communications id and years are labels, not
        # measurements: no unit, and no state class to build statistics from.
        self._unit: str | None = None
        if self._labels is not None:
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = list(self._labels.values())
        elif reg.kind == KIND_TEMP:
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_suggested_display_precision = 1
        elif reg.unit == "min":
            self._unit = UnitOfTime.MINUTES

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Temperatures follow the stat's own display unit (register 21).

        A property rather than an attribute set once, because a user changing
        the format on the keypad must be reflected. It changes approximately
        never in practice - and Home Assistant restarts long-term statistics
        when it does, which is why register 21 itself is not writable from here.
        """
        if self.reg.kind == KIND_TEMP:
            return (
                UnitOfTemperature.FAHRENHEIT
                if self.fahrenheit
                else UnitOfTemperature.CELSIUS
            )
        return self._unit

    @property
    def native_value(self) -> float | int | str | None:
        value = self.decoded_value()
        if self._labels is None or value is None:
            return value
        # An undocumented value reads unknown, not an invented label.
        return self._labels.get(int(value))


class EdgeScheduleSensor(CoordinatorEntity[EdgeCoordinator], SensorEntity):
    """The whole weekly program on one thermostat, carried as attributes.

    **Why one entity and not 126.** The grid is 7 days x 6 periods x 3 fields on
    a Heat. As entities that is 4032 on a full bus, and a period's time and
    temperature could never move together. As *attributes* it is one entity per
    thermostat that a markdown card, a template or a custom card can render
    however it likes, with no JavaScript needed to see the program at all.

    **Why the state is a timestamp.** A state is one scalar and must fit in 255
    characters, so it cannot be the grid. The one thing that genuinely varies is
    how long ago we looked - the program is not polled (`async_read_schedule`
    explains the bus arithmetic), so its freshness is a real question and
    "unknown" honestly means nothing has read it yet.

    **Nothing here is recorded.** The grid would otherwise be written to the
    database on every state change for values that move perhaps twice a year.
    Editing it is `heatmiser_edge.set_schedule`; attributes are read-only, and a
    template writing to one is not a thing that exists.
    """

    _attr_has_entity_name = True
    _attr_name = "Weekly program"
    _attr_icon = "mdi:calendar-clock"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({MATCH_ALL})

    def __init__(self, coordinator: EdgeCoordinator, unit: UnitConfig) -> None:
        super().__init__(coordinator)
        self.unit = unit
        # "schedule" rather than a register number, because it is not one
        # register - and an integer suffix could never collide with it.
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{unit.unit_id}_schedule"
        )
        self._attr_device_info = coordinator.device_info(unit)

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.schedule_read.get(self.unit.unit_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The grid, plus the two registers that say how to read it.

        Register 28 is how many periods a day the thermostat runs and register
        29 how many of the seven days are independent - so a card knows how many
        rows to offer and whether editing Monday means editing all five
        weekdays. Every row is reported whatever register 28 says: the extra
        ones exist and hold values.
        """
        words = self.coordinator.schedules.get(self.unit.unit_id)
        if words is None:
            return {}
        data = self.coordinator.unit_data(self.unit.unit_id)
        mode = self.coordinator.program_mode(self.unit.unit_id)
        fahrenheit = bool(data and data.fahrenheit)
        return {
            "periods": usable_periods(
                self.unit.model, self.coordinator.program_type(self.unit.unit_id)
            ),
            "program_mode": PROGRAM_MODE_LABELS.get(mode) if mode is not None else None,
            # The set temperatures are in the *thermostat's* unit, register 21,
            # not the user's. A card editing them has to label and bound them
            # correctly, and nothing else in the attributes would say which.
            "temperature_unit": (
                UnitOfTemperature.FAHRENHEIT if fahrenheit else UnitOfTemperature.CELSIUS
            ),
            "schedule": format_week(self.unit.model, words, fahrenheit),
        }


def _labels_for(unit: UnitConfig, reg: Reg) -> dict[int, str] | None:
    """The value legend for an enum register, or None if it has no legend."""
    if reg.kind != KIND_ENUM:
        return None
    if reg.number in _MODE_REGISTERS:
        return OPERATION_MODES[unit.model]
    return ENUMS[unit.model].get(reg.number)
