from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.agent import router
from app.db.database import Base
from app.models.medication_model import (
    MedicationPlan,
    MedicationReminderEvent,
    MedicationTakenRecord,
)
from app.services.medication_reminder_service import (
    acknowledge_chat_reminders,
    get_pending_chat_reminders,
    sync_due_reminders,
)
from app.services.reminder_stream_service import ReminderBroker


class MedicationReminderServiceTests(unittest.TestCase):
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

    def add_plan(
        self,
        *,
        plan_id: int,
        remind_time: str,
        user_id: int = 1,
        status: str = "active",
        frequency: str = "daily",
    ) -> MedicationPlan:
        plan = MedicationPlan(
            plan_id=plan_id,
            user_id=user_id,
            drug_name="二甲双胍",
            dosage="1片",
            time_text="早餐后",
            remind_time=remind_time,
            frequency=frequency,
            status=status,
        )
        self.db.add(plan)
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def test_sync_due_reminders_creates_pending_event_for_due_plan(self) -> None:
        self.add_plan(plan_id=1, remind_time="08:00")

        created_events = sync_due_reminders(
            self.db,
            now_provider=lambda: datetime(2026, 4, 8, 8, 5),
        )

        self.assertEqual(len(created_events), 1)
        stored_events = self.db.query(MedicationReminderEvent).all()
        self.assertEqual(len(stored_events), 1)
        self.assertEqual(stored_events[0].delivery_status, "pending")
        self.assertIn("二甲双胍", stored_events[0].message_content)

    def test_sync_due_reminders_is_idempotent_for_same_plan_and_day(self) -> None:
        self.add_plan(plan_id=1, remind_time="08:00")

        sync_due_reminders(self.db, now_provider=lambda: datetime(2026, 4, 8, 8, 5))
        sync_due_reminders(self.db, now_provider=lambda: datetime(2026, 4, 8, 8, 20))

        stored_events = self.db.query(MedicationReminderEvent).all()
        self.assertEqual(len(stored_events), 1)

    def test_sync_due_reminders_skips_plan_when_taken_today(self) -> None:
        plan = self.add_plan(plan_id=1, remind_time="08:00")
        self.db.add(
            MedicationTakenRecord(
                user_id=1,
                plan_id=plan.plan_id,
                drug_name=plan.drug_name,
                dosage=plan.dosage,
                time_text=plan.time_text,
                remind_time=plan.remind_time,
                status="taken",
                created_at=datetime(2026, 4, 8, 7, 30),
            )
        )
        self.db.commit()

        created_events = sync_due_reminders(
            self.db,
            now_provider=lambda: datetime(2026, 4, 8, 8, 5),
        )

        self.assertEqual(created_events, [])
        self.assertEqual(self.db.query(MedicationReminderEvent).count(), 0)

    def test_sync_due_reminders_skips_invalid_remind_time(self) -> None:
        self.add_plan(plan_id=1, remind_time="bad-time")
        self.add_plan(plan_id=2, remind_time="24:00")

        created_events = sync_due_reminders(
            self.db,
            now_provider=lambda: datetime(2026, 4, 8, 8, 5),
        )

        self.assertEqual(created_events, [])
        self.assertEqual(self.db.query(MedicationReminderEvent).count(), 0)

    def test_sync_due_reminders_supports_weekly_frequency_on_matching_weekday(
        self,
    ) -> None:
        self.add_plan(plan_id=1, remind_time="08:00", frequency="weekly:WED,FRI")

        created_events = sync_due_reminders(
            self.db,
            now_provider=lambda: datetime(2026, 4, 8, 8, 5),
        )

        self.assertEqual(len(created_events), 1)
        stored_event = self.db.query(MedicationReminderEvent).one()
        self.assertEqual(stored_event.scheduled_at, datetime(2026, 4, 8, 8, 0))

    def test_sync_due_reminders_skips_weekly_frequency_on_non_matching_weekday(
        self,
    ) -> None:
        self.add_plan(plan_id=1, remind_time="08:00", frequency="weekly:MON,FRI")

        created_events = sync_due_reminders(
            self.db,
            now_provider=lambda: datetime(2026, 4, 8, 8, 5),
        )

        self.assertEqual(created_events, [])
        self.assertEqual(self.db.query(MedicationReminderEvent).count(), 0)

    def test_sync_due_reminders_falls_back_to_daily_for_invalid_weekly_frequency(
        self,
    ) -> None:
        self.add_plan(plan_id=1, remind_time="08:00", frequency="weekly:BAD")

        created_events = sync_due_reminders(
            self.db,
            now_provider=lambda: datetime(2026, 4, 8, 8, 5),
        )

        self.assertEqual(len(created_events), 1)
        stored_event = self.db.query(MedicationReminderEvent).one()
        self.assertEqual(stored_event.scheduled_at, datetime(2026, 4, 8, 8, 0))

    def test_sync_due_reminders_supports_interval_hours_frequency(self) -> None:
        self.add_plan(plan_id=1, remind_time="08:00", frequency="interval:8h")

        first_events = sync_due_reminders(
            self.db,
            now_provider=lambda: datetime(2026, 4, 8, 8, 5),
        )
        second_events = sync_due_reminders(
            self.db,
            now_provider=lambda: datetime(2026, 4, 8, 16, 5),
        )

        self.assertEqual(len(first_events), 1)
        self.assertEqual(len(second_events), 1)
        stored_events = (
            self.db.query(MedicationReminderEvent)
            .order_by(MedicationReminderEvent.scheduled_at.asc())
            .all()
        )
        self.assertEqual(
            [event.scheduled_at for event in stored_events],
            [
                datetime(2026, 4, 8, 8, 0),
                datetime(2026, 4, 8, 16, 0),
            ],
        )

    def test_sync_due_reminders_supports_cron_frequency(self) -> None:
        self.add_plan(plan_id=1, remind_time="08:00", frequency="cron:0 8,20 * * *")

        created_events = sync_due_reminders(
            self.db,
            now_provider=lambda: datetime(2026, 4, 8, 20, 5),
        )

        self.assertEqual(len(created_events), 1)
        stored_event = self.db.query(MedicationReminderEvent).one()
        self.assertEqual(stored_event.scheduled_at, datetime(2026, 4, 8, 20, 0))
        self.assertEqual(stored_event.scheduled_for, "20:00")

    def test_sync_due_reminders_supports_cron_exact_boundary(self) -> None:
        self.add_plan(plan_id=1, remind_time="08:00", frequency="cron:0 8,20 * * *")

        created_events = sync_due_reminders(
            self.db,
            now_provider=lambda: datetime(2026, 4, 8, 8, 0),
        )

        self.assertEqual(len(created_events), 1)
        stored_event = self.db.query(MedicationReminderEvent).one()
        self.assertEqual(stored_event.scheduled_at, datetime(2026, 4, 8, 8, 0))
        self.assertEqual(stored_event.scheduled_for, "08:00")

    def test_sync_due_reminders_does_not_backfill_stale_cron_before_first_slot(
        self,
    ) -> None:
        self.add_plan(plan_id=1, remind_time="08:00", frequency="cron:0 8,20 * * *")

        created_events = sync_due_reminders(
            self.db,
            now_provider=lambda: datetime(2026, 4, 9, 7, 55),
        )

        self.assertEqual(created_events, [])
        self.assertEqual(self.db.query(MedicationReminderEvent).count(), 0)

    def test_sync_due_reminders_uses_latest_due_interval_after_long_downtime(
        self,
    ) -> None:
        plan = self.add_plan(plan_id=1, remind_time="08:00", frequency="interval:8h")
        plan.last_reminded_at = datetime(2026, 4, 8, 8, 0)
        self.db.commit()

        created_events = sync_due_reminders(
            self.db,
            now_provider=lambda: datetime(2026, 4, 10, 10, 0),
        )

        self.assertEqual(len(created_events), 1)
        stored_event = self.db.query(MedicationReminderEvent).one()
        self.assertEqual(stored_event.scheduled_at, datetime(2026, 4, 10, 8, 0))

    def test_sync_due_reminders_supports_interval_across_midnight(self) -> None:
        self.add_plan(plan_id=1, remind_time="08:00", frequency="interval:8h")

        first_events = sync_due_reminders(
            self.db,
            now_provider=lambda: datetime(2026, 4, 8, 8, 5),
        )
        second_events = sync_due_reminders(
            self.db,
            now_provider=lambda: datetime(2026, 4, 8, 16, 5),
        )
        third_events = sync_due_reminders(
            self.db,
            now_provider=lambda: datetime(2026, 4, 9, 0, 5),
        )

        self.assertEqual(len(first_events), 1)
        self.assertEqual(len(second_events), 1)
        self.assertEqual(len(third_events), 1)
        stored_events = (
            self.db.query(MedicationReminderEvent)
            .order_by(MedicationReminderEvent.scheduled_at.asc())
            .all()
        )
        self.assertEqual(
            [event.scheduled_at for event in stored_events],
            [
                datetime(2026, 4, 8, 8, 0),
                datetime(2026, 4, 8, 16, 0),
                datetime(2026, 4, 9, 0, 0),
            ],
        )

    def test_sync_due_reminders_accepts_chinese_daily_frequency(self) -> None:
        self.add_plan(plan_id=1, remind_time="08:00", frequency="每天")

        created_events = sync_due_reminders(
            self.db,
            now_provider=lambda: datetime(2026, 4, 8, 8, 5),
        )

        self.assertEqual(len(created_events), 1)
        self.assertEqual(self.db.query(MedicationReminderEvent).count(), 1)

    def test_get_pending_chat_reminders_keeps_events_pending_before_ack(self) -> None:
        self.add_plan(plan_id=1, remind_time="08:00")
        sync_due_reminders(self.db, now_provider=lambda: datetime(2026, 4, 8, 8, 5))

        reminders = get_pending_chat_reminders(
            self.db,
            user_id=1,
            now_provider=lambda: datetime(2026, 4, 8, 8, 6),
        )

        self.assertEqual(len(reminders), 1)
        stored_event = self.db.query(MedicationReminderEvent).one()
        self.assertEqual(stored_event.delivery_status, "pending")

    def test_acknowledge_chat_reminders_marks_events_delivered(self) -> None:
        self.add_plan(plan_id=1, remind_time="08:00")
        sync_due_reminders(self.db, now_provider=lambda: datetime(2026, 4, 8, 8, 5))
        reminders = get_pending_chat_reminders(
            self.db,
            user_id=1,
            now_provider=lambda: datetime(2026, 4, 8, 8, 6),
        )

        result = acknowledge_chat_reminders(
            self.db,
            user_id=1,
            reminder_ids=[reminders[0]["reminder_id"]],
            now_provider=lambda: datetime(2026, 4, 8, 8, 7),
        )

        stored_event = self.db.query(MedicationReminderEvent).one()
        self.assertEqual(stored_event.delivery_status, "delivered")
        self.assertIsNotNone(stored_event.delivered_at)
        self.assertEqual(result["acknowledged_ids"], [reminders[0]["reminder_id"]])
        self.assertEqual(result["skipped_ids"], [])
        self.assertEqual(result["ignored_ids"], [])

    def test_acknowledge_chat_reminders_skips_taken_before_delivery(self) -> None:
        plan = self.add_plan(plan_id=1, remind_time="08:00")
        sync_due_reminders(self.db, now_provider=lambda: datetime(2026, 4, 8, 8, 5))
        reminders = get_pending_chat_reminders(
            self.db,
            user_id=1,
            now_provider=lambda: datetime(2026, 4, 8, 8, 6),
        )
        self.db.add(
            MedicationTakenRecord(
                user_id=1,
                plan_id=plan.plan_id,
                drug_name=plan.drug_name,
                dosage=plan.dosage,
                time_text=plan.time_text,
                remind_time=plan.remind_time,
                status="taken",
                created_at=datetime(2026, 4, 8, 8, 6),
            )
        )
        self.db.commit()

        result = acknowledge_chat_reminders(
            self.db,
            user_id=1,
            reminder_ids=[reminders[0]["reminder_id"]],
            now_provider=lambda: datetime(2026, 4, 8, 8, 7),
        )

        stored_event = self.db.query(MedicationReminderEvent).one()
        self.assertEqual(stored_event.delivery_status, "skipped")
        self.assertEqual(result["acknowledged_ids"], [])
        self.assertEqual(result["skipped_ids"], [reminders[0]["reminder_id"]])
        self.assertEqual(result["ignored_ids"], [])

    def test_acknowledge_chat_reminders_reports_ignored_ids(self) -> None:
        self.add_plan(plan_id=1, remind_time="08:00")
        sync_due_reminders(self.db, now_provider=lambda: datetime(2026, 4, 8, 8, 5))
        reminders = get_pending_chat_reminders(
            self.db,
            user_id=1,
            now_provider=lambda: datetime(2026, 4, 8, 8, 6),
        )

        result = acknowledge_chat_reminders(
            self.db,
            user_id=1,
            reminder_ids=[reminders[0]["reminder_id"], 999],
            now_provider=lambda: datetime(2026, 4, 8, 8, 7),
        )

        self.assertEqual(result["acknowledged_ids"], [reminders[0]["reminder_id"]])
        self.assertEqual(result["ignored_ids"], [999])

    def test_interval_reminder_is_not_hidden_by_earlier_taken_record(self) -> None:
        plan = self.add_plan(plan_id=1, remind_time="08:00", frequency="interval:8h")
        sync_due_reminders(self.db, now_provider=lambda: datetime(2026, 4, 8, 8, 5))
        self.db.add(
            MedicationTakenRecord(
                user_id=1,
                plan_id=plan.plan_id,
                drug_name=plan.drug_name,
                dosage=plan.dosage,
                time_text=plan.time_text,
                remind_time=plan.remind_time,
                status="taken",
                created_at=datetime(2026, 4, 8, 9, 0),
            )
        )
        self.db.commit()
        sync_due_reminders(self.db, now_provider=lambda: datetime(2026, 4, 8, 16, 5))

        reminders = get_pending_chat_reminders(
            self.db,
            user_id=1,
            now_provider=lambda: datetime(2026, 4, 8, 16, 6),
        )
        result = acknowledge_chat_reminders(
            self.db,
            user_id=1,
            reminder_ids=[reminder["reminder_id"] for reminder in reminders],
            now_provider=lambda: datetime(2026, 4, 8, 16, 7),
        )

        self.assertEqual([reminder["reminder_id"] for reminder in reminders], [1, 2])
        self.assertEqual(result["acknowledged_ids"], [1, 2])
        self.assertEqual(result["skipped_ids"], [])

    def test_pending_reminder_before_midnight_can_be_acked_after_midnight(self) -> None:
        self.add_plan(plan_id=1, remind_time="23:55", frequency="daily")
        sync_due_reminders(self.db, now_provider=lambda: datetime(2026, 4, 8, 23, 56))

        reminders = get_pending_chat_reminders(
            self.db,
            user_id=1,
            now_provider=lambda: datetime(2026, 4, 9, 0, 1),
        )
        result = acknowledge_chat_reminders(
            self.db,
            user_id=1,
            reminder_ids=[reminder["reminder_id"] for reminder in reminders],
            now_provider=lambda: datetime(2026, 4, 9, 0, 2),
        )

        self.assertEqual([reminder["reminder_id"] for reminder in reminders], [1])
        self.assertEqual(result["acknowledged_ids"], [1])
        self.assertEqual(
            self.db.query(MedicationReminderEvent).one().delivery_status, "delivered"
        )


