import json
import math
import os
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen

import copernicusmarine

WAVE_DATASET = "cmems_mod_med_wav_anfc_4.2km_PT1H-i"
TEMP_DATASET = "cmems_mod_med_phy-tem_anfc_4.2km-2D_PT1H-m"
SEA_LEVEL_DATASET = "cmems_mod_med_phy-ssh_anfc_4.2km_PT15M-i"

USERNAME = os.environ["COPERNICUSMARINE_SERVICE_USERNAME"]
PASSWORD = os.environ["COPERNICUSMARINE_SERVICE_PASSWORD"]

LATITUDE = 32.02
LONGITUDE = 34.70

# Experimental Bat Yam surf-height calibration
# Observed: ~0.30 m breaking surf
# Primary offshore swell: 0.89 m
SURF_CALIBRATION = 0.30 / 0.89


def get_value(point, variable):
    try:
        number = float(point[variable].values.squeeze())
        return round(number, 2) if math.isfinite(number) else None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def get_marine_dataset(dataset_id, variables, start, end):
    return copernicusmarine.open_dataset(
        username=USERNAME,
        password=PASSWORD,
        dataset_id=dataset_id,
        variables=variables,
        minimum_longitude=34.68,
        maximum_longitude=34.75,
        minimum_latitude=31.98,
        maximum_latitude=32.04,
        start_datetime=start,
        end_datetime=end,
    )


def get_point(dataset):
    return dataset.sel(
        latitude=LATITUDE,
        longitude=LONGITUDE,
        method="nearest",
    )


def add_wind(result, now):
    url = (
        "https://api.met.no/weatherapi/locationforecast/2.0/compact"
        "?lat=32.02&lon=34.70"
    )

    request = Request(
        url,
        headers={
            "User-Agent": (
                "SilverSurfReport/1.0 "
                "(https://silversurfboardsisrael.com)"
            ),
            "Accept": "application/json",
        },
    )

    try:
        with urlopen(request, timeout=20) as response:
            weather = json.load(response)

        timeseries = weather["properties"]["timeseries"]

        nearest = min(
            timeseries,
            key=lambda item: abs(
                (
                    datetime.fromisoformat(
                        item["time"].replace("Z", "+00:00")
                    ) - now
                ).total_seconds()
            ),
        )

        details = nearest["data"]["instant"]["details"]
        speed = float(details["wind_speed"])
        direction = float(details["wind_from_direction"])

        if math.isfinite(speed) and math.isfinite(direction):
            result["wind_speed"] = round(speed, 1)
            result["wind_direction"] = round(direction) % 360
            result["wind_source"] = "MET Norway"

    except Exception as error:
        print("Wind unavailable:", error)


def add_water_temperature(result, hour):
    try:
        dataset = get_marine_dataset(
            TEMP_DATASET, ["thetao"], hour, hour
        )

        if dataset is None:
            raise ValueError("Temperature dataset unavailable")

        result["water_temperature"] = get_value(
            get_point(dataset), "thetao"
        )

    except Exception as error:
        print("Water temperature unavailable:", error)


def add_sea_level(result, now):
    try:
        start = now - timedelta(hours=1)
        end = now + timedelta(hours=1)

        dataset = get_marine_dataset(
            SEA_LEVEL_DATASET,
            ["zos"],
            start.strftime("%Y-%m-%dT%H:%M:%S"),
            end.strftime("%Y-%m-%dT%H:%M:%S"),
        )

        if dataset is None:
            raise ValueError("Sea level dataset unavailable")

        point = get_point(dataset)
        nearest = point.sel(
            time=now.replace(tzinfo=None),
            method="nearest",
        )

        result["sea_level"] = get_value(nearest, "zos")
        result["sea_level_source"] = "Copernicus Marine"

    except Exception as error:
        print("Sea level unavailable:", error)


