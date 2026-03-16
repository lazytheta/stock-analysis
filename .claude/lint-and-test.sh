#!/bin/bash
cd /Users/administrator/Documents/github/stock-analysis

echo "=== Ruff lint ==="
python3 -m ruff check . 2>&1 | tail -10

echo ""
echo "=== Tests ==="
python3 -m pytest test_tastytrade_api.py test_ibkr_api.py -q 2>&1 | tail -5

exit 0
