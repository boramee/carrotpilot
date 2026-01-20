from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_VEHICLE_ID, DOMAIN
from .entity import MyHyundaiEntity


@dataclass(frozen=True, kw_only=True)
class MyHyundaiSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Any], Any]
    unit_fn: Callable[[Any], str | None] | None = None


SENSOR_DESCRIPTIONS: tuple[MyHyundaiSensorDescription, ...] = (
    MyHyundaiSensorDescription(
        key="odometer",
        translation_key="odometer",
        device_class=SensorDeviceClass.DISTANCE,
        value_fn=lambda vehicle: vehicle.odometer,
        unit_fn=lambda vehicle: vehicle.odometer_unit,
    ),
    MyHyundaiSensorDescription(
        key="total_driving_range",
        translation_key="total_driving_range",
        device_class=SensorDeviceClass.DISTANCE,
        value_fn=lambda vehicle: vehicle.total_driving_range,
        unit_fn=lambda vehicle: vehicle.total_driving_range_unit,
    ),
    MyHyundaiSensorDescription(
        key="fuel_level",
        translation_key="fuel_level",
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda vehicle: vehicle.fuel_level,
    ),
    MyHyundaiSensorDescription(
        key="car_battery_percentage",
        translation_key="car_battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda vehicle: vehicle.car_battery_percentage,
    ),
    MyHyundaiSensorDescription(
        key="ev_battery_percentage",
        translation_key="ev_battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda vehicle: vehicle.ev_battery_percentage,
    ),
    MyHyundaiSensorDescription(
        key="air_temperature",
        translation_key="air_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda vehicle: vehicle.air_temperature,
    ),
    MyHyundaiSensorDescription(
        key="last_updated_at",
        translation_key="last_updated",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda vehicle: vehicle.last_updated_at,
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
            MyHyundaiSensorEntity(coordinator, vehicle_id, description)
            for description in SENSOR_DESCRIPTIONS
        ]
    )


class MyHyundaiSensorEntity(MyHyundaiEntity, SensorEntity):
    entity_description: MyHyundaiSensorDescription

    def __init__(
        self,
        coordinator,
        vehicle_id: str,
        description: MyHyundaiSensorDescription,
    ) -> None:
        super().__init__(coordinator, vehicle_id)
        self.entity_description = description
        self._attr_unique_id = f"{vehicle_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        vehicle = self.coordinator.get_vehicle(self.vehicle_id)
        if vehicle is None:
            return None
        return self.entity_description.value_fn(vehicle)

    @property
    def native_unit_of_measurement(self) -> str | None:
        if self.entity_description.unit_fn:
            vehicle = self.coordinator.get_vehicle(self.vehicle_id)
            if vehicle is not None:
                return self.entity_description.unit_fn(vehicle)
        return super().native_unit_of_measurement
