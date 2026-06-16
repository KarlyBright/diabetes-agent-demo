from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.models.glucose_model import GlucoseRecordModel
from app.services.glucose_ingestion_service import GlucoseIngestionInput, ingest_glucose_reading


class GlucoseIngestionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_local = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=self.engine,
        )
        Base.metadata.create_all(bind=self.engine)
        self.db = self.session_local()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_ingest_glucose_reading_creates_record(self) -> None:
        result = ingest_glucose_reading(
            self.db,
            GlucoseIngestionInput(
                user_id=1,
                value=7.2,
                measure_time="2026-04-11T08:30:00",
                measure_type="post_meal",
                source="device",
            ),
        )

        self.assertTrue(result.created)
        self.assertEqual(self.db.query(GlucoseRecordModel).count(), 1)
        stored = self.db.query(GlucoseRecordModel).one()
        self.assertEqual(stored.source, "device")
        self.assertEqual(stored.measure_type, "post_meal")

    def test_ingest_glucose_reading_is_idempotent_for_same_identity(self) -> None:
        first = ingest_glucose_reading(
            self.db,
            GlucoseIngestionInput(
                user_id=1,
                value=7.2,
                measure_time="2026-04-11T08:30:00",
                measure_type="post_meal",
                source="device",
            ),
        )
        second = ingest_glucose_reading(
            self.db,
            GlucoseIngestionInput(
                user_id=1,
                value=7.2,
                measure_time="2026-04-11T08:30:00",
                measure_type="post_meal",
                source="device",
            ),
        )

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.record["id"], second.record["id"])
        self.assertEqual(self.db.query(GlucoseRecordModel).count(), 1)

    def test_ingest_glucose_reading_rejects_invalid_measure_type(self) -> None:
        with self.assertRaises(ValueError):
            ingest_glucose_reading(
                self.db,
                GlucoseIngestionInput(
                    user_id=1,
                    value=7.2,
                    measure_time="2026-04-11T08:30:00",
                    measure_type="unsupported",
                    source="device",
                ),
            )


if __name__ == "__main__":
    unittest.main()
