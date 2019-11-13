# App Specific Config
-include config.mk

APPS_DIR         ?= src
MAIN_APP         ?= $(shell ls -1 $(APPS_DIR))
OUT_DIR          ?= out
BUILD_DIR        ?= out/app
BUILD_DOCS_DIR   ?= out/docs
TEST_RESULTS    = test-reports

PACKAGES_DIR               = $(OUT_DIR)/packages
PACKAGES_SPLUNK_BASE_DIR   = $(PACKAGES_DIR)/splunkbase
PACKAGES_SPLUNK_SEMVER_DIR = $(PACKAGES_DIR)/splunksemver
PACKAGES_SPLUNK_SLIM_DIR   = $(PACKAGES_DIR)/splunkslim
PACKAGES_DIR_SPLUNK_DEPS   = $(PACKAGES_DIR)/splunk_deps

PACKAGE_DIRS = $(PACKAGES_DIR) $(PACKAGES_SPLUNK_BASE_DIR) $(PACKAGES_SPLUNK_SEMVER_DIR) $(PACKAGES_SPLUNK_SLIM_DIR) $(PACKAGES_DIR_SPLUNK_DEPS)

MAIN_APP_DESC     ?= Add on for Splunk
main_app_files     = $(shell find $(APPS_DIR)/$(MAIN_APP) -type f ! -iname "app.manifest" ! -iname "app.conf" ! -iname "*.pyc" ! -iname ".*" | sort)
MAIN_APP_OUT       = $(BUILD_DIR)/$(MAIN_APP)

DEPS 							 := $(shell find deps -maxdepth 1 -mindepth 1 -type d -print | awk -F/ '{print $$NF}')

docs_files         = $(shell find docs -type f ! -iname ".*")

RELEASE            = $(shell gitversion /showvariable FullSemVer)
BUILD_NUMBER      ?= 0000
COMMIT_ID         ?= $(shell git rev-parse --short HEAD)
BRANCH            ?= $(shell git branch | grep \* | cut -d ' ' -f2)
VERSION            = $(shell gitversion /showvariable FullSemVer)
PACKAGE_SLUG       =
PACKAGE_VERSION    = $(VERSION)
APP_VERSION        = $(VERSION)

DOCKER_IMG				= $(shell echo $(MAIN_APP) | tr '[:upper:]' '[:lower:]')



VERSION=$(shell gitversion /showvariable MajorMinorPatch)

PACKAGE_SLUG=D$(COMMIT_ID)
ifneq (,$(findstring $(BRANCH),"master"))
	PACKAGE_SLUG=R$(COMMIT_ID)
endif

PACKAGE_VERSION=$(VERSION)-$(PACKAGE_SLUG)
APP_VERSION=$(VERSION)$(PACKAGE_SLUG)

COPYRIGHT_DOCKER_IMAGE = splseckit/copyright-header:latest
COPYRIGHT_CMD ?= docker run --rm --volume `pwd`:/usr/src/ $(COPYRIGHT_DOCKER_IMAGE)
COPYRIGHT_SOFTWARE ?= $(MAIN_APP)
COPYRIGHT_SOFTWARE_DESCRIPTION ?= $(MAIN_APP_DESC)
COPYRIGHT_OUTPUT_DIR ?= .
COPYRIGHT_WORD_WRAP ?= 100
COPYRIGHT_PATHS ?= $(APPS_DIR)

SPLUNKBASE    ?= Not Published
REPOSITORY    ?= Private

SPHINXBUILD   = sphinx-build

SPHINXOPTS          =
SPHINXSOURCEDIR     = docs
SPHINXBUILDDIR      = out/docs

EPUB_NAME          ?= README



#.PHONY   = help package clean docs config all_dirs build $(PACKAGE_DIRS) list
.DEFAULT = help

.PHONY: help
help: ## Show this help message.
	@echo 'usage: make [target] ...'
	@echo
	@echo 'targets:'
	@egrep '^(.+)\:\ ##\ (.+)' $(MAKEFILE_LIST) | column -t -c 2 -s ':#' | sed 's/^/  /'

ALL_DIRS = $(OUT_DIR) $(BUILD_DIR) $(TEST_RESULTS) $(PACKAGE_DIRS) $(BUILD_DOCS_DIR)

.PHONY: CHECK_ENV
CHECK_ENV: ##Check the environment
CHECK_ENV:
	EXECUTABLES = crudini jq sponge slim splunk-appinspect pandoc sphinx-build
	K := $(foreach exec,$(EXECUTABLES),$(if $(shell which $(exec)),some string,$(error "No $(exec) in PATH")))

.PHONY: clean
clean:
	@rm -rf $(OUT_DIR)
	@rm -rf $(TEST_RESULTS)

clean_all: clean docker_clean

# Create all build directories
$(ALL_DIRS):
	@mkdir -p $@


#Copy all source files for main app
$(BUILD_DIR)/%: $(APPS_DIR)/%
	mkdir -p $(@D)
#	@chmod o-w,g-w,a+X  $(@D)
	cp -L $< $@
	chmod o-w,g-w,a-x $@

