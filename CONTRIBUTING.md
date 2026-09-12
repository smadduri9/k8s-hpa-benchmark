# Contributing

This repository is a completed personal benchmark. There is no cluster to re-run against. Open an issue before sending a pull request.

CI also verifies the self-contained v1.1 evidence, tests publication calculations, and checks generated documentation for drift. Existing offline checks include: Wilcoxon self-test, the metrics contract against fixture CSVs, the repo-path quoting audit, and analysis module imports. Shape validation and the benchmark itself require a cluster and are not in CI.

## Zenodo archive (DOI)

The GitHub repository is the working copy. A Zenodo archive supplies a DOI for citation. `CITATION.cff` holds the metadata Zenodo will import.

Click-by-click, once, from an account that can administer `smadduri9/k8s-hpa-benchmark`:

1. Sign in at [https://zenodo.org](https://zenodo.org) with the GitHub account that owns the repo (or an account that GitHub has granted access).
2. Open [https://zenodo.org/account/settings/github/](https://zenodo.org/account/settings/github/). Authorize Zenodo if GitHub asks.
3. Find `smadduri9/k8s-hpa-benchmark` in the repository list. Flip the switch to on. Zenodo creates a webhook on the repo.
4. On GitHub, open the repository, then Releases, then Draft a new release. Tag `v1.0.0` (or the next unused version). Title the release. Publish it.
5. Wait for the webhook. Zenodo will show a new deposit under [https://zenodo.org/deposit](https://zenodo.org/deposit) with a reserved DOI (`10.5281/zenodo.NNNNNNN`).
6. Open that deposit. Confirm citation metadata against `CITATION.cff`. Describe the archive as mixed-license: repository code is MIT; bundled data derivatives retain the source terms in `artifacts/v1.1/SOURCE_NOTICES.md`. Do not label third-party data as MIT. Publish the deposit.
7. Copy the DOI. Add it to `CITATION.cff` as `doi: 10.5281/zenodo.NNNNNNN` and to the README citation line, then commit.

Do not create the Zenodo deposit by uploading a zip by hand. The GitHub release webhook is the integration this file describes. Subsequent tagged releases mint new version DOIs under the same concept DOI.

A DOI is not assigned until step 6. Until then, cite the GitHub URL in `CITATION.cff`.
