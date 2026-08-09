# Engine Testing Results

Generated: 2026-08-09T12:29:13Z

## Root (gateway + integration)
.......                                                                  [100%]
=============================== warnings summary ===============================
.venv/lib/python3.13/site-packages/fastapi/testclient.py:1
  /Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/.venv/lib/python3.13/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
7 passed, 1 warning in 0.79s
## Follow-up Sequences (FUS)
warning: `VIRTUAL_ENV=/Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
.........................................                                [100%]
=============================== warnings summary ===============================
.venv/lib/python3.13/site-packages/fastapi/testclient.py:1
  /Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/src/agents/follow_up_sequences/.venv/lib/python3.13/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
41 passed, 1 warning in 0.67s
## Feedback Loop (FLS)
warning: `VIRTUAL_ENV=/Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
......................                                                   [100%]
=============================== warnings summary ===============================
.venv/lib/python3.13/site-packages/fastapi/testclient.py:1
  /Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/src/agents/feedback_loop/.venv/lib/python3.13/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
22 passed, 1 warning in 7.74s
## Performance Analytics (PAS)
warning: `VIRTUAL_ENV=/Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
..................................                                       [100%]
=============================== warnings summary ===============================
.venv/lib/python3.13/site-packages/fastapi/testclient.py:1
  /Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/src/agents/performance_analytics/.venv/lib/python3.13/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
34 passed, 1 warning in 4.77s
## Task Automation (TAS)
warning: `VIRTUAL_ENV=/Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
.................................................s....................   [100%]
=============================== warnings summary ===============================
.venv/lib/python3.13/site-packages/fastapi/testclient.py:1
  /Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/src/agents/task_automation/.venv/lib/python3.13/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
69 passed, 1 skipped, 1 warning in 5.96s
## Compliance Monitor (CMS)
warning: `VIRTUAL_ENV=/Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
........................................................................ [ 67%]
..................................                                       [100%]
=============================== warnings summary ===============================
.venv/lib/python3.13/site-packages/fastapi/testclient.py:1
  /Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/src/agents/compliance_monitor/.venv/lib/python3.13/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
106 passed, 1 warning in 3.63s
