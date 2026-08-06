#!/usr/bin/env python3
"""Temporal worker — connector activities + doc-request workflow."""
from __future__ import annotations

import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from app.core.config import get_settings
from app.integrations.activities.connectors import CONNECTOR_ACTIVITIES
from app.integrations.activities.doc_workflow import DOC_WORKFLOW_ACTIVITIES
from app.integrations.temporal_workflows import ConnectorActivityWorkflow
from app.workflows.doc_request_temporal import DocRequestChainWorkflow
from app.workflows.generic_temporal import GenericStartWorkflow

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    client = await Client.connect(settings.temporal_host, namespace=settings.temporal_namespace)
    worker = Worker(
        client,
        task_queue=settings.temporal_task_queue,
        workflows=[ConnectorActivityWorkflow, DocRequestChainWorkflow, GenericStartWorkflow],
        activities=[*CONNECTOR_ACTIVITIES, *DOC_WORKFLOW_ACTIVITIES],
    )
    logger.info(
        "temporal_worker_started",
        extra={
            "host": settings.temporal_host,
            "queue": settings.temporal_task_queue,
            "workflows": ["ConnectorActivityWorkflow", "DocRequestChainWorkflow", "GenericStartWorkflow"],
        },
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
