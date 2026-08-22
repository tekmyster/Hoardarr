# Versioning

Hoardarr uses semantic versions below `1.0.0` while the product is in beta.

- Beta 1 releases use the current pre-1.0 feature line. The repository is presently on
  `0.3.x`; the beta label does not force a particular minor component.
- Every shipped change increments the patch component unless it intentionally starts a new beta feature line.
- `backend/pyproject.toml` is the authoritative release version.
- The Python package, frontend package, lock files, API, and visible UI must report the same version.
- Release verification fails when version metadata is inconsistent.

Do not deploy an unversioned hotfix. Increment the version, update synchronized metadata, run the release and application tests, and then deploy it.
