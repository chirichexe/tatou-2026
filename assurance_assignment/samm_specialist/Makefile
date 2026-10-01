LATEXMK ?= latexmk
TEX_FILE := samm-specialist.tex
BUILD_DIR := build
PDF_DIR := $(CURDIR)
ROOT_AUX := $(basename $(TEX_FILE)).aux $(basename $(TEX_FILE)).fdb_latexmk \
	$(basename $(TEX_FILE)).fls $(basename $(TEX_FILE)).log \
	$(basename $(TEX_FILE)).out $(basename $(TEX_FILE)).synctex.gz

.PHONY: all pdf watch clean

all: pdf

# Put all compilation intermediates in build/, with only the final PDF beside the source.
pdf: $(TEX_FILE)
	mkdir -p $(BUILD_DIR)
	$(LATEXMK) -pdf -outdir=$(BUILD_DIR) -out2dir=$(PDF_DIR) $(TEX_FILE)

# Run this once and leave it running; latexmk rebuilds the PDF after source changes.
watch:
	mkdir -p $(BUILD_DIR)
	$(LATEXMK) -pdf -pvc -view=none -outdir=$(BUILD_DIR) -out2dir=$(PDF_DIR) $(TEX_FILE)

clean:
	$(LATEXMK) -c -outdir=$(BUILD_DIR) -out2dir=$(PDF_DIR) $(TEX_FILE)
	rm -rf $(BUILD_DIR)
	rm -f $(ROOT_AUX)
