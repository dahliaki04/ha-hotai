"""Base entity: device registry wiring + push updates over the dispatcher."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from . import HotaiDevice, HotaiHub
from .const import DOMAIN, SIGNAL_CONNECTION, SIGNAL_DEVICE_UPDATE


class HotaiEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: HotaiHub, device: HotaiDevice, key: str) -> None:
        self.hub = hub
        self.device = device
        self._key = key
        self._attr_unique_id = f"{device.sn}_{key}"
        model = device.model or "TaiSEIA appliance"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.sn)},
            manufacturer=device.brand or "HOTAI 和泰",
            model=model,
            name=device.name,
            sw_version=str(device.module.get("firmware_version") or "") or None,
            serial_number=device.sn,
            connections=_mac_connection(device),
        )

    @property
    def available(self) -> bool:
        return self.hub.connected and self.device.connected

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, f"{SIGNAL_DEVICE_UPDATE}_{self.device.sn}", self._handle_update)
        )
        self.async_on_remove(async_dispatcher_connect(self.hass, SIGNAL_CONNECTION, self._handle_update))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    async def async_set_fields(self, fields: dict[str, int]) -> None:
        await self.hub.async_set(self.device.sn, fields)


def _mac_connection(device: HotaiDevice) -> set[tuple[str, str]]:
    mac = str(device.module.get("mac_address") or "").strip().lower()
    if len(mac) == 12 and all(c in "0123456789abcdef" for c in mac):
        mac = ":".join(mac[i : i + 2] for i in range(0, 12, 2))
    if len(mac) == 17:
        return {("mac", mac)}
    return set()
