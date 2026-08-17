"""Writable registers whose complete value set the manual spells out.

Only a *complete* legend qualifies. The thermostat accepts undocumented values
without complaining, so a partial legend would offer choices we could not read
back - and a read-only register with a legend belongs in `ENUMS` as a sensor,
not here, because a select would let you write a state the stat computes for
itself.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, REG_OPERATION_MODE, SELECTS
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
        EdgeSelect(coordinator, unit, reg)
        for unit, reg in coordinator.entity_registers()
        if coordinator.platform_for(unit, reg) == "select"
    )


class EdgeSelect(EdgeControlEntity, SelectEntity):
    def __init__(
        self, coordinator: EdgeCoordinator, unit: UnitConfig, reg: Reg
    ) -> None:
        super().__init__(coordinator, unit, reg)
        self.spec = SELECTS[unit.model][reg.number]
        if self.spec.category == "config":
            self._attr_entity_category = EntityCategory.CONFIG
        if not self.spec.enabled:
            self._attr_entity_registry_enabled_default = False

    @property
    def options(self) -> list[str]:
        # The switching differential's labels are in degrees, so they follow the
        # stat's display unit even though the wire values do not change.
        labels = self.spec.labels(self.fahrenheit)
        if self.reg.number == REG_OPERATION_MODE:
            # Program mode 03 leaves the stat with no weekly program, and it
            # silently refuses the modes that depend on one. Verified on a Heat;
            # register 29 is documented identically for a Timer, so the same
            # rule is applied here - see CLAUDE.md.
            allowed = self.coordinator.allowed_operation_modes(self.unit.unit_id)
            return [labels[value] for value in allowed if value in labels]
        return list(labels.values())

    @property
    def current_option(self) -> str | None:
        raw = self.raw()
        if raw is None:
            return None
        # An undocumented value reads unknown. Reporting an invented option
        # would be rejected by Home Assistant as not in `options` anyway.
        return self.spec.labels(self.fahrenheit).get(raw)

    async def async_select_option(self, option: str) -> None:
        labels = self.spec.labels(self.fahrenheit)
        raw = next((value for value, label in labels.items() if label == option), None)
        if raw is None:
            raise ValueError(f"{option!r} is not an option for {self.reg.name}")
        if self.reg.number == REG_OPERATION_MODE and option not in self.options:
            # The stat would refuse the write and say nothing about it.
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="mode_not_available",
                translation_placeholders={
                    "mode": option,
                    "name": self.unit.name,
                    "available": ", ".join(self.options),
                },
            )
        await self.async_write(raw)