# Copy and update app.conf
$(BUILD_DIR)/$(MAIN_APP)/default/app.conf: $(ALL_DIRS)\
																	$(patsubst $(APPS_DIR)/%,$(BUILD_DIR)/%,$(main_app_files)) \
																	$(APPS_DIR)/$(MAIN_APP)/default/app.conf

	cp $(APPS_DIR)/$(MAIN_APP)/default/app.conf $(BUILD_DIR)/$(MAIN_APP)/default/app.conf
	crudini --set $(BUILD_DIR)/$(MAIN_APP)/default/app.conf launcher version $(APP_VERSION)
	crudini --set $(BUILD_DIR)/$(MAIN_APP)/default/app.conf launcher description "$(MAIN_DESCRIPTION)"
	crudini --set $(BUILD_DIR)/$(MAIN_APP)/default/app.conf install build $(BUILD_NUMBER)
	crudini --set $(BUILD_DIR)/$(MAIN_APP)/default/app.conf ui label "$(MAIN_LABEL)"
	chmod o-w,g-w,a-x $@

#Copy and update app.manifest
$(BUILD_DIR)/$(MAIN_APP)/app.manifest: $(ALL_DIRS)\
															$(patsubst $(APPS_DIR)/%,$(BUILD_DIR)/%,$(main_app_files)) \
															$(BUILD_DIR)/$(MAIN_APP)/$(LICENSE_FILE) \
															$(BUILD_DIR)/$(MAIN_APP)/default/app.conf \
															$(APPS_DIR)/$(MAIN_APP)/app.manifest
	mkdir -p $(BUILD_DIR)/$(MAIN_APP)/default/
	cp $(APPS_DIR)/$(MAIN_APP)/app.manifest $(BUILD_DIR)/$(MAIN_APP)/app.manifest
	slim generate-manifest --update $(BUILD_DIR)/$(MAIN_APP) | sponge $(BUILD_DIR)/$(MAIN_APP)/app.manifest
	jq '.info.title="$(MAIN_LABEL)"'  $(BUILD_DIR)/$(MAIN_APP)/app.manifest | sponge $(BUILD_DIR)/$(MAIN_APP)/app.manifest
	jq '.info.description="$(MAIN_DESCRIPTION)"'  $(BUILD_DIR)/$(MAIN_APP)/app.manifest | sponge $(BUILD_DIR)/$(MAIN_APP)/app.manifest
	jq '.info.license= { "name": "$(COPYRIGHT_LICENSE)", "text": "$(LICENSE_FILE)", "uri": "$(LICENSE_URL)" }'  $(BUILD_DIR)/$(MAIN_APP)/app.manifest | sponge $(BUILD_DIR)/$(MAIN_APP)/app.manifest
	chmod o-w,g-w,a-x $@

#Copy and update license file
$(BUILD_DIR)/$(MAIN_APP)/$(LICENSE_FILE): $(patsubst $(APPS_DIR)/%,$(BUILD_DIR)/%,$(main_app_files)) \
																 $(LICENSE_FILE)
	cp $< $@
	chmod o-w,g-w,a-x $@

$(BUILD_DOCS_DIR)/$(MAIN_APP)/epub/$(EPUB_NAME).epub: $(docs_files)
	@$(SPHINXBUILD) -M epub "$(SPHINXSOURCEDIR)" "$(BUILD_DOCS_DIR)/$(MAIN_APP)" $(SPHINXOPTS) -D epub_basename=$(EPUB_NAME) -D rst_prolog="$$rst_prolog"

$(BUILD_DIR)/$(MAIN_APP)/$(EPUB_NAME).epub: $(BUILD_DOCS_DIR)/$(MAIN_APP)/epub/$(EPUB_NAME).epub
	cp $< $@
	chmod o-w,g-w,a-x $@

.PHONY: $(DEPS)
$(DEPS):
	@echo ADD $(BUILD_DIR)/$@ /opt/splunk/etc/apps/$@ >>$(BUILD_DIR)/Dockerfile
	@$(MAKE) -C deps/$@ build PACKAGES_DIR=$(realpath $(PACKAGES_DIR)) BUILD_DIR=$(realpath $(BUILD_DIR))


build: $(ALL_DIRS) \
				$(patsubst $(APPS_DIR)/%,$(BUILD_DIR)/%,$(main_app_files)) \
				$(BUILD_DIR)/$(MAIN_APP)/$(LICENSE_FILE)\
				$(BUILD_DIR)/$(MAIN_APP)/app.manifest \
				$(BUILD_DIR)/$(MAIN_APP)/$(EPUB_NAME).epub \

$(PACKAGES_SPLUNK_BASE_DIR)/$(MAIN_APP)-$(PACKAGE_VERSION).tar.gz: build
	slim package -o $(PACKAGES_SPLUNK_BASE_DIR) $(BUILD_DIR)/$(MAIN_APP)

package: ## Package each app
package: $(PACKAGES_SPLUNK_BASE_DIR)/$(MAIN_APP)-$(PACKAGE_VERSION).tar.gz

