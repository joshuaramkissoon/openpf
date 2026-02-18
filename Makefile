.PHONY: up up-hot up-stable down down-hot down-stable logs logs-hot logs-stable restart restart-hot restart-stable clean clean-hot clean-stable build build-hot build-stable

COMPOSE_STABLE = docker compose -f docker-compose.yml
COMPOSE_HOT = docker compose -f docker-compose.yml -f docker-compose.hot.yml

# MODE=hot (default) | MODE=stable
MODE ?= hot
ifeq ($(MODE),stable)
COMPOSE = $(COMPOSE_STABLE)
else ifeq ($(MODE),hot)
COMPOSE = $(COMPOSE_HOT)
else
$(error Invalid MODE '$(MODE)'. Use MODE=hot or MODE=stable)
endif

up:
	@touch backend/mypf.db
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

logs:
	@mkdir -p backend/logs && touch backend/logs/t212.log backend/logs/marketdata.log backend/logs/scheduler.log
	@(tail -f backend/logs/t212.log backend/logs/marketdata.log backend/logs/scheduler.log | sed 's/^/[mcp] /' &) && $(COMPOSE) logs -f

restart:
	$(MAKE) down MODE=$(MODE)
	$(MAKE) up MODE=$(MODE)

clean:
	$(COMPOSE) down -v

build:
	$(COMPOSE) build --no-cache

up-hot:
	$(MAKE) up MODE=hot

up-stable:
	$(MAKE) up MODE=stable

down-hot:
	$(MAKE) down MODE=hot

down-stable:
	$(MAKE) down MODE=stable

logs-hot:
	$(MAKE) logs MODE=hot

logs-stable:
	$(MAKE) logs MODE=stable

restart-hot:
	$(MAKE) restart MODE=hot

restart-stable:
	$(MAKE) restart MODE=stable

clean-hot:
	$(MAKE) clean MODE=hot

clean-stable:
	$(MAKE) clean MODE=stable

build-hot:
	$(MAKE) build MODE=hot

build-stable:
	$(MAKE) build MODE=stable
