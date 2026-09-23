HARNESS ?= auto
CLAUDE_SETTINGS ?= $(HOME)/.claude/settings.json
STEP_STATUS_HOME ?= $(HOME)/.claude/step-status
BIN := $(STEP_STATUS_HOME)/bin
PI_AGENT ?= $(HOME)/.pi/agent
TRACKER_CC := $(if $(filter pi,$(HARNESS)),,1)
TRACKER_PI := $(if $(filter pi both,$(HARNESS)),1,$(if $(filter auto,$(HARNESS)),$(shell command -v pi >/dev/null && echo 1)))
WT := workflow-tracker/scripts
SCRIPTS := steps.sh statusline.sh capture_context.py hook_session_start.sh hook_prompt.sh wire_statusline.sh
WIRE_ENV := CLAUDE_SETTINGS="$(CLAUDE_SETTINGS)" STEP_STATUS_HOME="$(STEP_STATUS_HOME)"

EXT := extension
UV ?= uv
UV_INSTALL_FLAGS ?= --refresh --exclude-newer "3 days"
PENGUPOOL ?= pengupool
EDITOR_CLI ?=
EDITOR_RUN = EDITOR_CLI="$(EDITOR_CLI)" python3 scripts/editor_cli.py
VSIX := $(abspath $(EXT)/pengupool-local.vsix)

.PHONY: install uninstall check-install install-all uninstall-all install-hooks uninstall-hooks install-tracker uninstall-tracker selfcheck ext-deps ext-compile ext-package ext-install ext-uninstall

install:
	$(UV) tool install --force $(UV_INSTALL_FLAGS) .
	CLAUDE_SETTINGS="$(CLAUDE_SETTINGS)" "$(PENGUPOOL)" setup $(HARNESS)
	$(MAKE) install-tracker

check-install:
	CLAUDE_SETTINGS="$(CLAUDE_SETTINGS)" "$(PENGUPOOL)" setup --check $(HARNESS)

install-all:
	$(EDITOR_RUN) --check
	$(MAKE) install
	$(MAKE) ext-install

uninstall-all:
	$(MAKE) ext-uninstall
	$(MAKE) uninstall

install-hooks:
	CLAUDE_SETTINGS="$(CLAUDE_SETTINGS)" "$(PENGUPOOL)" install-hook

uninstall-hooks:
	CLAUDE_SETTINGS="$(CLAUDE_SETTINGS)" "$(PENGUPOOL)" uninstall-hook

# Copy the tracker scripts to a stable dir (the plugin cache dir changes on every update) and wire
# GLOBAL settings from there, so settings.json points at paths that survive updates. Running
# wire_statusline.sh FROM $(BIN) makes it register statusLine + SessionStart + UserPromptSubmit all
# pointing at $(BIN).
install-tracker:
	mkdir -p "$(BIN)"
	for f in $(SCRIPTS); do cp "$(WT)/$$f" "$(BIN)/$$f"; done
	chmod +x "$(BIN)"/*.sh
	$(if $(TRACKER_CC),$(WIRE_ENV) bash "$(BIN)/wire_statusline.sh")
	$(if $(TRACKER_PI),mkdir -p "$(PI_AGENT)/extensions" "$(PI_AGENT)/skills/workflow-tracker")
	$(if $(TRACKER_PI),sed 's|__STEP_STATUS_BIN__|$(BIN)|' workflow-tracker/pi/workflow-tracker.ts > "$(PI_AGENT)/extensions/workflow-tracker.ts")
	$(if $(TRACKER_PI),cp workflow-tracker/SKILL.md "$(PI_AGENT)/skills/workflow-tracker/SKILL.md")
	$(if $(TRACKER_PI),ln -sfn "$(BIN)" "$(PI_AGENT)/skills/workflow-tracker/scripts")
	$(if $(TRACKER_PI),@echo "workflow-tracker: pi extension + skill → $(PI_AGENT) (restart pi sessions to load)")

# Unwires both harnesses whatever HARNESS is: removing only our own files and settings entries is safe.
uninstall-tracker:
	@if [ -f "$(BIN)/wire_statusline.sh" ]; then $(WIRE_ENV) bash "$(BIN)/wire_statusline.sh" --unwire; fi
	for f in $(SCRIPTS); do rm -f "$(BIN)/$$f"; done
	rm -f "$(PI_AGENT)/extensions/workflow-tracker.ts"
	rm -rf "$(PI_AGENT)/skills/workflow-tracker"

uninstall:
	$(MAKE) uninstall-tracker
	CLAUDE_SETTINGS="$(CLAUDE_SETTINGS)" "$(PENGUPOOL)" teardown both
	$(UV) tool uninstall pengupool

selfcheck:
	bash "$(WT)/wire_statusline.sh" --selfcheck
	bash "$(WT)/steps.sh" --selfcheck

# Dependency setup is explicit; routine rebuilds reuse the installed toolchain.
ext-deps:
	cd "$(EXT)" && npm ci

ext-compile:
	@test -f "$(EXT)/node_modules/typescript/bin/tsc" || { echo "Run make ext-deps first." >&2; exit 1; }
	cd "$(EXT)" && npm run compile

ext-package: ext-compile
	cd "$(EXT)" && npm run package -- --out "$(VSIX)"

ext-install:
	$(EDITOR_RUN) --check
	$(MAKE) ext-package
	$(EDITOR_RUN) --install-extension "$(VSIX)" --force
	@echo "Reload the editor window to activate the updated extension and backend."

ext-uninstall:
	$(EDITOR_RUN) --uninstall-extension nduwork.pengupool
