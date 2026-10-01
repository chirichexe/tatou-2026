LATEXMK ?= latexmk
TEX_FILE := samm-specialist.tex
BUILD_DIR := build

.PHONY: all pdf watch clean

all: pdf

# Keep auxiliary files in build/ while leaving the final PDF beside the source.
pdf: $(TEX_FILE)
	mkdir -p $(BUILD_DIR)
	$(LATEXMK) -pdf -auxdir=$(BUILD_DIR) $(TEX_FILE)

# Run this once and leave it running; latexmk rebuilds the PDF after source changes.
watch:
	mkdir -p $(BUILD_DIR)
	$(LATEXMK) -pdf -pvc -view=none -auxdir=$(BUILD_DIR) $(TEX_FILE)

clean:
	$(LATEXMK) -C -auxdir=$(BUILD_DIR) $(TEX_FILE)
	rm -rf $(BUILD_DIR)