def add_wave_energy(result, hour):
    try:
        dataset = get_marine_dataset(
            WAVE_DATASET, ["VTM10"], hour, hour
        )

        if dataset is None:
            raise ValueError("Energy period unavailable")

        period = get_value(get_point(dataset), "VTM10")

        # Use offshore significant wave height, not surf estimate.
        height = result["wave_height_offshore"]

        if (
            height is not None
            and period is not None
            and height >= 0
            and period > 0
        ):
            result["wave_energy"] = round(
                0.49 * height ** 2 * period, 2
            )
            result["wave_energy_unit"] = "kW/m"

    except Exception as error:
        print("Wave energy unavailable:", error)


def add_surf_components(result, hour):
    variables = [
        "VHM0_SW1",
        "VMDR_SW1",
        "VTM01_SW1",
        "VHM0_SW2",
        "VMDR_SW2",
        "VTM01_SW2",
        "VHM0_WW",
        "VMDR_WW",
        "VTM01_WW",
    ]

    try:
        dataset = get_marine_dataset(
            WAVE_DATASET, variables, hour, hour
        )

        if dataset is None:
            raise ValueError("Swell data unavailable")

        point = get_point(dataset)

        mappings = {
            "primary_swell_height": "VHM0_SW1",
            "primary_swell_direction": "VMDR_SW1",
            "primary_swell_period": "VTM01_SW1",
            "secondary_swell_height": "VHM0_SW2",
            "secondary_swell_direction": "VMDR_SW2",
            "secondary_swell_period": "VTM01_SW2",
            "wind_wave_height": "VHM0_WW",
            "wind_wave_direction": "VMDR_WW",
            "wind_wave_period": "VTM01_WW",
        }

        for output_name, variable in mappings.items():
            result[output_name] = get_value(point, variable)

    except Exception as error:
        print("Swell components unavailable:", error)


def add_surf_height_estimate(result):
    swell = result["primary_swell_height"]

    # Never substitute offshore height if swell is unavailable.
    if swell is not None and swell >= 0:
        estimated_height = swell * SURF_CALIBRATION

        result["wave_height"] = round(estimated_height, 2)
        result["surf_height_cm"] = round(estimated_height * 100)
        result["surf_height_method"] = "experimental_bat_yam_v1"
        result["surf_height_status"] = "unvalidated"

    else:
        result["wave_height"] = None
        result["surf_height_cm"] = None


def main():
    now = datetime.now(timezone.utc)
    hour = now.strftime("%Y-%m-%dT%H:00:00")

    waves = get_marine_dataset(
        WAVE_DATASET,
        ["VHM0", "VMDR", "VTM01_SW1"],
        hour,
        hour,
    )

    if waves is None:
        raise ValueError("Wave forecast unavailable")

    point = get_point(waves)

    result = {
        "location": "Bat Yam",
        "wave_height": None,
        "wave_height_offshore": get_value(point, "VHM0"),
        "surf_height_cm": None,
        "wave_period": get_value(point, "VTM01_SW1"),
        "wave_direction": get_value(point, "VMDR"),
        "water_temperature": None,
        "wind_speed": None,
        "wind_direction": None,
        "sea_level": None,
        "wave_energy": None,
        "primary_swell_height": None,
        "primary_swell_direction": None,
        "primary_swell_period": None,
        "secondary_swell_height": None,
        "secondary_swell_direction": None,
        "secondary_swell_period": None,
        "wind_wave_height": None,
        "wind_wave_direction": None,
        "wind_wave_period": None,
        "updated_at": now.isoformat(),
    }

    # All existing forecast sources
    add_water_temperature(result, hour)
    add_wind(result, now)
    add_sea_level(result, now)
    add_wave_energy(result, hour)
    add_surf_components(result, hour)

    # New experimental surf-height estimate
    add_surf_height_estimate(result)

    os.makedirs("data", exist_ok=True)

    with open(
        "data/forecast.json",
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            result,
            file,
            ensure_ascii=False,
            indent=2,
        )

    print(result)


if __name__ == "__main__":
    main()
