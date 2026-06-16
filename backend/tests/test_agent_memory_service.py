from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.services.agent_memory_service import (
    archive_memory,
    extract_memory_fact,
    list_memories,
    upsert_memory,
)


class TestAgentMemoryService:
    def setup_method(self) -> None:
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

    def teardown_method(self) -> None:
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_upsert_memory_deduplicates_by_user_category_and_key(self) -> None:
        db = self.session_local()
        try:
            created = upsert_memory(
                db,
                user_id=1,
                category="allergy",
                key="花生",
                value="过敏",
            )
            updated = upsert_memory(
                db,
                user_id=1,
                category="allergy",
                key="花生",
                value="严重过敏",
            )

            memories = list_memories(db, user_id=1)
        finally:
            db.close()

        assert created.id == updated.id
        assert len(memories) == 1
        assert memories[0].value == "严重过敏"

    def test_archive_memory_hides_deleted_item_from_active_query(self) -> None:
        db = self.session_local()
        try:
            memory = upsert_memory(
                db,
                user_id=1,
                category="preference",
                key="咖啡",
                value="晚上不喝",
            )
            archived = archive_memory(db, user_id=1, memory_id=memory.id)
            active_memories = list_memories(db, user_id=1)
            all_memories = list_memories(db, user_id=1, include_archived=True)
        finally:
            db.close()

        assert archived is True
        assert active_memories == []
        assert len(all_memories) == 1
        assert all_memories[0].status == "archived"

    def test_list_memories_isolated_by_user_id(self) -> None:
        db = self.session_local()
        try:
            upsert_memory(
                db,
                user_id=1,
                category="allergy",
                key="花生",
                value="过敏",
            )
            upsert_memory(
                db,
                user_id=2,
                category="allergy",
                key="牛奶",
                value="不耐受",
            )
            user_one_memories = list_memories(db, user_id=1)
            user_two_memories = list_memories(db, user_id=2)
        finally:
            db.close()

        assert [memory.key for memory in user_one_memories] == ["花生"]
        assert [memory.key for memory in user_two_memories] == ["牛奶"]

    def test_extract_memory_fact_reads_simple_remember_statement(self) -> None:
        result = extract_memory_fact("记住我花生过敏")

        assert result is not None
        assert result.category == "allergy"
        assert result.key == "花生"
        assert result.value == "过敏"
