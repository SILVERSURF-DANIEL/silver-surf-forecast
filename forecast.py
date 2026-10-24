import json
import math
import os
from datetime import datetime, timezone

import copernicusmarine

WAVE_DATASET = "cmems_mod_med_wav_anfc_4.2km_PT1H-i"
TEMP_DATASET = "cmems_mod_med_phy-tem_anfc_4.2km-2D_PT1H-m"

USERNAME = os.environ["COPERNICUSMARINE_SERVICE_USERNAME"]
PASSWORD = os.environ["COPERNICUSMARINE_SERVICE_PASSWORD"]


def get_value(point, variable):
    try:
        number = float(point[variable].values.squeeze())
        return round(number, 2) if math.isfinite(number) else None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def main():
    now = datetime.now(timezone.utc)
    hour = now.strftime("%Y-%m-%dT%H:00:00")

    # Existing working wave forecast
    waves = copernicusmarine.open_dataset(
        username=USERNAME,
        password=PASSWORD,
        dataset_id=WAVE_DATASET,
        variables=["VHM0", "VMDR", "VTM01_SW1"],
        minimum_longitude=34.68,
        maximum_longitude=34.75,
        minimum_latitude=31.98,
        maximum_latitude=32.04,
        start_datetime=hour,
        end_datetime=hour,
    )

    point = waves.sel(
        latitude=32.02,
        longitude=34.70,
        method="nearest"
    )

    result = {
        "location": "Bat Yam",
        "wave_height": get_value(point, "VHM0"),
        "wave_period": get_value(point, "VTM01_SW1"),
        "wave_direction": get_value(point, "VMDR"),
        "water_temperature": None,
        "updated_at": now.isoformat()
    }

    # Additional sea surface temperature
    # A failure here will not stop the wave forecast.
    try:
        temperature = copernicusmarine.open_dataset(
            username=USERNAME,
            password=PASSWORD,
            dataset_id=TEMP_DATASET,
            variables=["thetao"],
            minimum_longitude=34.68,
            maximum_longitude=34.75,
            minimum_latitude=31.98,
            maximum_latitude=32.04,
            start_datetime=hour,
            end_datetime=hour,
        )

        temp_point = temperature.sel(
            latitude=32.02,
            longitude=34.70,
            method="nearest"
        )

        result["water_temperature"] = get_value(
            temp_point, "thetao"
        )

    except Exception as error:
        print("Water temperature unavailable:", error)

    os.makedirs("data", exist_ok=True)

    with open("data/forecast.json", "w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)

    print(result)


if __name__ == "__main__":
    main()
