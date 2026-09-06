"""Config flow: sign in with the 和泰智慧家 account (email + password)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_EMAIL, CONF_HOST, CONF_PASSWORD, DEFAULT_HOST, DOMAIN
from .exohome import AuthError, ConnectionFailed, ExoHomeClient, ExoHomeError

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_HOST, default=DEFAULT_HOST): str,
    }
)


class HotaiConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _async_try_login(self, email: str, password: str, host: str) -> tuple[str | None, dict[str, str]]:
        client = ExoHomeClient(async_get_clientsession(self.hass), host)
        try:
            await client.login(email, password)
        except AuthError:
            return None, {"base": "invalid_auth"}
        except ConnectionFailed:
            return None, {"base": "cannot_connect"}
        except ExoHomeError:
            return None, {"base": "unknown"}
        return client.user_id or email.lower(), {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            host = (user_input.get(CONF_HOST) or DEFAULT_HOST).strip()
            uid, errors = await self._async_try_login(email, user_input[CONF_PASSWORD], host)
            if not errors and uid:
                await self.async_set_unique_id(f"{host}:{uid}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=email,
                    data={CONF_EMAIL: email, CONF_PASSWORD: user_input[CONF_PASSWORD], CONF_HOST: host},
                )
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            host = entry.data.get(CONF_HOST, DEFAULT_HOST)
            _uid, errors = await self._async_try_login(entry.data[CONF_EMAIL], user_input[CONF_PASSWORD], host)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )
