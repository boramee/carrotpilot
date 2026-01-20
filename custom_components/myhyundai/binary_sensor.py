from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_VEHICLE_ID, DOMAIN
from .entity import MyHyundaiEntity


@dataclass(frozen=True, kw_only=True)
class MyHyundaiBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[Any], bool | None]


BINARY_SENSOR_DESCRIPTIONS: tuple[MyHyundaiBinarySensorDescription, ...] = (
    MyHyundaiBinarySensorDescription(
        key="engine_is_running",
        translation_key="engine_running",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda vehicle: vehicle.engine_is_running,
    ),
    MyHyundaiBinarySensorDescription(
        key="hood_is_open",
        translation_key="hood_open",
        device_class=BinarySensorDeviceClass.OPENING,
        value_fn=lambda vehicle: vehicle.hood_is_open,
    ),
    MyHyundaiBinarySensorDescription(
        key="trunk_is_open",
        translation_key="trunk_open",
        device_class=BinarySensorDeviceClass.OPENING,
        value_fn=lambda vehicle: vehicle.trunk_is_open,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    vehicle_id = entry.data[CONF_VEHICLE_ID]
    async_add_entities(
        [
            MyHyundaiBinarySensorEntity(coordinator, vehicle_id, description)
            for description in BINARY_SENSOR_DESCRIPTIONS
        ]
    )


class MyHyundaiBinarySensorEntity(MyHyundaiEntity, BinarySensorEntity):
    entity_description: MyHyundaiBinarySensorDescription

    def __init__(
        self,
        coordinator,
        vehicle_id: str,
        description: MyHyundaiBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, vehicle_id)
        self.entity_description = description
        self._attr_unique_id = f"{vehicle_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        vehicle = self.coordinator.get_vehicle(self.vehicle_id)
        if vehicle is None:
            return None
        return self.entity_description.value_fn(vehicle)
