from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.db.base import Base


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(120), unique=True, index=True, nullable=False)
    user_id = Column(String(64), index=True, nullable=False, default="anonymous")
    session_id = Column(String(80), index=True, nullable=False, default="default")
    query = Column(Text, nullable=False, default="")

    status = Column(String(32), index=True, nullable=False, default="running")
    phase = Column(String(32), nullable=False, default="start")
    last_node = Column(String(120), nullable=False, default="")
    error = Column(Text, nullable=False, default="")

    checkpoint_count = Column(Integer, nullable=False, default=0)
    recovered = Column(Boolean, nullable=False, default=False)

    answer_length = Column(Integer, nullable=False, default=0)
    sources_count = Column(Integer, nullable=False, default=0)

    started_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    finished_at = Column(DateTime, nullable=True)


class AgentRunCheckpoint(Base):
    __tablename__ = "agent_run_checkpoints"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(120), index=True, nullable=False)
    user_id = Column(String(64), index=True, nullable=False, default="anonymous")
    session_id = Column(String(80), index=True, nullable=False, default="default")

    node_name = Column(String(120), index=True, nullable=False, default="")
    phase = Column(String(32), nullable=False, default="end")
    status = Column(String(32), index=True, nullable=False, default="running")
    error = Column(Text, nullable=False, default="")

    state_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=datetime.now, index=True)


class AgentMemoryRecord(Base):
    __tablename__ = "agent_memory_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(64), index=True, nullable=False, default="anonymous")
    session_id = Column(String(80), index=True, nullable=False, default="default")
    run_id = Column(String(120), index=True, nullable=True)

    memory_type = Column(String(32), index=True, nullable=False, default="memory")
    memory_text = Column(Text, nullable=False, default="")
    created_at = Column(DateTime, default=datetime.now, index=True)