test-reports/$(MAIN_APP)-appapproval.xml: $(PACKAGES_SPLUNK_BASE_DIR)/$(MAIN_APP)-$(PACKAGE_VERSION).tar.gz
	splunk-appinspect inspect $(PACKAGES_SPLUNK_BASE_DIR)/$(MAIN_APP)-$(PACKAGE_VERSION).tar.gz --data-format junitxml --output-file test-reports/$(MAIN_APP)-appapproval.xml --excluded-tags manual --excluded-tags prerelease  --included-tags appapproval

test-reports/$(MAIN_APP)-splunk_appinspect.xml: $(PACKAGES_SPLUNK_BASE_DIR)/$(MAIN_APP)-$(PACKAGE_VERSION).tar.gz
	splunk-appinspect inspect $(PACKAGES_SPLUNK_BASE_DIR)/$(MAIN_APP)-$(PACKAGE_VERSION).tar.gz --data-format junitxml --output-file test-reports/$(MAIN_APP)-splunk_appinspect.xml --excluded-tags manual --excluded-tags prerelease  --included-tags splunk_appinspect

test-reports/$(MAIN_APP)-cloud.xml: $(PACKAGES_SPLUNK_BASE_DIR)/$(MAIN_APP)-$(PACKAGE_VERSION).tar.gz
	splunk-appinspect inspect $(PACKAGES_SPLUNK_BASE_DIR)/$(MAIN_APP)-$(PACKAGE_VERSION).tar.gz --data-format junitxml --output-file test-reports/$(MAIN_APP)-cloud.xml --excluded-tags manual --excluded-tags prerelease  --included-tags cloud

test-reports/$(MAIN_APP)-self-service.xml: $(PACKAGES_SPLUNK_BASE_DIR)/$(MAIN_APP)-$(PACKAGE_VERSION).tar.gz
	splunk-appinspect inspect $(PACKAGES_SPLUNK_BASE_DIR)/$(MAIN_APP)-$(PACKAGE_VERSION).tar.gz --data-format junitxml --output-file test-reports/$(MAIN_APP)-self-service.xml --excluded-tags manual --excluded-tags prerelease  --included-tags self-service

package_test: ## Package Test
package_test: test-reports/$(MAIN_APP)-appapproval.xml \
	 			      test-reports/$(MAIN_APP)-splunk_appinspect.xml \
							test-reports/$(MAIN_APP)-cloud.xml \
							test-reports/$(MAIN_APP)-self-service.xml

# Docker socket is needed to run license docker containers
docker_package:
	docker run --rm --volume `pwd`:/usr/build --volume /var/run/docker.sock:/var/run/docker.sock -w /usr/build -it $(DOCKER_IMAGE) bash -c "make package_test"
docker_package_test:
	docker run --rm --volume `pwd`:/usr/build --volume /var/run/docker.sock:/var/run/docker.sock -w /usr/build -it $(DOCKER_IMAGE) bash -c "make package_test"

$(shell docker ps -qa --no-trunc  --filter status=exited --filter ancestor=$(DOCKER_IMG)-dev):
	docker rm $@

docker_clean: $(shell docker ps -qa --no-trunc  --filter status=exited --filter ancestor=$(DOCKER_IMG)-dev)

.PHONY: $(BUILD_DIR)/Dockerfile
$(BUILD_DIR)/Dockerfile:
	cp buildtools/Docker/standalone_dev/Dockerfile $(BUILD_DIR)/Dockerfile

docker_build: build $(BUILD_DIR)/Dockerfile  $(DEPS) 
	docker build -t $(DOCKER_IMG)-dev:latest -f $(BUILD_DIR)/Dockerfile .

docker_run: docker_build
	docker run \
	      -it \
				-v $(realpath $(BUILD_DIR)/$(MAIN_APP)):/opt/splunk/etc/apps/$(MAIN_APP) \
				-p 8000:8000 \
				-e 'SPLUNK_START_ARGS=--accept-license' \
				-e 'SPLUNK_PASSWORD=Changed!11' \
				$(DOCKER_IMG)-dev:latest start

docker_dev: docker_build
	docker run \
	      -it \
				-v $(realpath $(APPS_DIR)/$(MAIN_APP)):/opt/splunk/etc/apps/$(MAIN_APP) \
				-p 8000:8000 \
				-e 'SPLUNK_START_ARGS=--accept-license' \
				-e 'SPLUNK_PASSWORD=Changed!11' \
				$(DOCKER_IMG)-dev:latest start


add-copyright: ## Add copyright notice header to supported file types
add-copyright:
	$(COPYRIGHT_CMD) \
		$(COPYRIGHT_LICENSE_ARG) \
	  --add-path $(APPS_DIR)/$(MAIN_APP) \
	  --guess-extension \
	  --copyright-holder '$(COPYRIGHT_HOLDER)' \
	  --copyright-software '$(COPYRIGHT_SOFTWARE)' \
	  --copyright-software-description '$(COPYRIGHT_SOFTWARE_DESCRIPTION)' \
	  --copyright-year $(COPYRIGHT_YEAR) \
	  --word-wrap $(COPYRIGHT_WORD_WRAP) \
	  --output-dir .
	  
