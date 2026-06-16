from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

MMOL_PER_MOL = 1000.0
GLUCOSE_MOLAR_MASS_G_PER_MOL = 180.15588
MMOL_PER_KG_PER_L = 1000.0 / GLUCOSE_MOLAR_MASS_G_PER_MOL * 1000.0

_SAMPLE_TYPE_MAP = {
    1: "capillary_whole_blood",
    2: "capillary_plasma",
    3: "venous_whole_blood",
    4: "venous_plasma",
    5: "arterial_whole_blood",
    6: "arterial_plasma",
    7: "undetermined_whole_blood",
    8: "undetermined_plasma",
    9: "interstitial_fluid",
    10: "control_solution",
}

_SAMPLE_LOCATION_MAP = {
    1: "finger",
    2: "alternate_site_test",
    3: "earlobe",
    4: "control_solution",
    15: "unknown",
}

_MEAL_MAP = {
    1: "preprandial",
    2: "postprandial",
    3: "fasting",
    4: "casual",
    5: "bedtime",
}


@dataclass(frozen=True)
class GlpMeasurementRecord:
    sequence_number: int
    measure_time: datetime
    time_offset_minutes: int | None
    context_follows: bool
    unit: str
    glucose_value: float | None
    sample_type: str | None
    sample_location: str | None


@dataclass(frozen=True)
class GlpContextRecord:
    sequence_number: int
    meal: str | None


@dataclass(frozen=True)
class NormalizedGlucoseMeasurement:
    user_id: int
    value: float
    measure_time: str
    measure_type: str
    source: str


def _decode_sfloat16(raw_value: int) -> float:
    mantissa = raw_value & 0x0FFF
    exponent = (raw_value >> 12) & 0x0F
    if mantissa >= 0x0800:
        mantissa -= 0x1000
    if exponent >= 0x08:
        exponent -= 0x10
    return mantissa * (10 ** exponent)


def _parse_datetime(payload: bytes, *, offset: int) -> datetime:
    if len(payload) < offset + 7:
        raise ValueError("payload does not contain a complete Base Time field")
    year = int.from_bytes(payload[offset : offset + 2], byteorder="little", signed=False)
    month = payload[offset + 2]
    day = payload[offset + 3]
    hour = payload[offset + 4]
    minute = payload[offset + 5]
    second = payload[offset + 6]
    try:
        return datetime(year, month, day, hour, minute, second)
    except ValueError as exc:
        raise ValueError("payload contains an invalid Base Time value") from exc


def parse_glucose_measurement_payload(payload: bytes) -> GlpMeasurementRecord:
    if len(payload) < 10:
        raise ValueError("glucose measurement payload is too short")

    flags = payload[0]
    sequence_number = int.from_bytes(payload[1:3], byteorder="little", signed=False)
    base_time = _parse_datetime(payload, offset=3)
    cursor = 10

    time_offset_minutes: int | None = None
    if flags & 0x01:
        if len(payload) < cursor + 2:
            raise ValueError("glucose measurement payload is missing time offset")
        time_offset_minutes = int.from_bytes(payload[cursor : cursor + 2], byteorder="little", signed=True)
        cursor += 2

    glucose_value: float | None = None
    sample_type: str | None = None
    sample_location: str | None = None
    if flags & 0x02:
        if len(payload) < cursor + 3:
            raise ValueError("glucose measurement payload is missing concentration or sample metadata")
        raw_concentration = int.from_bytes(payload[cursor : cursor + 2], byteorder="little", signed=False)
        glucose_value = _decode_sfloat16(raw_concentration)
        type_location = payload[cursor + 2]
        sample_type = _SAMPLE_TYPE_MAP.get(type_location & 0x0F, "unknown")
        sample_location = _SAMPLE_LOCATION_MAP.get((type_location >> 4) & 0x0F, "unknown")
        cursor += 3

    if flags & 0x08:
        if len(payload) < cursor + 2:
            raise ValueError("glucose measurement payload is missing sensor status annunciation")
        cursor += 2

    effective_time = base_time + timedelta(minutes=time_offset_minutes or 0)
    unit = "mol/L" if flags & 0x04 else "kg/L"
    context_follows = bool(flags & 0x10)

    return GlpMeasurementRecord(
        sequence_number=sequence_number,
        measure_time=effective_time,
        time_offset_minutes=time_offset_minutes,
        context_follows=context_follows,
        unit=unit,
        glucose_value=glucose_value,
        sample_type=sample_type,
        sample_location=sample_location,
    )


def parse_glucose_context_payload(payload: bytes) -> GlpContextRecord:
    if len(payload) < 3:
        raise ValueError("glucose context payload is too short")

    flags = payload[0]
    sequence_number = int.from_bytes(payload[1:3], byteorder="little", signed=False)
    cursor = 3

    meal: str | None = None
    if flags & 0x02:
        if len(payload) < cursor + 1:
            raise ValueError("glucose context payload is missing meal field")
        meal = _MEAL_MAP.get(payload[cursor], "unknown")
        cursor += 1

    return GlpContextRecord(sequence_number=sequence_number, meal=meal)


def _convert_to_mmol_per_l(glucose_value: float, unit: str) -> float:
    if unit == "mol/L":
        return glucose_value * MMOL_PER_MOL
    if unit == "kg/L":
        return glucose_value * MMOL_PER_KG_PER_L
    raise ValueError(f"unsupported glucose unit: {unit}")


def _infer_measure_type(measure_time: datetime, context: GlpContextRecord | None) -> str:
    if context is not None:
        if context.meal == "postprandial":
            return "post_meal"
        if context.meal in {"preprandial", "fasting"}:
            return "fasting"
        if context.meal == "bedtime":
            return "before_sleep"

    if measure_time.hour >= 21 or measure_time.hour < 2:
        return "before_sleep"
    return "fasting"


def normalize_glp_measurement(
    measurement: GlpMeasurementRecord,
    *,
    context: GlpContextRecord | None = None,
    user_id: int,
) -> NormalizedGlucoseMeasurement:
    if measurement.glucose_value is None:
        raise ValueError("glucose measurement does not contain a concentration value")
    if context is not None and context.sequence_number != measurement.sequence_number:
        raise ValueError("glucose context sequence number does not match measurement")

    value_mmol = round(_convert_to_mmol_per_l(measurement.glucose_value, measurement.unit), 1)
    return NormalizedGlucoseMeasurement(
        user_id=user_id,
        value=value_mmol,
        measure_time=measurement.measure_time.isoformat(timespec="seconds"),
        measure_type=_infer_measure_type(measurement.measure_time, context),
        source="device",
    )
