.PHONY: install demo eval test docker-demo

install:
	pip install -r requirements.txt

demo:
	python -m waybill.cli demo -n 8

eval:
	python -m waybill.cli eval -n 200

test:
	pytest -q

docker-demo:
	cd docker && docker compose run --rm waybill demo