class ReminderBrokerTests(unittest.TestCase):
    def test_publish_delivers_only_to_matching_user_queue(self) -> None:
        broker = ReminderBroker()
        user_one_queue = broker.subscribe(user_id=1)
        user_two_queue = broker.subscribe(user_id=2)

        broker.publish(
            [
                {"user_id": 1, "reminder_id": 1, "content": "用户1提醒"},
                {"user_id": 2, "reminder_id": 2, "content": "用户2提醒"},
            ]
        )

        self.assertEqual(user_one_queue.get_nowait()["reminder_id"], 1)
        self.assertEqual(user_two_queue.get_nowait()["reminder_id"], 2)
        self.assertTrue(user_one_queue.empty())
        self.assertTrue(user_two_queue.empty())


class MedicationReminderApiTests(unittest.TestCase):
    def build_client(self) -> tuple[TestClient, sessionmaker, object]:
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        session_local = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=engine,
        )
        Base.metadata.create_all(bind=engine)

        app = FastAPI()
        app.include_router(router, prefix="/api/agent")

        from app.api import agent as agent_module

        def override_get_db():
            db = session_local()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[agent_module.get_db] = override_get_db
        app.dependency_overrides[agent_module.get_db_session_factory] = (
            lambda: session_local
        )
        return TestClient(app), session_local, engine

    def test_reminders_route_returns_empty_list_when_no_events_exist(self) -> None:
        client, _, engine = self.build_client()
        try:
            response = client.get("/api/agent/reminders")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"code": 0, "data": []})
        finally:
            engine.dispose()

    def test_reminders_route_returns_pending_reminders_without_consuming_them(
        self,
    ) -> None:
        client, session_local, engine = self.build_client()
        db = session_local()
        try:
            db.add(
                MedicationPlan(
                    plan_id=1,
                    user_id=1,
                    drug_name="二甲双胍",
                    dosage="1片",
                    time_text="早餐后",
                    remind_time="08:00",
                    frequency="daily",
                    status="active",
                )
            )
            db.add(
                MedicationReminderEvent(
                    user_id=1,
                    plan_id=1,
                    reminder_date=datetime.now().date().isoformat(),
                    scheduled_for="08:00",
                    message_content="💊 用药提醒",
                    delivery_status="pending",
                )
            )
            db.commit()

            response = client.get("/api/agent/reminders")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["code"], 0)
            self.assertEqual(len(payload["data"]), 1)
            self.assertEqual(payload["data"][0]["content"], "💊 用药提醒")
            stored_event = db.query(MedicationReminderEvent).one()
            self.assertEqual(stored_event.delivery_status, "pending")
        finally:
            db.close()
            engine.dispose()

    def test_reminders_route_returns_only_demo_user_reminders(self) -> None:
        client, session_local, engine = self.build_client()
        db = session_local()
        today_text = datetime.now().date().isoformat()
        try:
            db.add_all(
                [
                    MedicationPlan(
                        plan_id=1,
                        user_id=1,
                        drug_name="二甲双胍",
                        dosage="1片",
                        time_text="早餐后",
                        remind_time="08:00",
                        frequency="daily",
                        status="active",
                    ),
                    MedicationPlan(
                        plan_id=2,
                        user_id=2,
                        drug_name="阿卡波糖",
                        dosage="1片",
                        time_text="晚餐后",
                        remind_time="18:00",
                        frequency="daily",
                        status="active",
                    ),
                    MedicationReminderEvent(
                        reminder_id=1,
                        user_id=1,
                        plan_id=1,
                        reminder_date=today_text,
                        scheduled_for="08:00",
                        message_content="💊 用户1提醒",
                        delivery_status="pending",
                    ),
                    MedicationReminderEvent(
                        reminder_id=2,
                        user_id=2,
                        plan_id=2,
                        reminder_date=today_text,
                        scheduled_for="18:00",
                        message_content="💊 用户2提醒",
                        delivery_status="pending",
                    ),
                ]
            )
            db.commit()

            response = client.get("/api/agent/reminders")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["code"], 0)
            self.assertEqual(len(payload["data"]), 1)
            self.assertEqual(payload["data"][0]["reminder_id"], 1)
            self.assertEqual(payload["data"][0]["content"], "💊 用户1提醒")
            stored_events = (
                db.query(MedicationReminderEvent)
                .order_by(MedicationReminderEvent.reminder_id.asc())
                .all()
            )
            self.assertEqual(
                [event.delivery_status for event in stored_events],
                ["pending", "pending"],
            )
        finally:
            db.close()
            engine.dispose()

    def test_reminders_ack_route_marks_events_delivered(self) -> None:
        client, session_local, engine = self.build_client()
        db = session_local()
        try:
            db.add(
                MedicationPlan(
                    plan_id=1,
                    user_id=1,
                    drug_name="二甲双胍",
                    dosage="1片",
                    time_text="早餐后",
                    remind_time="08:00",
                    frequency="daily",
                    status="active",
                )
            )
            db.add(
                MedicationReminderEvent(
                    reminder_id=1,
                    user_id=1,
                    plan_id=1,
                    reminder_date=datetime.now().date().isoformat(),
                    scheduled_for="08:00",
                    message_content="💊 用药提醒",
                    delivery_status="pending",
                )
            )
            db.commit()

            response = client.post(
                "/api/agent/reminders/ack", json={"reminder_ids": [1]}
            )

            self.assertEqual(response.status_code, 200)
            stored_event = db.query(MedicationReminderEvent).one()
            self.assertEqual(stored_event.delivery_status, "delivered")
            self.assertEqual(response.json()["data"]["acknowledged_ids"], [1])
        finally:
            db.close()
            engine.dispose()

    def test_reminders_ack_route_ignores_non_demo_user_reminders(self) -> None:
        client, session_local, engine = self.build_client()
        db = session_local()
        today_text = datetime.now().date().isoformat()
        try:
            db.add_all(
                [
                    MedicationPlan(
                        plan_id=1,
                        user_id=1,
                        drug_name="二甲双胍",
                        dosage="1片",
                        time_text="早餐后",
                        remind_time="08:00",
                        frequency="daily",
                        status="active",
                    ),
                    MedicationPlan(
                        plan_id=2,
                        user_id=2,
                        drug_name="阿卡波糖",
                        dosage="1片",
                        time_text="晚餐后",
                        remind_time="18:00",
                        frequency="daily",
                        status="active",
                    ),
                    MedicationReminderEvent(
                        reminder_id=1,
                        user_id=1,
                        plan_id=1,
                        reminder_date=today_text,
                        scheduled_for="08:00",
                        message_content="💊 用户1提醒",
                        delivery_status="pending",
                    ),
                    MedicationReminderEvent(
                        reminder_id=2,
                        user_id=2,
                        plan_id=2,
                        reminder_date=today_text,
                        scheduled_for="18:00",
                        message_content="💊 用户2提醒",
                        delivery_status="pending",
                    ),
                ]
            )
            db.commit()

            response = client.post(
                "/api/agent/reminders/ack", json={"reminder_ids": [2, 1]}
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()["data"]
            self.assertEqual(payload["acknowledged_ids"], [1])
            self.assertEqual(payload["ignored_ids"], [2])
            stored_events = (
                db.query(MedicationReminderEvent)
                .order_by(MedicationReminderEvent.reminder_id.asc())
                .all()
            )
            self.assertEqual(stored_events[0].delivery_status, "delivered")
            self.assertEqual(stored_events[1].delivery_status, "pending")
        finally:
            db.close()
            engine.dispose()

    def test_reminders_ack_route_validates_batch_size(self) -> None:
        client, _, engine = self.build_client()
        try:
            response = client.post(
                "/api/agent/reminders/ack", json={"reminder_ids": []}
            )
            self.assertEqual(response.status_code, 422)
        finally:
            engine.dispose()

    def test_reminders_stream_route_emits_pending_reminder_once(self) -> None:
        client, session_local, engine = self.build_client()
        db = session_local()
        scheduled_at = datetime.now().replace(second=0, microsecond=0) - timedelta(
            minutes=1
        )
        today_text = scheduled_at.date().isoformat()
        try:
            db.add(
                MedicationPlan(
                    plan_id=1,
                    user_id=1,
                    drug_name="二甲双胍",
                    dosage="1片",
                    time_text="早餐后",
                    remind_time="08:00",
                    frequency="daily",
                    status="active",
                )
            )
            db.add(
                MedicationReminderEvent(
                    reminder_id=1,
                    user_id=1,
                    plan_id=1,
                    reminder_date=today_text,
                    scheduled_for=scheduled_at.strftime("%H:%M"),
                    scheduled_at=scheduled_at,
                    message_content="💊 用药提醒",
                    delivery_status="pending",
                )
            )
            db.commit()

            response = client.get("/api/agent/reminders/stream?once=true")

            self.assertEqual(response.status_code, 200)
            self.assertIn("text/event-stream", response.headers["content-type"])
            payload_line = next(
                line for line in response.text.splitlines() if line.startswith("data: ")
            )
            payload = json.loads(payload_line.removeprefix("data: "))
            self.assertEqual(payload["reminder_id"], 1)
            self.assertEqual(payload["content"], "💊 用药提醒")
        finally:
            db.close()
            engine.dispose()

    def test_reminders_stream_route_ignores_query_user_id_in_demo_mode(self) -> None:
        client, session_local, engine = self.build_client()
        db = session_local()
        scheduled_at = datetime.now().replace(second=0, microsecond=0) - timedelta(
            minutes=1
        )
        today_text = scheduled_at.date().isoformat()
        try:
            db.add_all(
                [
                    MedicationPlan(
                        plan_id=1,
                        user_id=1,
                        drug_name="二甲双胍",
                        dosage="1片",
                        time_text="早餐后",
                        remind_time="08:00",
                        frequency="daily",
                        status="active",
                    ),
                    MedicationPlan(
                        plan_id=2,
                        user_id=2,
                        drug_name="阿卡波糖",
                        dosage="1片",
                        time_text="晚餐后",
                        remind_time="18:00",
                        frequency="daily",
                        status="active",
                    ),
                    MedicationReminderEvent(
                        reminder_id=1,
                        user_id=1,
                        plan_id=1,
                        reminder_date=today_text,
                        scheduled_for=scheduled_at.strftime("%H:%M"),
                        scheduled_at=scheduled_at,
                        message_content="💊 用户1提醒",
                        delivery_status="pending",
                    ),
                    MedicationReminderEvent(
                        reminder_id=2,
                        user_id=2,
                        plan_id=2,
                        reminder_date=today_text,
                        scheduled_for=scheduled_at.strftime("%H:%M"),
                        scheduled_at=scheduled_at,
                        message_content="💊 用户2提醒",
                        delivery_status="pending",
                    ),
                ]
            )
            db.commit()

            response = client.get("/api/agent/reminders/stream?once=true&user_id=2")

            self.assertEqual(response.status_code, 200)
            payload_line = next(
                line for line in response.text.splitlines() if line.startswith("data: ")
            )
            payload = json.loads(payload_line.removeprefix("data: "))
            self.assertEqual(payload["reminder_id"], 1)
            self.assertEqual(payload["content"], "💊 用户1提醒")
            self.assertNotIn("用户2提醒", response.text)
        finally:
            db.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
