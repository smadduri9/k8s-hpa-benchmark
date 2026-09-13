# Contributing

This is a completed personal benchmark with a retained public evidence package. The original cluster has been deleted. Open an issue to discuss a proposed change before sending a pull request.

## Validate a change

Follow [REPRODUCE.md](REPRODUCE.md#verify-published-results) to set up Python 3.14 and the repository virtualenv. Run the offline verifier, publication tests and documentation checks before submitting changes. CI also runs the Wilcoxon self-test, metric-contract fixtures, path-quoting checks and analysis imports.

Keep current findings in [RESULTS.md](RESULTS.md), the overview in [README.md](README.md), and historical material in [docs/archive/](docs/archive/README.md). Never fill missing measurements with estimates. Use the literal `MISSING` and explain exclusions.

## Evidence and figures

The [v1.1 package](artifacts/v1.1/README.md) is self-contained. Its verifier is authoritative for this publication; the general experiment aggregator does not apply its exclusions. Preserve raw evidence bytes and the [evidence attributes](.gitattributes).

The [text generator](scripts/generate_publication_text.py) maintains bounded metric blocks in README, RESULTS and the existing Pages document. The [figure generator](scripts/generate_public_figures.py) reads only canonical summaries. After a deliberate presentation edit, run the text check and inspect the rendered figures. The architecture and measurement-guards SVGs are hand-maintained diagrams, separate from generated measurement figures.

Do not regenerate evidence checksums merely to make changed inputs pass. Evidence or exclusion changes require an explicit explanation, corresponding summaries and rejection tests. New cloud measurements require the separate rerun workflow and a new publication scope.

## Citation and release records

[CITATION.cff](CITATION.cff) remains the v1.0.0 citation record. A directory named `artifacts/v1.1` is an evidence version, not a release or a new DOI. Release and archive changes are separate maintainer operations. No DOI value is present in the current citation file, so this repository does not display an unverified DOI badge.

Repository code is MIT licensed. Third-party trace derivatives keep their [source terms](artifacts/v1.1/SOURCE_NOTICES.md); they must not be relabeled as MIT.
