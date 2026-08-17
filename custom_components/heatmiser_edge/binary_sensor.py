"""On/off readings.

Which registers are boolean is a judgement about the *map*, not about Home
Assistant, so it is declared once in `const.BINARY` and never inferred here. A
register that happens to hold 0 or 1 today is not necessarily boolean; only the
ones the manual documents as an on/off legend qualify.

Register 42 (the keypad lock) is the odd one out: it is writable, but the manual
only tells us that 0 cancels the lock, not what writing the password does. It
ships here read-only, disabled by default, until hardware settles it.
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import BINARY
from .coordinator import EdgeConfigEntry, EdgeCoordinator, UnitConfig
from .entity import EdgeEntity
from .registers import Reg


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EdgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        EdgeBinarySensor(coordinator, unit, reg)
        for unit, reg in coordinator.entity_registers()
        if coordinator.platform_for(unit, reg) == "binary_sensor"
    )


class EdgeBinarySensor(EdgeEntity, BinarySensorEntity):
    def __init__(
        self, coordinator: EdgeCoordinator, unit: UnitConfig, reg: Reg
    ) -> None:
        super().__init__(coordinator, unit, reg)
        device_class = BINARY[unit.model].get(reg.number)
        if device_class is not None:
            self._attr_device_class = BinarySensorDeviceClass(device_class)

    @property
    def is_on(self) -> bool | None:
        raw = self.raw()
        return None if raw is None else bool(raw)
