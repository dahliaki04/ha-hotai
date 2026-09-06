"""Hotai Dehumidifier (unofficial) — cloud-push integration for HOTAI/Karo's appliances on Exosite ExoHome."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_EMAIL,
    CONF_HOST,
    CONF_PASSWORD,
    DEFAULT_HOST,
    DOMAIN,
    REFRESH_INTERVAL_SECONDS,
    SIGNAL_CONNECTION,
    SIGNAL_DEVICE_UPDATE,
)
from .exohome import (
    EVENT_ADD_DEVICE,
    EVENT_DEL_DEVICE,
    EVENT_DEVICE_CHANGE,
    AuthError,
    ConnectionFailed,
    ExoHomeClient,
    ExoHomeError,
)
from .infomodel import ModelSpec, parse_model, pick_model

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.HUMIDIFIER,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.NUMBER,
]

type HotaiConfigEntry = ConfigEntry[HotaiHub]


@dataclass
class HotaiDevice:
    """One appliance as returned by the cloud `get` request."""

    sn: str
    data: dict[str, Any] = field(default_factory=dict)
    spec: ModelSpec | None = None  # cloud information model for this device family, if known

    @property
    def status(self) -> dict[str, Any]:
        return self.data.get("status") or {}

    @property
    def connected(self) -> bool:
        return self.data.get("connected") in (1, True)

    @property
    def fields(self) -> list[str]:
        f = self.data.get("fields")
        return list(f) if isinstance(f, list) else []

    @property
    def profile(self) -> dict[str, Any]:
        return self.data.get("profile") or {}

    @property
    def esh(self) -> dict[str, Any]:
        return self.profile.get("esh") or {}

    @property
    def module(self) -> dict[str, Any]:
        return self.profile.get("module") or {}

    @property
    def model(self) -> str:
        return str(self.esh.get("model") or "").strip()

    @property
    def brand(self) -> str:
        return str(self.esh.get("brand") or "").strip()

    @property
    def esh_device_id(self) -> int | None:
        raw = self.esh.get("device_id")
        if raw in (None, ""):
            return None
        try:
            return int(str(raw), 16) if isinstance(raw, str) else int(raw)
        except ValueError:
            return None

    @property
    def name(self) -> str:
        props = self.data.get("properties") or {}
        return str(props.get("displayName") or self.model or self.sn)

    def supports(self, key: str) -> bool:
        """The cloud lists the fields a device implements; fall back to whatever status carries."""
        fields = self.fields
        if fields:
            return key in fields
        return key in self.status

    def value(self, key: str) -> int | None:
        raw = self.status.get(key)
        if raw is None or raw == "":
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            try:
                return int(float(raw))
            except (TypeError, ValueError):
                return None

    def raw(self, key: str) -> Any:
        return self.status.get(key)

    def range(self, key: str) -> tuple[int | None, int | None, list[int] | None]:
        """(min, max, allowed values): device fields_range first, else the family information model."""
        lo, hi, allowed = self._range_from_fields_range(key)
        if (lo, hi, allowed) != (None, None, None):
            return lo, hi, allowed
        fs = self.spec.spec(key) if self.spec else None
        if fs is None:
            return None, None, None
        return fs.min, fs.max, (sorted(fs.allowed) or None)

    def labels(self, key: str) -> dict[int, str]:
        """value -> label from the information model (e.g. H0E 0:自動 1:低 2:中 3:高)."""
        fs = self.spec.spec(key) if self.spec else None
        return dict(fs.allowed) if fs else {}

    def error_texts(self) -> list[str]:
        code = self.value("H12")
        if self.spec:
            return self.spec.decode_error(code)
        return [str(code)] if code else []

    def _range_from_fields_range(self, key: str) -> tuple[int | None, int | None, list[int] | None]:
        fr = self.data.get("fields_range")
        merged: dict[str, Any] = {}
        if isinstance(fr, list):
            for item in fr:
                if isinstance(item, dict):
                    merged.update(item)
        elif isinstance(fr, dict):
            merged = fr
        spec = merged.get(key)
        if spec is None:
            return None, None, None
        if isinstance(spec, dict):
            lo, hi = spec.get("min"), spec.get("max")
            allowed = spec.get("values") if isinstance(spec.get("values"), list) else None
            return (_int(lo), _int(hi), [v for v in map(_int, allowed or []) if v is not None] or None)
        if isinstance(spec, list):
            vals = [v for v in map(_int, spec) if v is not None]
            if vals and all(isinstance(v, int) for v in vals):
                return min(vals), max(vals), vals
        return None, None, None

    def apply_changes(self, changes: dict[str, Any]) -> None:
        """Merge a `device_change` event payload."""
        if not isinstance(changes, dict):
            return
        if isinstance(changes.get("status"), dict):
            self.data.setdefault("status", {}).update(changes["status"])
        for key in ("profile", "properties"):
            if isinstance(changes.get(key), dict):
                self.data[key] = _deep_merge(self.data.get(key) or {}, changes[key])
        for key in ("connected", "device_state", "users", "fields", "fields_range", "calendar"):
            if key in changes and changes[key] is not None:
                self.data[key] = changes[key]


def _int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _deep_merge(base: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in new.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class HotaiHub:
    """Owns the cloud client and the device cache; pushes updates to entities via dispatcher."""

    def __init__(self, hass: HomeAssistant, entry: HotaiConfigEntry, client: ExoHomeClient) -> None:
        self.hass = hass
        self.entry = entry
        self.client = client
        self.devices: dict[str, HotaiDevice] = {}
        self._unsub_events: Any = None
        self._unsub_timer: Any = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self.client.connected

    async def async_setup(self) -> None:
        self.client.set_reauth_callback(self._async_reauth)
        self._unsub_events = self.client.add_listener(self._on_event)
        await self.client.connect()
        records = await self.client.get_all_devices()
        self.devices = {sn: HotaiDevice(sn, rec) for sn, rec in records.items()}
        await self._async_load_info_models()
        for dev in self.devices.values():
            _LOGGER.debug(
                "device %s model=%s family=%s fields=%s status=%s",
                dev.sn, dev.model, dev.spec.family if dev.spec else None, dev.fields, dev.status,
            )
        self._unsub_timer = async_track_time_interval(
            self.hass, self._async_periodic_refresh, timedelta(seconds=REFRESH_INTERVAL_SECONDS)
        )

    async def _async_load_info_models(self) -> None:
        """Attach the family information model (ranges, option labels, error texts) to each device."""
        try:
            models = await self.client.info_models()
        except ExoHomeError as err:
            _LOGGER.warning("info models unavailable, using generic SA04 defaults: %s", err)
            return
        cache: dict[str, ModelSpec] = {}
        for dev in self.devices.values():
            im = pick_model(models, dev.model)
            if im is None:
                continue
            fam = str(im.get("familyName") or dev.model)
            if fam not in cache:
                cache[fam] = parse_model(im)
            dev.spec = cache[fam]

    async def async_unload(self) -> None:
        if self._unsub_timer:
            self._unsub_timer()
            self._unsub_timer = None
        if self._unsub_events:
            self._unsub_events()
            self._unsub_events = None
        await self.client.disconnect()

    async def _async_reauth(self) -> str:
        data = self.entry.data
        try:
            await self.client.login(data[CONF_EMAIL], data[CONF_PASSWORD])
        except AuthError:
            self.entry.async_start_reauth(self.hass)
            raise
        return self.client.token or ""

    @callback
    def _on_event(self, event: dict[str, Any]) -> None:
        name = event.get("event")
        data = event.get("data") or {}
        if name == EVENT_DEVICE_CHANGE:
            sn = data.get("device")
            if sn in self.devices:
                self.devices[sn].apply_changes(data.get("changes") or {})
                async_dispatcher_send(self.hass, f"{SIGNAL_DEVICE_UPDATE}_{sn}")
            elif sn:
                self.hass.async_create_task(self._async_add_device(sn))
        elif name == EVENT_ADD_DEVICE:
            sn = data.get("device") if isinstance(data, dict) else data
            if sn:
                self.hass.async_create_task(self._async_add_device(str(sn)))
        elif name == EVENT_DEL_DEVICE:
            sn = data.get("device") if isinstance(data, dict) else data
            if sn in self.devices:
                self.devices[sn].data["connected"] = 0
                async_dispatcher_send(self.hass, f"{SIGNAL_DEVICE_UPDATE}_{sn}")
        elif name in ("_connected", "_disconnected"):
            async_dispatcher_send(self.hass, SIGNAL_CONNECTION)
            if name == "_connected" and self.devices:
                self.hass.async_create_task(self._async_refresh_all())

    async def _async_add_device(self, sn: str) -> None:
        try:
            rec = await self.client.get_device(sn)
        except ExoHomeError as err:
            _LOGGER.warning("could not fetch new device %s: %s", sn, err)
            return
        if sn in self.devices:
            self.devices[sn].data = rec
            async_dispatcher_send(self.hass, f"{SIGNAL_DEVICE_UPDATE}_{sn}")
            return
        self.devices[sn] = HotaiDevice(sn, rec)
        await self._async_load_info_models()
        _LOGGER.info("new HOTAI device %s (%s); reload the integration to create its entities", sn, self.devices[sn].model)

    async def _async_periodic_refresh(self, _now: Any) -> None:
        await self._async_refresh_all()

    async def _async_refresh_all(self) -> None:
        async with self._lock:
            for sn in list(self.devices):
                await self.async_refresh_device(sn)

    async def async_refresh_device(self, sn: str) -> None:
        try:
            rec = await self.client.get_device(sn)
        except ExoHomeError as err:
            _LOGGER.debug("refresh %s failed: %s", sn, err)
            return
        if sn in self.devices:
            old = self.devices[sn].data
            for key in ("properties", "owner", "role"):  # only lst_device carries these
                if key in old and key not in rec:
                    rec[key] = old[key]
            self.devices[sn].data = rec
        else:
            self.devices[sn] = HotaiDevice(sn, rec)
            await self._async_load_info_models()
        async_dispatcher_send(self.hass, f"{SIGNAL_DEVICE_UPDATE}_{sn}")

    async def async_set(self, sn: str, fields: dict[str, int]) -> None:
        """Send a `set` and apply it optimistically; the cloud confirms with a device_change."""
        await self.client.set_fields(sn, fields)
        if sn in self.devices:
            self.devices[sn].data.setdefault("status", {}).update(fields)
            async_dispatcher_send(self.hass, f"{SIGNAL_DEVICE_UPDATE}_{sn}")


async def async_setup_entry(hass: HomeAssistant, entry: HotaiConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    client = ExoHomeClient(session, entry.data.get(CONF_HOST, DEFAULT_HOST))
    try:
        await client.login(entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD])
    except AuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except ConnectionFailed as err:
        raise ConfigEntryNotReady(f"cannot reach {client.host}: {err}") from err

    hub = HotaiHub(hass, entry, client)
    try:
        await hub.async_setup()
    except AuthError as err:
        await hub.async_unload()
        raise ConfigEntryAuthFailed(str(err)) from err
    except ExoHomeError as err:
        await hub.async_unload()
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HotaiConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_unload()
    return ok
