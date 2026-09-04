# DONE CONDITIONS (Tier 1)

Completion policy: no Tier 1 item is marked done unless (1) the exact command in this file is run and (2) the actual command output is pasted into `PROGRESS.md`.

Format: `item_id | verification_command | expected_output`

`t1-0-kind-harness-gate | bash scripts/smoke_test.sh --check harness | output contains "KIND_CLUSTER_READY", "METRICS_SERVER_READY", "KIND_IMAGE_LOADED", and "HPA_UTILIZATION_PRESENT"; output does not contain "<unknown>" in the hpa utilization line; output contains "METRIC_CONTRACT_VERIFIED"`

`t1-a-coldstart | bash scripts/smoke_test.sh --check coldstart | log contains "PODS_AT_ZERO_CONFIRMED", then "READY_REPLICAS_MATCH_DECLARED", then "LOAD_START t0=" in this exact order`

`t1-b-assertions | bash scripts/smoke_test.sh --check assertions && bash scripts/smoke_test.sh --negative-test fixed-replica-assert && bash scripts/smoke_test.sh --negative-test empty-metrics-column && bash scripts/smoke_test.sh --negative-test missing-locust-hpa && bash scripts/smoke_test.sh --negative-test hpa-never-scaled | positive: DECLARED_REPLICAS_FROM_SPEC; negatives: REPLICA_BELOW_DECLARED, empty column, missing locust, HPA_NEVER_SCALED`

`t1-c-fixed-metrics | bash scripts/smoke_test.sh --check fixed-metrics && bash scripts/smoke_test.sh --check error-rate-positive | fixed-metrics: FIXED_METRICS_REQUIRED_COLUMNS_POPULATED and ERROR_RATE_COLUMN_POPULATED with error_rate=0.0 rows; error-rate-positive: ERROR_RATE_NONZERO_VERIFIED and non_zero>=1 in collector output`

`t1-c-label-isolation | bash scripts/smoke_test.sh --check label-isolation --mode fixed --both-deployments-up && bash scripts/smoke_test.sh --negative-test label-isolation | positive: LABEL_ISOLATION_VERIFIED and OPPOSITE_ARM_SERIES=0; negative: LABEL_ISOLATION_FAILED`

`t1-d-locust-authority | bash scripts/smoke_test.sh --check locust-authority && bash scripts/smoke_test.sh --negative-test missing-locust-fixed && bash scripts/smoke_test.sh --negative-test missing-locust-hpa | positive: LOCUST_FIXED_STATS_FOUND, LOCUST_HPA_STATS_FOUND, REQUEST_AUTHORITY=LOCUST; negatives: locust_fixed_stats.csv absent, locust_hpa_stats.csv absent`

`t1-e-preflight-traps | bash scripts/smoke_test.sh --check preflight-traps --env-file .env | output contains PREFLIGHT_PASS, TRAP_SCENARIO_PASS for normal/error/sigint, TRAP_CLEANUP_IDEMPOTENT, NEGATIVE_CLUSTER_VERIFICATION_PASS, PROJECT_CLUSTER_VERIFICATION_REQUIRED, TRAP_CLEANUP_VERIFIED`

`t1-a2-synthetic-rename-minimal | python3 synthetic/generate_synthetic_data.py --help 2>&1 | output begins with "SYNTHETIC DATA GENERATOR"; file path "analysis/simulate_results.py" no longer exists; file path "synthetic/generate_synthetic_data.py" exists`

`t1-f-full-smoke-suite | bash scripts/smoke_test.sh --full | output contains "SMOKE_SUITE_PASS"; output contains "NEGATIVE_ASSERTION_TEST_PASS"; output contains "ALL_TIER1_ASSERTIONS_EXERCISED"`

`t1-i-handoff | bash scripts/smoke_test.sh --check handoff-docs | output contains "HANDOFF_MD_PRESENT" and "HANDOFF_COMMAND_ORDER_VALIDATED" and "HANDOFF_TRUST_CHECKS_PRESENT"`
