# Local docker loop for the TA-aws-trusted-advisor integration suite.
# Requires a Docker engine on THIS host. If you don't have one, prefer
# `run_local.py --backend live` against a reachable Splunk instead.

APP := TA-aws-trusted-advisor
VERSION ?= 1.0.0

.PHONY: build up test down logs cycle

build:  ## ucc-gen build into ../stage with widened test perms
	cd .. && ucc-gen build --source package --ta-version $(VERSION)
	cd .. && rm -rf stage && mkdir -p stage && cp -r output/$(APP) stage/$(APP)
	# The test container's splunk user (a different uid) must read the app and
	# write local/inputs.conf + checkpoints into the bind mount. Wide perms are
	# fine on this throwaway copy — it is never the artefact.
	cd .. && chmod -R 777 stage/$(APP)

up:  ## start Splunk and wait for health
	docker compose up -d --wait --wait-timeout 360

test:  ## run the suite against the running container
	cd .. && SPLUNK_MGMT_URL=https://127.0.0.1:8089 SPLUNK_USER=admin \
		SPLUNK_PASSWORD=Changeme1! SPLUNK_CONTAINER=ta_aws_trusted_advisor_splunk \
		pytest tests/ -v -ra

down:  ## stop and remove the container + volumes
	docker compose down -v || true

logs:  ## tail splunkd on failure
	docker compose logs --tail 300 splunk || true
	docker exec ta_aws_trusted_advisor_splunk tail -n 300 /opt/splunk/var/log/splunk/splunkd.log || true

cycle: build up test down  ## full local loop
