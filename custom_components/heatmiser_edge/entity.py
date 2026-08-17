"""The base every Heatmiser EDGE entity shares.

Two things live here that would otherwise be repeated six times: availability
scoped to *one thermostat* rather than the whole bus, and the °C/°F awareness
that register 21 forces on every temperature.
"""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DIAGNOSTIC, DISABLED_BY_DEFAULT
from .coordinator import EdgeCoordinator, UnitConfig, UnitData
from .decode import decode_value, encode_value
from .hub import EdgeConnectionError
from .registers import Reg


class EdgeEntity(CoordinatorEntity[EdgeCoordinator]):
    """One register on one thermostat."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: EdgeCoordinator, unit: UnitConfig, reg: Reg
    ) -> None:
        super().__init__(coordinator)
        self.unit = unit
        self.reg = reg
        self._attr_name = reg.name
        # (bus, unit id, manual register) is the stable protocol identity. The
        # entry id stands in for the bus, deliberately: a serial path is not
        # stable across reboots, and swapping a USB adapter for a TCP gateway
        # must not orphan every entity on the bus.
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{unit.unit_id}_{reg.number}"
        )
        self._attr_device_info = coordinator.device_info(unit)
        if reg.number in DIAGNOSTIC[unit.model]:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if reg.number in DISABLED_BY_DEFAULT[unit.model]:
            self._attr_entity_registry_enabled_default = False

    @property
    def unit_data(self) -> UnitData | None:
        return self.coordinator.unit_data(self.unit.unit_id)

    @property
    def available(self) -> bool:
        """Three-way: the poll succeeded, *this* thermostat answered, and the
        register was in its block.

        The middle term is the whole point. One stat off the wall must not take
        the rest of the house with it.
        """
        data = self.unit_data
        return super().available and data is not None and self.reg.number in data.words

    @property
    def fahrenheit(self) -> bool:
        data = self.unit_data
        return bool(data and data.fahrenheit)

    def raw(self) -> int | None:
        data = self.unit_data
        return None if data is None else data.get(self.reg.number)

    def decoded_value(self) -> float | int | bool | str | None:
        """Named `decoded_value`, not `value`: NumberEntity already has a
        `value` property, and shadowing it turns the state into a bound method.
        """
        return decode_value(self.reg, self.raw(), self.fahrenheit)


class EdgeControlEntity(EdgeEntity):
    """A register the user can write.

    Every write follows the same path - encode, one FC06, then a read-back of
    this thermostat alone - and it is never optimistic: the thermostat may clamp
    a value silently, so what the UI shows is always what the stat actually kept.
    """

    async def async_write(self, value: float) -> None:
        try:
            await self.coordinator.hub.async_write_register(
                self.unit.unit_id, self.reg.number, encode_value(self.reg, value)
            )
        except EdgeConnectionError as err:
            raise HomeAssistantError(
                f"Writing {self.reg.name} on {self.unit.name} failed: {err}"
            ) from err
        # Not `async_request_refresh()`: that is debounced and polls the whole
        # bus, so a control would sit on its old value for up to ten seconds.
        await self.coordinator.async_refresh_unit(self.unit.unit_id, settle=True)
