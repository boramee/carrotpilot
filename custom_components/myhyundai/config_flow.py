from __future__ import annotations

from typing import Any

from hyundai_kia_connect_api import VehicleManager
from hyundai_kia_connect_api.exceptions import APIError, AuthenticationError
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .const import (
    CONF_BRAND,
    CONF_PIN,
    CONF_REGION,
    CONF_VEHICLE_ID,
    DEFAULT_BRAND,
    DEFAULT_REGION,
    DOMAIN,
)


class MyHyundaiConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._vehicles: list[Any] = []
        self._credentials: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            manager = VehicleManager(
                region=DEFAULT_REGION,
                brand=DEFAULT_BRAND,
                username=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
                pin=user_input.get(CONF_PIN),
            )
            try:
                result = await self.hass.async_add_executor_job(manager.login)
                if result is True:
                    vehicles = list(manager.vehicles.values())
                    if not vehicles:
                        errors["base"] = "no_vehicles"
                    elif len(vehicles) == 1:
                        vehicle = vehicles[0]
                        await self.async_set_unique_id(vehicle.id)
                        self._abort_if_unique_id_configured()
                        data = {
                            **user_input,
                            CONF_VEHICLE_ID: vehicle.id,
                            CONF_REGION: DEFAULT_REGION,
                            CONF_BRAND: DEFAULT_BRAND,
                        }
                        return self.async_create_entry(
                            title=vehicle.name or "MyHyundai",
                            data=data,
                        )
                    else:
                        self._vehicles = vehicles
                        self._credentials = user_input
                        return await self.async_step_vehicle()
                else:
                    errors["base"] = "otp_required"
            except AuthenticationError:
                errors["base"] = "auth"
            except APIError:
                errors["base"] = "unknown"

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_PIN): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_vehicle(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            vehicle_id = user_input[CONF_VEHICLE_ID]
            vehicle = next(
                (item for item in self._vehicles if item.id == vehicle_id),
                None,
            )
            if vehicle is None:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(vehicle.id)
                self._abort_if_unique_id_configured()
                data = {
                    **self._credentials,
                    CONF_VEHICLE_ID: vehicle_id,
                    CONF_REGION: DEFAULT_REGION,
                    CONF_BRAND: DEFAULT_BRAND,
                }
                return self.async_create_entry(
                    title=vehicle.name or "MyHyundai",
                    data=data,
                )

        options = {
            vehicle.id: f"{vehicle.name} ({vehicle.model})"
            if vehicle.model
            else vehicle.name or vehicle.id
            for vehicle in self._vehicles
        }
        schema = vol.Schema({vol.Required(CONF_VEHICLE_ID): vol.In(options)})
        return self.async_show_form(
            step_id="vehicle",
            data_schema=schema,
            errors=errors,
        )
