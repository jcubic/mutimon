VERSION = 0.5.0

.PHONY: all publish publish-test version build clean test coverage lint

all: version build

publish: version build
	twine upload dist/*

publish-test: version build
	twine upload --repository testpypi dist/*

build: clean
	python -m build

clean:
	rm -rf dist/ build/

test:
	python -m pytest tests/ -v

coverage:
	python -m pytest tests/ -v --cov=mutimon --cov-report=term --cov-report=lcov:coverage.lcov

lint:
	ruff check src/ tests/

version:
	sed -i 's/^version = "[0-9]\+\.[0-9]\+\.[0-9]\+"/version = "$(VERSION)"/' pyproject.toml
	sed -i 's/__version__ = "[0-9]\+\.[0-9]\+\.[0-9]\+"/__version__ = "$(VERSION)"/' src/mutimon/__init__.py
	sed -i 's|pip-[0-9]\+\.[0-9]\+\.[0-9]\+-blue|pip-$(VERSION)-blue|' README.md
