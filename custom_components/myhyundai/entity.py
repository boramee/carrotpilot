from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import MyHyundaiCoordinator


class MyHyundaiEntity(CoordinatorEntity[MyHyundaiCoordinator]):
    has_entity_name = True

    def __init__(self, coordinator: MyHyundaiCoordinator, vehicle_id: str) -> None:
        super().__init__(coordinator)
        self.vehicle_id = vehicle_id

    @property
    def device_info(self) -> DeviceInfo:
        vehicle = self.coordinator.get_vehicle(self.vehicle_id)
        return DeviceInfo(
            identifiers={(DOMAIN, self.vehicle_id)},
            manufacturer=MANUFACTURER,
            name=(vehicle.name if vehicle else None) or self.vehicle_id,
            model=(vehicle.model if vehicle else None) or "Vehicle",
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.get_vehicle(self.vehicle_id) is not None
