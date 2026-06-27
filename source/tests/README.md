# Unit Tests for rjsxrd

This directory contains unit tests for the rjsxrd VPN config generator.

## Running Tests

### Prerequisites

Install test dependencies:
```bash
pip install -r requirements.txt
```

This installs:
- `pytest` - Test framework
- `pytest-cov` - Coverage reporting
- `pytest-asyncio` - Async test support

### Run All Tests

```bash
cd source
pytest
```

### Run Specific Test File

```bash
pytest tests/test_fetcher.py -v
```

### Run Specific Test Function

```bash
pytest tests/test_fetcher.py::TestFetchData::test_fetch_success -v
```

### Run with Coverage Report

```bash
pytest --cov=fetchers --cov=utils --cov=processors --cov-report=html
```

Coverage report will be in `htmlcov/index.html`.

### Run Tests by Marker

```bash
# Run only unit tests (fast, no network)
pytest -m unit

# Run only integration tests (require network)
pytest -m integration

# Run all tests except slow ones
pytest -m "not slow"
```

### Run Tests in Parallel

```bash
pip install pytest-xdist
pytest -n auto  # Auto-detect CPU count
```

## Test Structure

```
tests/
├── conftest.py                 # Shared fixtures and configuration
├── test_fetcher.py             # Tests for fetchers/fetcher.py
├── test_file_utils.py          # Tests for utils/file_utils.py (26+)
├── test_config_processor.py    # Tests for processors/config_processor.py (45+)
├── test_executor_cache.py      # Tests for utils/executor_cache.py
├── test_ip_checker.py          # Tests for utils/ip_checker.py
├── test_ip_verifier.py         # Tests for utils/ip_verifier.py
├── test_logger.py              # Tests for utils/logger.py
├── test_process_registry.py    # Tests for utils/process_registry.py
├── test_progress.py            # Tests for utils/progress.py
├── test_proxy_monitor.py       # Tests for utils/proxy_monitor.py
├── test_security_filter.py     # Tests for utils/security_filter.py (28+)
├── test_simple_tester.py       # 25 tests (extract_host_port + SimpleTester)
├── test_smart_eta.py           # 14 tests for utils/smart_eta.py
├── test_telegram_proxy_scraper.py  # 27 tests for Telegram proxy scraping
├── test_url_stats.py           # Tests for utils/url_stats.py
├── test_xray_tester.py         # Tests for utils/xray_tester.py
├── test_yaml_converter.py      # 28 tests for utils/yaml_converter.py
└── README.md                   # This file
```

## Writing New Tests

1. Create a new file `test_<module>.py`
2. Import the module you're testing
3. Create test classes with descriptive names
4. Use fixtures from `conftest.py` for common data
5. Mark slow tests with `@pytest.mark.slow`
6. Mark network tests with `@pytest.mark.integration`

### Example Test

```python
import pytest
from my_module import my_function

class TestMyFunction:
    def test_success_case(self):
        result = my_function(valid_input)
        assert result == expected_output
    
    def test_failure_case(self):
        with pytest.raises(ValueError):
            my_function(invalid_input)
```

## Test Categories

### Unit Tests (`@pytest.mark.unit`)
- Test pure logic
- No external dependencies
- Fast execution (<100ms per test)
- Mock all I/O operations

### Integration Tests (`@pytest.mark.integration`)
- Test with real services
- May require network access
- Slower execution
- Test actual behavior

### Slow Tests (`@pytest.mark.slow`)
- Take >1 second to run
- May involve large datasets
- Skipped in quick test runs

## Continuous Integration

Tests are designed to run in GitHub Actions:
- Unit tests run on every commit
- Integration tests run nightly
- Coverage threshold: 80%

## Troubleshooting

### Import Errors

Make sure you're running from the `source` directory:
```bash
cd source
pytest
```

### Network Tests Failing

Network tests may fail in isolated environments. Skip them:
```bash
pytest -m "not integration"
```

### Async Test Errors

Make sure pytest-asyncio is installed:
```bash
pip install pytest-asyncio
```
