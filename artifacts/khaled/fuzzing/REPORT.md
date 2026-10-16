# Khaled — PDF upload fuzzing

## Purpose and scope
Test whether malformed PDF uploads are handled cleanly by
POST /api/upload-document.

The harness uses valid authentication and a valid .pdf filename so
mutations reach PDF validation. Each input uses temporary storage
and a fresh SQLite test database.

## Method
Atheris mutates a valid 855-byte PDF seed and uses Python coverage
feedback to select useful inputs.

The harness checks:
- Responses are 201, 400, or 413.
- Accepted uploads preserve their bytes and reported size.
- Uploaded files remain within the intended user directory.
- No unexpected files or database records remain.
- The original document and an outside sentinel remain unchanged.

Configuration: maximum input length 4096 bytes, per-input timeout
10 seconds, campaign time budget 600 seconds, random seed 1301.

## Finding and fix
Campaign 01 stopped after 712 executions with exit status 77.
A mutated PDF caused pymupdf.mupdf.FzErrorFormat:
"malformed page tree" when loading page 0.

The API returned HTTP 500. Replaying the saved input reproduced
the response. The harness stopped deliberately on its assertion;
this was not evidence that the application process crashed.

The validator did not catch FzErrorFormat. Adding it to the
validator's exception handlers makes this input return HTTP 400
through the existing rejection and cleanup path.

The regression test failed before the fix and passed afterwards.
It checks the response and unchanged storage/database state.

## Validation
- Original file-path tests: 86 passed.
- File-path tests including the regression: 87 passed.
- Complete API test directory after the fix: 192 passed.
- Original failing input replayed successfully after the fix.
- Campaign 02: 26,450 executions in 601 seconds, exit status 0.
  No additional harness failures were detected.
- Final Atheris counters: cov 608, ft 625.
  These are instrumentation counters, not coverage percentages.

## Evidence
baseline/ contains the environment, starting revision and baseline logs.
campaign-01/ contains discovery, diagnosis and before/after logs.
campaign-02/ contains the follow-up campaign and API test results.
findings/ preserves the original failing input.
corpus/ preserves the seed and generated inputs.
The regression fixture is server/test/api/fixtures/malformed-page-tree.pdf.

## Limits
This campaign covers PDF bytes with a fixed valid filename and
authentication. It does not cover other endpoints, authentication
fuzzing, large uploads, concurrency, production MariaDB or deployment.
Native MuPDF code was not built with sanitizer instrumentation.
Passing checks do not establish absence of vulnerabilities.

## Setup and reproduction
Run from the repository's server directory using Linux/WSL and Python 3.12.
The recorded dependency versions are in baseline/environment.txt.

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -e ".[dev]"
python -m pip install atheris

# Verify the regression test.
python -m pytest test/api/test_file_paths.py::test_upload_rejects_malformed_page_tree -v

# Replay the original failing input through the harness.
python -m fuzzing.fuzz_upload \
  ../artifacts/khaled/fuzzing/findings/crash-3d957d1cde31c0fda1bdf167fca856b564083bf1

# Run all API tests with a local test key.
SECRET_KEY=local-api-test-key-only python -m pytest test/api -v
```

On the fixed code, the regression test and saved-input replay pass.
On the original code, this input produced HTTP 500 and failed the harness.

## Work journal — 16 October 2026
1. Established the baseline: 86 file-path tests passed.
2. Built an isolated upload harness and completed a 100-input trial.
3. Discovered a malformed-page-tree failure after 712 executions.
4. Reproduced it and added a regression test that failed before the fix.
5. Added handling for FzErrorFormat; the regression test then passed.
6. Completed 26,450 follow-up executions without another detected failure.
7. Verified all 192 API tests, corrected lint issues, and preserved evidence.

AI assistance was used for harness development, diagnosis, the fix,
verification, and documentation.
