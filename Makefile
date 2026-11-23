.PHONY: install run test load pull history docker clean

install:
	python3 -m pip install -r requirements.txt

run:
	./run.sh

test:
	python3 -m unittest discover -s tests -v

load:
	python3 -m ingest.cli load data/inbox_eugene --db data/accounts/eugene.db

pull:
	python3 -m ingest.amazon --days 7 --out data/inbox_eugene

history:
	python3 -m ingest.history --days 88 --out data/inbox_eugene

docker:
	docker build -t j-dashboard .

clean:
	rm -rf __pycache__ */__pycache__ .pytest_cache
