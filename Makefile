# Safe Docker Compose helpers for NTRIP Caster.

ENV ?= development
PROFILES ?=
SERVICE ?= ntrip-caster
PYTHON ?= python3

COMPOSE_FILES := -f docker-compose.yml
ifeq ($(ENV),production)
COMPOSE_FILES += -f docker-compose.prod.yml
endif

empty :=
space := $(empty) $(empty)
comma := ,
PROFILE_LIST := $(strip $(subst $(comma),$(space),$(PROFILES)))
PROFILE_ARGS := $(foreach profile,$(PROFILE_LIST),--profile $(profile))
COMPOSE := docker compose $(COMPOSE_FILES) $(PROFILE_ARGS)
MONITORING_ARG := $(if $(findstring monitoring,$(PROFILES)),--monitoring,)

.DEFAULT_GOAL := help

.PHONY: help ci-static-check check-env prepare-env validate build up down restart status logs health info
.PHONY: monitoring full prod backup clean

help:
	@echo "NTRIP Caster Docker targets"
	@echo "  make ci-static-check                  Run safe static CI checks only"
	@echo "  make validate                         Validate Compose without starting services"
	@echo "  make build                            Build the application image"
	@echo "  make up                               Start the core service"
	@echo "  make monitoring                       Start core and local monitoring"
	@echo "  make full                             Start nginx, monitoring, and cache profiles"
	@echo "  make prod                             Start the production overlay"
	@echo "  make down | restart | status | logs   Manage services"
	@echo ""
	@echo "Variables: ENV=development|testing|production PROFILES=nginx,monitoring,cache"
	@echo "Credentials remain only in the ignored local .env and are never printed."

ci-static-check:
	@$(PYTHON) -m unittest discover -s tests -p "test_deployment_and_policy.py"
	@$(PYTHON) -m unittest discover -s tests -p "test_ci_workflow.py"
	@echo "Safe static CI checks OK"

check-env:
	@docker --version
	@docker compose version
	@$(PYTHON) --version

prepare-env:
	@$(PYTHON) scripts/deployment_config.py prepare-env \
		--env-file .env \
		--example .env.example \
		--environment $(ENV) \
		--profiles "$(PROFILES)" $(MONITORING_ARG)
	@chmod 600 .env

validate: check-env prepare-env
	@$(COMPOSE) config --quiet
	@echo "Compose configuration check OK"

build: validate
	@$(COMPOSE) build

up: validate
	@$(COMPOSE) up -d
	@$(MAKE) info ENV=$(ENV) PROFILES=$(PROFILES)

down:
	@$(COMPOSE) down

restart: validate
	@$(COMPOSE) up -d

status:
	@$(COMPOSE) ps

logs:
	@$(COMPOSE) logs -f $(SERVICE)

health:
	@$(COMPOSE) exec -T ntrip-caster python /app/healthcheck.py

info:
	@echo "NTRIP: TCP 2101 is remotely published by default; restrict it with a firewall."
	@echo "Web: http://127.0.0.1:5757"
	@echo "Monitoring, when enabled: http://127.0.0.1:9090 and http://127.0.0.1:3000"

monitoring:
	@$(MAKE) up ENV=$(ENV) PROFILES=monitoring

full:
	@$(MAKE) up ENV=$(ENV) PROFILES=nginx,monitoring,cache

prod:
	@$(MAKE) up ENV=production PROFILES=$(PROFILES)

backup:
	@./docker-deploy.sh backup

clean:
	@$(COMPOSE) down --remove-orphans
