# SAM Lambda build: copy only *.py under app/; install Linux x86_64 wheels (Lambda) from macOS/ARM.
# Handler: app.main.handler

.PHONY: build-BrollApiFunction
build-BrollApiFunction:
	@mkdir -p "$(ARTIFACTS_DIR)/app"
	rsync -a \
	  --include='*/' \
	  --include='*.py' \
	  --exclude='*' \
	  "$(CURDIR)/app/" "$(ARTIFACTS_DIR)/app/"
	"$(shell command -v python3.13 2>/dev/null || command -v python3)" -m pip install \
	  -r "$(CURDIR)/requirements-lambda.txt" \
	  -t "$(ARTIFACTS_DIR)" \
	  --upgrade \
	  --no-cache-dir \
	  --platform manylinux2014_x86_64 \
	  --python-version 313 \
	  --implementation cp \
	  --abi cp313 \
	  --only-binary=:all:
