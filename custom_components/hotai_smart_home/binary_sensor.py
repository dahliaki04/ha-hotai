"""Alarm-style flags from the appliance."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HotaiConfigEntry, HotaiDevice, HotaiHub
from .const import F_DEFROST, F_FILTER_DIRTY, F_TANK_FULL
from .entity import HotaiEntity


@dataclass(frozen=True, kw_only=True)
class HotaiBinarySensorDescription(BinarySensorEntityDescription):
    field: str


BINARY_SENSORS: tuple[HotaiBinarySensorDescription, ...] = (
    HotaiBinarySensorDescription(
        key="tank_full", field=F_TANK_FULL, translation_key="tank_full", device_class=BinarySensorDeviceClass.PROBLEM
    ),
    HotaiBinarySensorDescription(
        key="filter_dirty",
        field=F_FILTER_DIRTY,
        translation_key="filter_dirty",
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HotaiBinarySensorDescription(
        key="defrosting",
        field=F_DEFROST,
        translation_key="defrosting",
        device_class=BinarySensorDeviceClass.RUNNING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HotaiConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for dev in hub.devices.values():
        entities.extend(HotaiBinarySensor(hub, dev, d) for d in BINARY_SENSORS if dev.supports(d.field))
        entities.append(HotaiConnectivity(hub, dev))
    async_add_entities(entities)


class HotaiBinarySensor(HotaiEntity, BinarySensorEntity):
    entity_description: HotaiBinarySensorDescription

    def __init__(self, hub: HotaiHub, device: HotaiDevice, desc: HotaiBinarySensorDescription) -> None:
        super().__init__(hub, device, desc.key)
        self.entity_description = desc

    @property
    def is_on(self) -> bool | None:
        v = self.device.value(self.entity_description.field)
        return None if v is None else v != 0


class HotaiConnectivity(HotaiEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "connectivity"

    def __init__(self, hub: HotaiHub, device: HotaiDevice) -> None:
        super().__init__(hub, device, "connectivity")

    @property
    def available(self) -> bool:
        return self.hub.connected

    @property
    def is_on(self) -> bool:
        return self.device.connected
