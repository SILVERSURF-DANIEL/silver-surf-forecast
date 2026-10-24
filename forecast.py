import json
import math
import os
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen

import copernicusmarine

# SILVER SURF REPORT — BAT YAM
# Experimental breaking surf height model, version 2

WAVE_DATASET = "cmems_mod_med_wav_anfc_4.2km_PT1H-i"
TEMP_DATASET = "cmems_mod_med_phy-tem_anfc_4.2km-2D_PT1H-m"
SEA_LEVEL_DATASET = "cmems_mod_med_phy-ssh_anfc_4.2km_PT15M-i"

USERNAME = os.environ["COPERNICUSMARINE_SERVICE_USERNAME"]
PASSWORD = os.environ["COPERNICUSMARINE_SERVICE_PASSWORD"]

LATITUDE = 32.02
LONGITUDE = 34.70

# Preliminary orientation of the Bat Yam coastline.
# 270 degrees means the beach faces west.
BEACH_FACING = 270.0

# First calibration: estimated 30 cm surf from the
# 24 October 2026 observation. This is NOT validated.
BASE_SURF_FACTOR = 1.0
REFERENCE_PERIOD = 4.53
PERIOD_EXPONENT = 0.5
WIND_WAVE_FACTOR = 0.6


def get_value(point, variable):
    try:
        number = float(point[variable].values.squeeze())
        if math.isfinite(number):
            return round(number, 2)
    except (ValueError, TypeError, KeyError, AttributeError):
        pass
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

        nearest = min(
            weather["properties"]["timeseries"],
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
            raise ValueError("Temperature unavailable")

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
            raise ValueError("Sea level unavailable")

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


def add_swell_components(result, hour):
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

        for output, variable in mappings.items():
            result[output] = get_value(point, variable)

    except Exception as error:
        print("Swell components unavailable:", error)


def directional_exposure(direction):
    """
    Experimental directional exposure for a west-facing beach.
    Offshore swell directions use the FROM convention.
    """
    if direction is None:
        return 0.0

    difference = (direction - BEACH_FACING + 180) % 360 - 180
    exposure = math.cos(math.radians(difference))

    return max(0.0, exposure)


def period_adjustment(period):
    """
    Experimental adjustment for wave period.
    Longer periods get a moderate increase in weight.
    """
    if period is None or period <= 0:
        return 1.0

    factor = (period / REFERENCE_PERIOD) ** PERIOD_EXPONENT

    return max(0.65, min(1.60, factor))


def component_surf_height(height, direction, period, weight=1.0):
    if height is None or direction is None or height < 0:
        return None

    return (
        height
        * directional_exposure(direction)
        * period_adjustment(period)
        * weight
    )


def add_surf_height_estimate(result):
    """
    Combine component contributions in quadrature,
    rather than adding wave heights directly.
    This is a preliminary empirical surf estimate,
    NOT a calibrated nearshore wave model.
    """

    components = [
        (
            "primary_swell_height",
            "primary_swell_direction",
            "primary_swell_period",
            1.0,
        ),
        (
            "secondary_swell_height",
            "secondary_swell_direction",
            "secondary_swell_period",
            1.0,
        ),
        (
            "wind_wave_height",
            "wind_wave_direction",
            "wind_wave_period",
            WIND_WAVE_FACTOR,
        ),
    ]

    squared_heights = []
    valid_components = 0

    for height_key, direction_key, period_key, weight in components:
        height = result.get(height_key)
        direction = result.get(direction_key)
        period = result.get(period_key)

        contribution = component_surf_height(
            height, direction, period, weight
        )

        if contribution is not None:
            squared_heights.append(contribution ** 2)
            valid_components += 1

    if valid_components == 0:
        result["wave_height"] = None
        result["surf_height_cm"] = None
        return

    surf_height = (
        math.sqrt(sum(squared_heights)) * BASE_SURF_FACTOR
    )

    result["wave_height"] = round(surf_height, 2)
    result["surf_height_cm"] = round(surf_height * 100)

    result["surf_height_method"] = "bat_yam_directional_v2"
    result["surf_height_status"] = "experimental_unvalidated"
    result["surf_height_beach_facing"] = BEACH_FACING


def main():
    now = datetime.now(timezone.utc)
    hour = now.strftime("%Y-%m-%dT%H:00:00")

    # Original offshore wave model
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
        "surf_height_cm": None,
        "wave_height_offshore": get_value(point, "VHM0"),
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

    # Keep all currently working data sources
    add_water_temperature(result, hour)
    add_wind(result, now)
    add_sea_level(result, now)
    add_wave_energy(result, hour)
    add_swell_components(result, hour)

    # Replace displayed wave height with surf estimate
    add_surf_height_estimate(result)

    os.makedirs("data", exist_ok=True)

    with open(
        "data/forecast.json", "w", encoding="utf-8"
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
