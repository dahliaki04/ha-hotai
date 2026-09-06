"""Dehumidifier entity (ESH device_id 4 / SA04 information model)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.humidifier import (
    HumidifierAction,
    HumidifierDeviceClass,
    HumidifierEntity,
    HumidifierEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HotaiConfigEntry, HotaiDevice, HotaiHub
from .const import (
    ESH_DEVICE_ID_DEHUMIDIFIER,
    F_HUMIDITY,
    F_MODE,
    F_POWER,
    F_TARGET_HUMIDITY,
    MODE_TO_VALUE,
    MODE_VALUES,
)
from .entity import HotaiEntity

FALLBACK_MIN_HUMIDITY = 30
FALLBACK_MAX_HUMIDITY = 80


def is_dehumidifier(device: HotaiDevice) -> bool:
    did = device.esh_device_id
    if did is not None:
        return did == ESH_DEVICE_ID_DEHUMIDIFIER
    model = device.model.upper()
    return model.startswith(("SA04", "RD", "RDI")) or device.supports(F_TARGET_HUMIDITY)


async def async_setup_entry(
    hass: HomeAssistant, entry: HotaiConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    async_add_entities(
        HotaiDehumidifier(hub, dev) for dev in hub.devices.values() if is_dehumidifier(dev) and dev.supports(F_POWER)
    )


class HotaiDehumidifier(HotaiEntity, HumidifierEntity):
    _attr_device_class = HumidifierDeviceClass.DEHUMIDIFIER
    _attr_translation_key = "dehumidifier"
    _attr_name = None  # the device name is the entity name

    def __init__(self, hub: HotaiHub, device: HotaiDevice) -> None:
        super().__init__(hub, device, "dehumidifier")
        self._attr_supported_features = HumidifierEntityFeature(0)
        if device.supports(F_MODE):
            self._attr_supported_features |= HumidifierEntityFeature.MODES
            _lo, _hi, allowed = device.range(F_MODE)
            values = allowed if allowed else list(MODE_VALUES)
            self._attr_available_modes = [MODE_VALUES[v] for v in values if v in MODE_VALUES]
        lo, hi, _allowed = device.range(F_TARGET_HUMIDITY)
        self._attr_min_humidity = lo if lo is not None else FALLBACK_MIN_HUMIDITY
        self._attr_max_humidity = hi if hi is not None else FALLBACK_MAX_HUMIDITY

    @property
    def is_on(self) -> bool | None:
        v = self.device.value(F_POWER)
        return None if v is None else v == 1

    @property
    def mode(self) -> str | None:
        v = self.device.value(F_MODE)
        return MODE_VALUES.get(v) if v is not None else None

    @property
    def target_humidity(self) -> int | None:
        return self.device.value(F_TARGET_HUMIDITY)

    @property
    def current_humidity(self) -> int | None:
        return self.device.value(F_HUMIDITY)

    @property
    def action(self) -> HumidifierAction | None:
        if self.is_on is None:
            return None
        if not self.is_on:
            return HumidifierAction.OFF
        if self.mode == "fan_only":
            return HumidifierAction.IDLE
        return HumidifierAction.DRYING

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"device_state": self.device.data.get("device_state"), "raw_status": dict(self.device.status)}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.async_set_fields({F_POWER: 1})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_set_fields({F_POWER: 0})

    async def async_set_humidity(self, humidity: int) -> None:
        # Karo's HT046A1 silently ignores setpoints that aren't multiples of 5 (56 -> no change, 60/50/55 ok).
        target = int(round(humidity / 5.0)) * 5
        target = max(self.min_humidity, min(self.max_humidity, target))
        fields: dict[str, int] = {F_TARGET_HUMIDITY: target}
        # A humidity setpoint only matters in 設定除濕 (target) mode; switch to it like the app does.
        if self.device.supports(F_MODE) and self.mode not in ("target", None) and "target" in (self.available_modes or []):
            fields[F_MODE] = MODE_TO_VALUE["target"]
        await self.async_set_fields(fields)

    async def async_set_mode(self, mode: str) -> None:
        if mode not in MODE_TO_VALUE:
            raise ValueError(f"unknown mode {mode}")
        fields: dict[str, int] = {F_MODE: MODE_TO_VALUE[mode]}
        if self.is_on is False:
            fields[F_POWER] = 1
        await self.async_set_fields(fields)
