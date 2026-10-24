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
        print("Wind forecast unavailable:", error)


def add_water_temperature(result, hour):
    try:
        temperature = get_marine_dataset(
            TEMP_DATASET,
            ["thetao"],
            hour,
            hour,
        )

        if temperature is None:
            raise ValueError("Temperature dataset unavailable")

        point = get_point(temperature)

        result["water_temperature"] = get_value(
            point, "thetao"
        )

    except Exception as error:
        print("Water temperature unavailable:", error)


def add_sea_level(result, now):
    try:
        start = now - timedelta(hours=1)
        end = now + timedelta(hours=1)

        sea_level = get_marine_dataset(
            SEA_LEVEL_DATASET,
            ["zos"],
            start.strftime("%Y-%m-%dT%H:%M:%S"),
            end.strftime("%Y-%m-%dT%H:%M:%S"),
        )

        if sea_level is None:
            raise ValueError("Sea level dataset unavailable")

        point = get_point(sea_level)

        nearest_time = point.sel(
            time=now.replace(tzinfo=None),
            method="nearest",
        )

        result["sea_level"] = get_value(
            nearest_time, "zos"
        )

        result["sea_level_source"] = "Copernicus Marine"

    except Exception as error:
        print("Sea level unavailable:", error)


def add_wave_energy(result, hour):
    # Separate request so existing wave data is protected.
    try:
        energy_data = get_marine_dataset(
            WAVE_DATASET,
            ["VTM10"],
            hour,
            hour,
        )

        if energy_data is None:
            raise ValueError("Wave energy dataset unavailable")

        point = get_point(energy_data)
        energy_period = get_value(point, "VTM10")
        height = result["wave_height"]

        if (
            height is not None
            and energy_period is not None
            and height >= 0
            and energy_period > 0
        ):
            result["wave_energy"] = round(
                0.49 * height ** 2 * energy_period,
                2,
            )
            result["wave_energy_unit"] = "kW/m"

    except Exception as error:
        print("Wave energy unavailable:", error)


def main():
    now = datetime.now(timezone.utc)
    hour = now.strftime("%Y-%m-%dT%H:00:00")

    # Original working wave forecast
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
        "wave_height": get_value(point, "VHM0"),
        "wave_period": get_value(point, "VTM01_SW1"),
        "wave_direction": get_value(point, "VMDR"),
        "water_temperature": None,
        "wind_speed": None,
        "wind_direction": None,
        "sea_level": None,
        "wave_energy": None,
        "updated_at": now.isoformat(),
    }

    # Preserve all existing forecast data
    add_water_temperature(result, hour)
    add_wind(result, now)
    add_sea_level(result, now)

    # New calculated wave power
    add_wave_energy(result, hour)

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
