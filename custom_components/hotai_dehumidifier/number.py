"""Numeric settings (off-timer, high-humidity threshold)."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HotaiConfigEntry, HotaiDevice, HotaiHub
from .const import F_HIGH_HUMIDITY_THRESHOLD, F_TIMER_HOURS
from .entity import HotaiEntity


@dataclass(frozen=True, kw_only=True)
class HotaiNumberDescription(NumberEntityDescription):
    field: str


NUMBERS: tuple[HotaiNumberDescription, ...] = (
    HotaiNumberDescription(
        key="timer_hours",
        field=F_TIMER_HOURS,
        translation_key="timer_hours",
        native_min_value=0,
        native_max_value=99,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.HOURS,
        mode=NumberMode.BOX,
    ),
    HotaiNumberDescription(
        key="high_humidity_threshold",
        field=F_HIGH_HUMIDITY_THRESHOLD,
        translation_key="high_humidity_threshold",
        native_min_value=0,
        native_max_value=99,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HotaiConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    async_add_entities(
        HotaiNumber(hub, dev, d) for dev in hub.devices.values() for d in NUMBERS if dev.supports(d.field)
    )


class HotaiNumber(HotaiEntity, NumberEntity):
    entity_description: HotaiNumberDescription

    def __init__(self, hub: HotaiHub, device: HotaiDevice, desc: HotaiNumberDescription) -> None:
        super().__init__(hub, device, desc.key)
        self.entity_description = desc
        lo, hi, _allowed = device.range(desc.field)
        # The timer's model range is 1..N hours with 0 meaning "off"; keep 0 reachable.
        if lo is not None and desc.field != F_TIMER_HOURS:
            self._attr_native_min_value = lo
        if hi is not None:
            self._attr_native_max_value = hi

    @property
    def native_value(self) -> float | None:
        return self.device.value(self.entity_description.field)

    async def async_set_native_value(self, value: float) -> None:
        await self.async_set_fields({self.entity_description.field: int(round(value))})
