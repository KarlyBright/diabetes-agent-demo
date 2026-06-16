from __future__ import annotations

import unittest

from app.services.glp_parser_service import (
    normalize_glp_measurement,
    parse_glucose_context_payload,
    parse_glucose_measurement_payload,
)


VALID_MEASUREMENT_HEX = "170100ea07040b081e00000048c011"
VALID_CONTEXT_HEX = "02010002"


class GlpParserTests(unittest.TestCase):
    def test_parse_glucose_measurement_payload_returns_structured_record(self) -> None:
        measurement = parse_glucose_measurement_payload(bytes.fromhex(VALID_MEASUREMENT_HEX))

        self.assertEqual(measurement.sequence_number, 1)
        self.assertEqual(measurement.unit, "mol/L")
        self.assertTrue(measurement.context_follows)
        self.assertEqual(measurement.measure_time.isoformat(), "2026-04-11T08:30:00")
        self.assertAlmostEqual(measurement.glucose_value, 0.0072, places=4)
        self.assertEqual(measurement.sample_type, "capillary_whole_blood")
        self.assertEqual(measurement.sample_location, "finger")

    def test_parse_glucose_measurement_payload_rejects_truncated_payload(self) -> None:
        with self.assertRaises(ValueError):
            parse_glucose_measurement_payload(bytes.fromhex("170100ea07"))

    def test_parse_glucose_context_payload_reads_meal(self) -> None:
        context = parse_glucose_context_payload(bytes.fromhex(VALID_CONTEXT_HEX))

        self.assertEqual(context.sequence_number, 1)
        self.assertEqual(context.meal, "postprandial")

    def test_normalize_glp_measurement_maps_to_project_glucose_fields(self) -> None:
        measurement = parse_glucose_measurement_payload(bytes.fromhex(VALID_MEASUREMENT_HEX))
        context = parse_glucose_context_payload(bytes.fromhex(VALID_CONTEXT_HEX))

        normalized = normalize_glp_measurement(measurement, context=context, user_id=1)

        self.assertEqual(normalized.user_id, 1)
        self.assertEqual(normalized.source, "device")
        self.assertEqual(normalized.measure_type, "post_meal")
        self.assertEqual(normalized.measure_time, "2026-04-11T08:30:00")
        self.assertAlmostEqual(normalized.value, 7.2, places=1)


if __name__ == "__main__":
    unittest.main()
