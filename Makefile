.DEFAULT_GOAL := help
.PHONY: help dev


up: ## Run all the services, web (Django), Celery, RabbitMQ, Postgres, Redis
	docker compose up

sh: ## Open a shell with all dependencies
	docker compose run web sh

sh-app: ## Open a shell with all dependencies
	docker compose run app sh

psql: ## Open a Postgres shell
	docker compose run web python manage.py dbshell

build: ## Build the docker image used by the 'web' and 'celery' services in the docker-compose.yml
	docker compose build

build-no-cache: ## Build the docker image, without the the docker build cache, used by the 'web' and 'celery' services in the docker-compose.yml
	docker compose build web --no-cache

createsuperuser: ## Create the root Django superuser with username=root password=root
	docker compose \
	    run \
	    -e DJANGO_SUPERUSER_PASSWORD=root \
	    -e DJANGO_SUPERUSER_USERNAME=root \
	    -e DJANGO_SUPERUSER_EMAIL=root@casestudy.com \
	    web \
	    python manage.py createsuperuser --noinput

createusers: ## Create 2 standard users username=user1 and username=user2
	docker compose \
		run \
		web \
		python manage.py shell --command "from django.contrib.auth.models import User; User.objects.create_superuser(username='user1', email='user1@casestudy.com', password='password')"
	docker compose \
		run \
		web \
		python manage.py shell --command "from django.contrib.auth.models import User; User.objects.create_superuser(username='user2', email='user2@casestudy.com', password='password')"


migrate: ## Create and apply database migrations
	docker compose run web python manage.py makemigrations
	docker compose run web python manage.py migrate

open-admin: ## Open the Django admin page
	open http://localhost:8000/admin

open-app: ## Open the React app
	open http://localhost:3000

submit: ## Dump the Postgres database and package your project into a solution.zip file you can submit
	zip -r solution.zip .

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-30s\033[0m %s\n", $$1, $$2}'
