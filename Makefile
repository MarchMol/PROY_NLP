# Atajos del proyecto. En Windows sin `make`, usa: python tasks.py <tarea>
# Todas las tareas delegan en tasks.py para que exista una sola fuente de verdad.

PY ?= python

.PHONY: help setup test lint fmt

help:
	@$(PY) tasks.py --list

setup:
	@$(PY) tasks.py setup

test:
	@$(PY) tasks.py test

lint:
	@$(PY) tasks.py lint

fmt:
	@$(PY) tasks.py fmt
