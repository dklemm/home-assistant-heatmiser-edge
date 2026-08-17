"""Writable on/off registers.

Polarity follows the *register*, not Home Assistant: `SwitchSpec.on` and `.off`
are the raw values the manual documents, so a register whose "on" is 0 still
turns on when you turn the switch on. An entity whose name and value disagree is
worse than an unusual mapping.

Some registers are only honoured in certain operation modes - the Timer's output
override is documented as working "in the Hold and Advanced mode". Those report
unavailable outside those modes, which is more honest than quietly accepting a
write the thermostat ignores.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SWITCHES
from .coordinator import EdgeConfigEntry, EdgeCoordinator, UnitConfig
from .entity import EdgeControlEntity
from .registers import Reg


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EdgeConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        EdgeSwitch(coordinator, unit, reg)
        for unit, reg in coordinator.entity_registers()
        if coordinator.platform_for(unit, reg) == "switch"
    )


class EdgeSwitch(EdgeControlEntity, SwitchEntity):
    def __init__(
        self, coordinator: EdgeCoordinator, unit: UnitConfig, reg: Reg
    ) -> None:
        super().__init__(coordinator, unit, reg)
        self.spec = SWITCHES[unit.model][reg.number]
        if self.spec.category == "config":
            self._attr_entity_category = EntityCategory.CONFIG
        if not self.spec.enabled:
            self._attr_entity_registry_enabled_default = False
        if self.spec.device_class is not None:
            self._attr_device_class = SwitchDeviceClass(self.spec.device_class)

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        if self.spec.requires_mode is None:
            return True
        return self.coordinator.operation_mode(self.unit.unit_id) in self.spec.requires_mode

    @property
    def is_on(self) -> bool | None:
        raw = self.raw()
        if raw is None:
            return None
        if raw == self.spec.on:
            return True
        # Neither documented value: the map is wrong about this register, so say
        # unknown rather than report "off" for something that is not off.
        return False if raw == self.spec.off else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_write(self.spec.on)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_write(self.spec.off)
