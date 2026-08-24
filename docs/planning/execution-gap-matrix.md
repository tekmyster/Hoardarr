# Hoardarr execution gap matrix

Generated from the canonical unified roadmap by `scripts/build-execution-gap-matrix.py`.
The CSV is authoritative for per-task execution fields; the roadmap remains authoritative for requirement wording.

## Queue summary

- Total tasks: 253
- IN PROGRESS: 2
- NOT STARTED: 104
- PHYSICAL VALIDATION PENDING: 1
- VERIFIED: 87
- VERIFIED IN ISOLATION: 59

## Active selection rule

Select the highest-priority task whose dependencies are VERIFIED, VERIFIED IN ISOLATION, or otherwise sufficient for software work. A physical or external blocker applies only to the irreducible validation boundary and never blocks independent software work.

## Machine-readable matrix

See [execution-gap-matrix.csv](execution-gap-matrix.csv). It tracks Task ID, priority, description, dependencies, status, implementation/UI/test/deployment evidence, hardware validation, external blocker, and next action for every roadmap row.
