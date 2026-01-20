from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from hyundai_kia_connect_api import ClimateRequestOptions, VehicleManager
from hyundai_kia_connect_api.const import ORDER_STATUS
from hyundai_kia_connect_api.exceptions import (
    APIError,
    AuthenticationError,
    AuthenticationOTPRequired,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    ACTION_STATUS_TIMEOUT,
    CONF_BRAND,
    CONF_PIN,
    CONF_REGION,
    DEFAULT_BRAND,
    DEFAULT_REGION,
    DEFAULT_REMOTE_START_CLIMATE,
    DEFAULT_REMOTE_START_DEFROST,
    DEFAULT_REMOTE_START_DURATION,
    DEFAULT_REMOTE_START_HEATING,
    DEFAULT_REMOTE_START_TEMPERATURE,
    DEFAULT_UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


class MyHyundaiCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="myhyundai",
            update_interval=DEFAULT_UPDATE_INTERVAL,
        )
        self.entry = entry
        self.manager = VehicleManager(
            region=entry.data.get(CONF_REGION, DEFAULT_REGION),
            brand=entry.data.get(CONF_BRAND, DEFAULT_BRAND),
            username=entry.data[CONF_USERNAME],
            password=entry.data[CONF_PASSWORD],
            pin=entry.data.get(CONF_PIN),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            await self.hass.async_add_executor_job(self.manager.check_and_refresh_token)
            await self.hass.async_add_executor_job(
                self.manager.update_all_vehicles_with_cached_state
            )
        except AuthenticationOTPRequired as err:
            raise UpdateFailed("OTP required to refresh token") from err
        except AuthenticationError as err:
            raise UpdateFailed("Authentication failed") from err
        except APIError as err:
            raise UpdateFailed(f"API error: {err}") from err
        return dict(self.manager.vehicles)

    def get_vehicle(self, vehicle_id: str) -> Any | None:
        if not self.data:
            return None
        return self.data.get(vehicle_id)

    async def _async_execute_action(
        self, action_name: str, func: Callable[..., str], *args: Any
    ) -> None:
        try:
            await self.hass.async_add_executor_job(self.manager.check_and_refresh_token)
            action_id = await self.hass.async_add_executor_job(func, *args)
            if action_id:
                status = await self.hass.async_add_executor_job(
                    self.manager.check_action_status,
                    args[0],
                    action_id,
                    True,
                    ACTION_STATUS_TIMEOUT,
                )
                if status != ORDER_STATUS.SUCCESS:
                    status_value = status.value if hasattr(status, "value") else status
                    raise HomeAssistantError(
                        f"{action_name} failed: {status_value}"
                    )
        except (AuthenticationError, AuthenticationOTPRequired) as err:
            raise HomeAssistantError("Authentication failed") from err
        except APIError as err:
            raise HomeAssistantError(f"{action_name} failed: {err}") from err
        finally:
            await self.async_request_refresh()

    async def async_lock(self, vehicle_id: str) -> None:
        await self._async_execute_action("lock", self.manager.lock, vehicle_id)

    async def async_unlock(self, vehicle_id: str) -> None:
        await self._async_execute_action("unlock", self.manager.unlock, vehicle_id)

    async def async_start_climate(self, vehicle_id: str) -> None:
        options = ClimateRequestOptions(
            set_temp=DEFAULT_REMOTE_START_TEMPERATURE,
            duration=DEFAULT_REMOTE_START_DURATION,
            defrost=DEFAULT_REMOTE_START_DEFROST,
            climate=DEFAULT_REMOTE_START_CLIMATE,
            heating=DEFAULT_REMOTE_START_HEATING,
        )
        await self._async_execute_action(
            "remote start",
            self.manager.start_climate,
            vehicle_id,
            options,
        )

    async def async_stop_climate(self, vehicle_id: str) -> None:
        await self._async_execute_action(
            "remote stop",
            self.manager.stop_climate,
            vehicle_id,
        )
