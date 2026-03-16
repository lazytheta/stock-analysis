# Testing

## Test Suites

| File | Tests | Coverage |
|------|-------|----------|
| `test_tastytrade_api.py` | 41 tests | tastytrade_api.py |
| `test_ibkr_api.py` | 40 tests | ibkr_api.py |

## Running Tests

```bash
# All tests
python3 -m pytest test_tastytrade_api.py test_ibkr_api.py -v

# Single suite
python3 -m pytest test_tastytrade_api.py -v

# Specific test
python3 -m pytest test_ibkr_api.py::TestPositions::test_option_position -v
```

## Key Points

- Tests run **fully offline** via mocks (~0.1s), no credentials needed
- Use `sys.modules["module"] = mock` (NOT `setdefault`) for module mocks
- Always `del sys.modules["ibkr_api"]` before reimport for clean isolation
- Run tests after ANY change to broker API modules as regression check

## When to Run Tests

- After modifying `tastytrade_api.py` or `ibkr_api.py`
- After modifying `broker_adapter.py` or `trade_utils.py`
- Before pushing to main
