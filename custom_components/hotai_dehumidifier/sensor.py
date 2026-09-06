"""Read-only values reported by the appliance."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HotaiConfigEntry, HotaiDevice, HotaiHub
from .const import (
    F_ACTIVE_POWER,
    F_CURRENT,
    F_ENERGY,
    F_ERROR,
    F_HUMIDITY,
    F_POWER_FACTOR,
    F_SIDE_VENT,
    F_TEMPERATURE,
    F_VOLTAGE,
)
from .entity import HotaiEntity


@dataclass(frozen=True, kw_only=True)
class HotaiSensorDescription(SensorEntityDescription):
    field: str
    value_fn: Callable[[HotaiDevice], Any] | None = None


def _int8(device: HotaiDevice, key: str) -> int | None:
    v = device.value(key)
    if v is None:
        return None
    return v - 256 if v > 127 else v


SENSORS: tuple[HotaiSensorDescription, ...] = (
    HotaiSensorDescription(
        key="temperature",
        field=F_TEMPERATURE,
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _int8(d, F_TEMPERATURE),
    ),
    HotaiSensorDescription(
        key="humidity",
        field=F_HUMIDITY,
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # Electrical readings: the information model only says "text"; units follow the TaiSEIA
    # dehumidifier services (A, V, %, W, kWh). Verify against the app before trusting scaling.
    HotaiSensorDescription(
        key="current",
        field=F_CURRENT,
        translation_key="current",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HotaiSensorDescription(
        key="voltage",
        field=F_VOLTAGE,
        translation_key="voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HotaiSensorDescription(
        key="power_factor",
        field=F_POWER_FACTOR,
        translation_key="power_factor",
        device_class=SensorDeviceClass.POWER_FACTOR,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HotaiSensorDescription(
        key="power",
        field=F_ACTIVE_POWER,
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    HotaiSensorDescription(
        key="energy",
        field=F_ENERGY,
        translation_key="energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    HotaiSensorDescription(
        key="error_code",
        field=F_ERROR,
        translation_key="error_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.raw(F_ERROR),
    ),
    HotaiSensorDescription(
        key="side_vent",
        field=F_SIDE_VENT,
        translation_key="side_vent",
        device_class=SensorDeviceClass.ENUM,
        options=["normal", "side"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: {0: "normal", 1: "side"}.get(d.value(F_SIDE_VENT) or 0),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HotaiConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    entities: list[HotaiSensor] = []
    for dev in hub.devices.values():
        entities.extend(HotaiSensor(hub, dev, desc) for desc in SENSORS if dev.supports(desc.field))
        entities.append(HotaiDeviceStateSensor(hub, dev))
    async_add_entities(entities)


class HotaiSensor(HotaiEntity, SensorEntity):
    entity_description: HotaiSensorDescription

    def __init__(self, hub: HotaiHub, device: HotaiDevice, desc: HotaiSensorDescription) -> None:
        super().__init__(hub, device, desc.key)
        self.entity_description = desc

    @property
    def native_value(self) -> Any:
        if self.entity_description.value_fn:
            return self.entity_description.value_fn(self.device)
        return self.device.value(self.entity_description.field)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.field != F_ERROR:
            return None
        texts = self.device.error_texts()
        return {"errors": texts, "description": "、".join(texts) if texts else "正常"}


class HotaiDeviceStateSensor(HotaiEntity, SensorEntity):
    """Cloud-side device state (idle/…) plus module details as attributes."""

    _attr_translation_key = "device_state"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: HotaiHub, device: HotaiDevice) -> None:
        super().__init__(hub, device, "device_state")

    @property
    def available(self) -> bool:
        return self.hub.connected

    @property
    def native_value(self) -> str | None:
        v = self.device.data.get("device_state")
        return str(v) if v is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        mod = self.device.module
        return {
            "connected": self.device.connected,
            "local_ip": mod.get("local_ip"),
            "ssid": mod.get("ssid"),
            "firmware_version": mod.get("firmware_version"),
            "esh": dict(self.device.esh),
            "fields": list(self.device.fields),
        }
