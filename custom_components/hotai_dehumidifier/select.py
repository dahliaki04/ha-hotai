"""Multi-value settings (fan speed, levels, sound).

Option keys are stable identifiers (translated in strings.json); the allowed set comes from the
device's information model when the cloud provides one, otherwise the generic SA04 0-15 levels.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HotaiConfigEntry, HotaiDevice, HotaiHub
from .const import (
    F_AIR_PURIFY_MODE,
    F_DEHUMIDIFY_LEVEL,
    F_DRY_CLOTHES_LEVEL,
    F_FAN_SPEED,
    F_SOUND,
    F_SWING_LEVEL,
    SOUND_VALUES,
)
from .entity import HotaiEntity

FAN_NAMED = {0: "auto", 1: "low", 2: "medium", 3: "high"}


@dataclass(frozen=True, kw_only=True)
class HotaiSelectDescription(SelectEntityDescription):
    field: str
    values: dict[int, str] = field(default_factory=dict)  # generic value -> option key
    values_fn: Callable[[HotaiDevice], dict[int, str] | None] | None = None  # model-aware override


def _levels(zero: str | None = None, count: int = 16) -> dict[int, str]:
    out = {i: str(i) for i in range(count)}
    if zero is not None:
        out[0] = zero
    return out


def _fan_values(device: HotaiDevice) -> dict[int, str] | None:
    """Karo's units report 4 speeds (自動/低/中/高); name them instead of numbering them."""
    _lo, _hi, allowed = device.range(F_FAN_SPEED)
    if allowed and set(allowed) <= set(FAN_NAMED):
        return {v: FAN_NAMED[v] for v in sorted(allowed)}
    return None


SELECTS: tuple[HotaiSelectDescription, ...] = (
    HotaiSelectDescription(
        key="fan_speed", field=F_FAN_SPEED, translation_key="fan_speed", values=_levels("auto"), values_fn=_fan_values
    ),
    HotaiSelectDescription(
        key="dehumidify_level", field=F_DEHUMIDIFY_LEVEL, translation_key="dehumidify_level", values=_levels()
    ),
    HotaiSelectDescription(
        key="dry_clothes_level", field=F_DRY_CLOTHES_LEVEL, translation_key="dry_clothes_level", values=_levels()
    ),
    HotaiSelectDescription(key="swing_level", field=F_SWING_LEVEL, translation_key="swing_level", values=_levels()),
    HotaiSelectDescription(
        key="air_purify_mode", field=F_AIR_PURIFY_MODE, translation_key="air_purify_mode", values=_levels("off")
    ),
    HotaiSelectDescription(
        key="sound", field=F_SOUND, translation_key="sound", values=SOUND_VALUES, entity_category=EntityCategory.CONFIG
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: HotaiConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    hub = entry.runtime_data
    async_add_entities(
        HotaiSelect(hub, dev, d) for dev in hub.devices.values() for d in SELECTS if dev.supports(d.field)
    )


class HotaiSelect(HotaiEntity, SelectEntity):
    entity_description: HotaiSelectDescription

    def __init__(self, hub: HotaiHub, device: HotaiDevice, desc: HotaiSelectDescription) -> None:
        super().__init__(hub, device, desc.key)
        self.entity_description = desc
        values = (desc.values_fn(device) if desc.values_fn else None) or dict(desc.values)
        lo, hi, allowed = device.range(desc.field)
        if allowed:
            values = {v: o for v, o in values.items() if v in allowed}
        elif lo is not None or hi is not None:
            values = {v: o for v, o in values.items() if (lo is None or v >= lo) and (hi is None or v <= hi)}
        self._values = values
        self._options = {o: v for v, o in values.items()}
        self._attr_options = list(values.values())

    @property
    def current_option(self) -> str | None:
        v = self.device.value(self.entity_description.field)
        return self._values.get(v) if v is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        labels = self.device.labels(self.entity_description.field)
        return {"labels": ", ".join(f"{v}={t}" for v, t in sorted(labels.items()))} if labels else {}

    async def async_select_option(self, option: str) -> None:
        await self.async_set_fields({self.entity_description.field: self._options[option]})
