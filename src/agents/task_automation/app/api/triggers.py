"""Schedule and Kafka trigger APIs."""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.cron_triggers import fire_schedule, list_fired, list_schedules, register_schedule
from app.kafka_consumer import consume_kafka_message, list_consumed
from app.kafka_consumer_group import consumer_stats, start_kafka_consumer, stop_kafka_consumer
from app.rbac import Principal, require_scope

router = APIRouter(tags=["triggers"])
IngestAuth = Annotated[Principal, Depends(require_scope("events.ingest"))]
ReadAuth = Annotated[Principal, Depends(require_scope("vision.read"))]
WriteAuth = Annotated[Principal, Depends(require_scope("rules.write"))]


class ScheduleCreate(BaseModel):
    name: str = Field(..., min_length=1)
    cron: str = Field(..., description="Cron expression (metadata; fired via API in dev)")
    trigger_event: str
    payload_template: dict[str, Any] = Field(default_factory=dict)
    active: bool = True


class KafkaMessage(BaseModel):
    topic: str
    payload: dict[str, Any] = Field(default_factory=dict)
    offset: int | None = None
    partition: int | None = None


class FireScheduleRequest(BaseModel):
    payload_override: dict[str, Any] = Field(default_factory=dict)


@router.post("/triggers/kafka/consume", summary="Ingest Kafka message (HTTP adapter)")
def kafka_consume(body: KafkaMessage, _: IngestAuth) -> dict[str, Any]:
    return consume_kafka_message(
        topic=body.topic,
        payload=body.payload,
        offset=body.offset,
        partition=body.partition,
    )


@router.get("/triggers/kafka/consumed", summary="Recently consumed Kafka messages")
def kafka_consumed(_: ReadAuth, limit: int = 50) -> dict[str, Any]:
    return {"messages": list_consumed(limit=limit)}


@router.get("/triggers/kafka/consumer", summary="Background Kafka consumer group status")
def kafka_consumer_status(_: ReadAuth) -> dict[str, Any]:
    return consumer_stats()


@router.post("/triggers/kafka/consumer/start", summary="Start background Kafka consumer")
def kafka_consumer_start(_: IngestAuth) -> dict[str, Any]:
    return start_kafka_consumer()


@router.post("/triggers/kafka/consumer/stop", summary="Stop background Kafka consumer")
def kafka_consumer_stop(_: WriteAuth) -> dict[str, Any]:
    stop_kafka_consumer()
    return {"stopped": True}


@router.post("/triggers/schedules", summary="Register cron schedule", status_code=201)
def create_schedule(body: ScheduleCreate, _: WriteAuth) -> dict[str, Any]:
    return register_schedule(
        name=body.name,
        cron=body.cron,
        trigger_event=body.trigger_event,
        payload_template=body.payload_template,
        active=body.active,
    )


@router.get("/triggers/schedules", summary="List cron schedules")
def schedules(_: ReadAuth) -> dict[str, Any]:
    return {"schedules": list_schedules()}


@router.post("/triggers/schedules/{schedule_id}/fire", summary="Fire schedule immediately")
def fire(schedule_id: str, body: FireScheduleRequest, _: IngestAuth) -> dict[str, Any]:
    result = fire_schedule(schedule_id, payload_override=body.payload_override)
    if result.get("error") == "schedule_not_found":
        raise HTTPException(status_code=404, detail="schedule not found")
    return result


@router.get("/triggers/schedules/fired", summary="Recently fired schedules")
def fired_schedules(_: ReadAuth, limit: int = 50) -> dict[str, Any]:
    return {"fired": list_fired(limit=limit)}
