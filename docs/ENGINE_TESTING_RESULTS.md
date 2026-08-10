# Engine Testing Results

Generated: 2026-08-10T13:00:00Z

## Root (gateway + integration)
.........                                                                [100%]
=============================== warnings summary ===============================
.venv/lib/python3.13/site-packages/fastapi/testclient.py:1
  /Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/.venv/lib/python3.13/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
9 passed, 1 warning in 0.71s

## Follow-up Sequences (FUS)
warning: `VIRTUAL_ENV=/Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
........................................................................ [ 75%]
........................                                                 [100%]
=============================== warnings summary ===============================
.venv/lib/python3.13/site-packages/fastapi/testclient.py:1
  /Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/src/agents/follow_up_sequences/.venv/lib/python3.13/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
96 passed, 1 warning in 1.21s

## Performance Analytics (PAS)
warning: `VIRTUAL_ENV=/Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
........................................................................ [ 79%]
...................                                                      [100%]
=============================== warnings summary ===============================
.venv/lib/python3.13/site-packages/fastapi/testclient.py:1
  /Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/src/agents/performance_analytics/.venv/lib/python3.13/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
91 passed, 1 warning in 7.40s

## Task Automation (TAS)
warning: `VIRTUAL_ENV=/Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
........................................................................ [ 55%]
.....................................s....................               [100%]
=============================== warnings summary ===============================
.venv/lib/python3.13/site-packages/fastapi/testclient.py:1
  /Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/src/agents/task_automation/.venv/lib/python3.13/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
129 passed, 1 skipped, 1 warning in 6.47s

## Feedback Loop (FLS)
warning: `VIRTUAL_ENV=/Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
...............................................................          [100%]
=============================== warnings summary ===============================
.venv/lib/python3.13/site-packages/fastapi/testclient.py:1
  /Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/src/agents/feedback_loop/.venv/lib/python3.13/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

tests/test_disk_persistence.py: 10 warnings
  /Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/src/agents/feedback_loop/.venv/lib/python3.13/site-packages/joblib/numpy_pickle.py:207: DeprecationWarning: Setting the shape on a NumPy array has been deprecated in NumPy 2.5.
    As an alternative, you can create a new view using np.reshape (with copy=False if needed).
    array.shape = self.shape

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
63 passed, 11 warnings in 15.43s

## Compliance Monitor (CMS)
warning: `VIRTUAL_ENV=/Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/.venv` does not match the project environment path `.venv` and will be ignored; use `--active` to target the active environment instead
........................................................................ [ 47%]
........................................................................ [ 94%]
........                                                                 [100%]
=============================== warnings summary ===============================
.venv/lib/python3.13/site-packages/fastapi/testclient.py:1
  /Users/macbookpro/crequity/crm_op_service/crm-operations-platform-service/src/agents/compliance_monitor/.venv/lib/python3.13/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
152 passed, 1 warning in 5.44s
