SHELL := /bin/sh
.DEFAULT_GOAL := all
BUILD_DIR := .build
PDF := $(BUILD_DIR)/main.pdf
TEX := $(wildcard *.tex sections/*.tex)

.PHONY: all watch
all: $(PDF)

$(PDF): $(TEX) Makefile
	@mkdir -p $(BUILD_DIR)
	pdflatex -interaction=nonstopmode -halt-on-error -file-line-error -output-directory=$(BUILD_DIR) main.tex
	pdflatex -interaction=nonstopmode -halt-on-error -file-line-error -output-directory=$(BUILD_DIR) main.tex

# Poll source checksums, including additions and deletions; Ctrl-C stops watching.
# Remember a failed version so it is retried only after another source change.
watch:
	@echo "Watching LaTeX sources; press Ctrl-C to stop."
	@previous=''; while :; do \
		current=$$(find . -type f \( -name '*.tex' -o -name 'Makefile' \) ! -path './$(BUILD_DIR)/*' -exec cksum {} \; | LC_ALL=C sort); \
		if [ "$$current" != "$$previous" ]; then \
			$(MAKE) --no-print-directory -B all || echo "Build failed; waiting for source changes."; \
			previous="$$current"; \
		fi; \
		sleep 1; \
	done
