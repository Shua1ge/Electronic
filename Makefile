SHELL := /bin/zsh
UV := /opt/homebrew/bin/uv
PYTHON := /opt/homebrew/bin/python3.13
export UV_CACHE_DIR := $(CURDIR)/.cache/uv
export XDG_CACHE_HOME := $(CURDIR)/.cache
export HOMEBREW_NO_AUTO_UPDATE := 1

.PHONY: setup download verify verify-vision check ocr-tool test
setup:
	HOMEBREW_BUNDLE_NO_UPGRADE=1 /opt/homebrew/bin/brew bundle install --file=Brewfile
	$(UV) sync --frozen --python $(PYTHON)

download:
	.venv/bin/python scripts/download_model.py

verify:
	scripts/local-python scripts/verify_pipeline.py

verify-vision:
	scripts/offline-python scripts/verify_offline.py
	scripts/offline-python scripts/infer_image.py data/samples/ppt-cable-topology.png --output outputs/verification/vision.json
	scripts/local-python scripts/check_vision.py

check:
	$(UV) pip check --python .venv/bin/python
	HOMEBREW_BUNDLE_NO_UPGRADE=1 /opt/homebrew/bin/brew bundle check --file=Brewfile

ocr-tool:
	mkdir -p tools/bin .cache/swift
	/usr/bin/xcrun swiftc -O -module-cache-path .cache/swift scripts/vision_ocr.swift -o tools/bin/vision-ocr

test:
	scripts/local-python -m unittest discover -s tests -v
