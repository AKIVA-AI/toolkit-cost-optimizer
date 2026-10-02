# Releasing

Releases are cut by pushing a `vX.Y.Z` tag. CI then runs
[`.github/workflows/release.yml`](.github/workflows/release.yml), which:

1. runs the test suite;
2. builds the sdist and wheel, runs `twine check --strict` on them, installs
   the wheel in a clean virtual environment and fails if the installed version
   is not the tag's version;
3. creates a GitHub Release for the tag with both files attached, using the
   tag's `CHANGELOG.md` section as the release notes;
4. publishes the same two files to PyPI with
   [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC, no
   API token) from the `pypi` deployment environment, which only `v*` tags
   can use. If PyPI publishing is not enabled for the repository, this job is
   skipped and the tag still produces the GitHub Release.

The `build` job in `.github/workflows/ci.yml` runs the same build, `twine check`
and wheel install on every pull request, so a tag on a green `main` commit
builds the same way.

## Cutting a release

1. In a pull request, set the new version in `pyproject.toml` and `src/toolkit_cost_latency_opt/__init__.py` (`__version__`), and move the
   `CHANGELOG.md` entries for it under a `## [X.Y.Z] - YYYY-MM-DD` heading.
   In the same pull request:
   - if `src/toolkit_cost_latency_opt/data/model_prices.json` is stale, re-check
     every price on the provider pages and update its `sources` entries and
     `as_of`;
   - if the example data changed (`examples/generate.py`), update the numbers
     in the README's five-minute example; `tests/test_example.py` checks them.

   Merge when CI is green.
2. Tag that commit on `main` and push the tag:

   ```bash
   git switch main && git pull
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin vX.Y.Z
   ```

## Verifying a release

1. Actions → Release → the run for the tag: `Test`, `Build and check
   distributions` and `GitHub Release` succeeded, and `Publish to PyPI`
   succeeded (or shows as skipped if PyPI publishing is not enabled).
2. The GitHub Release has both files:

   ```bash
   gh release view vX.Y.Z --repo AKIVA-AI/toolkit-cost-optimizer
   # assets: toolkit_cost_optimizer-X.Y.Z-py3-none-any.whl and toolkit_cost_optimizer-X.Y.Z.tar.gz
   ```

3. PyPI: <https://pypi.org/project/toolkit-cost-optimizer/> shows version X.Y.Z with the
   same two files. Each file's page shows provenance (an attestation) naming
   `AKIVA-AI/toolkit-cost-optimizer` and `release.yml`.
4. A clean install works:

   ```bash
   python -m venv /tmp/rel && . /tmp/rel/bin/activate
   pip install "toolkit-cost-optimizer==X.Y.Z"
   python -c "import importlib.metadata as m; print(m.version('toolkit-cost-optimizer'))"   # X.Y.Z
   toolkit-opt --help
   ```
