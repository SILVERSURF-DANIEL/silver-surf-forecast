
import json
import os
from datetime import datetime, timezone

import copernicusmarine
import xarray as xr

DATASET = "cmems_mod_med_wav_anfc_4.2km_PT1H-i"

def main():
    now = datetime.now(timezone.utc)

    dataset = copernicusmarine.open_dataset(
       username=os.environ["COPERNICUSMARINE_SERVICE_USERNAME"],
        password=os.environ["COPERNICUSMARINE_SERVICE_PASSWORD"],
        dataset_id=DATASET,
        variables=["VHM0", "VMDR", "VTM01_SW1"],
        minimum_longitude=34.68,
        maximum_longitude=34.75,
        minimum_latitude=31.98,
        maximum_latitude=32.04,
        start_datetime=now.strftime("%Y-%m-%dT%H:00:00"),
        end_datetime=now.strftime("%Y-%m-%dT%H:00:00"),
    )

    point = dataset.sel(
        latitude=32.02,
        longitude=34.70,
        method="nearest"
    )

    def value(name):
        try:
            number = float(point[name].values.squeeze())
            return round(number, 2)
        except (ValueError, TypeError, KeyError):
            return None

    result = {
        "location": "Bat Yam",
        "wave_height": value("VHM0"),
        "wave_period": value("VTM01_SW1"),
        "wave_direction": value("VMDR"),
        "updated_at": now.isoformat()
    }

    os.makedirs("data", exist_ok=True)

    with open("data/forecast.json", "w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)

    print(result)

if __name__ == "__main__":
    main()
