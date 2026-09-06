"""On/off features of the appliance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HotaiConfigEntry, HotaiDevice, HotaiHub
from .const import (
    F_AMBIENT_LIGHT,
    F_AUTO_SWING,
    F_BODY_ANTI_MOLD,
    F_HIGH_HUMIDITY_ALERT,
    F_KEY_LOCK,
    F_MUTE,
)
from .entity import HotaiEntity


@dataclass(frozen=True, kw_only=True)
class HotaiSwitchDescription(SwitchEntityDescription):
    field: str


SWITCHES: tuple[HotaiSwitchDescription, ...] = (
    HotaiSwitchDescription(key="auto_swing", field=F_AUTO_SWING, translation_key="auto_swing"),
    HotaiSwitchDescription(
        key="ambient_light", field=F_AMBIENT_LIGHT, translation_key="ambient_light", entity_category=EntityCategory.CONFIG
    ),
    HotaiSwitchDescription(
        key="body_anti_mold", field=F_BODY_ANTI_MOLD, translation_key="body_anti_mold", entity_category=EntityCategory.CONFIG
    ),
    HotaiSwitchDescription(
        key="high_humidity_alert",
        field=F_HIGH_HUMIDITY_ALERT,
        translation_key="high_humidity_alert",
        entity_category=EntityCategory.CONFIG,
    ),
    HotaiSwitchDescription(key="key_lock", field=F_KEY_LOCK, translation_key="key_lock", entity_category=EntityCategory.CONFIG),
    HotaiSwitchDescription(key="mute", field=F_MUTE, translation_key="mute", entity_category=EntityCategory.CONFIG),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HotaiConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    async_add_entities(
        HotaiSwitch(hub, dev, d) for dev in hub.devices.values() for d in SWITCHES if dev.supports(d.field)
    )


class HotaiSwitch(HotaiEntity, SwitchEntity):
    entity_description: HotaiSwitchDescription

    def __init__(self, hub: HotaiHub, device: HotaiDevice, desc: HotaiSwitchDescription) -> None:
        super().__init__(hub, device, desc.key)
        self.entity_description = desc

    @property
    def is_on(self) -> bool | None:
        v = self.device.value(self.entity_description.field)
        return None if v is None else v == 1

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_set_fields({self.entity_description.field: 1})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_set_fields({self.entity_description.field: 0})
