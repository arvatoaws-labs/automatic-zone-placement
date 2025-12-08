#!/bin/bash
pip install -q -r requirements.txt -r requirements-test.txt
pytest -q "$@"
