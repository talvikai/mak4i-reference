# Conformance evidence

This page maps the MAK4I Reference implementation to the acceptance criteria
in the protocol's `CONFORMANCE.md`, at the revision pinned in
[`PROTOCOL_REVISION`](../PROTOCOL_REVISION) (`talvikai/mak4i-protocol` commit
`4a690268f5aca6a4321f3598c21c391b62f15a37`, protocol version 0.2). The
Reference claims the profiles reported by `mak4i_whoami.protocol.profiles`
(`src/mak4i/protocol.py`): **`server-project-records`** plus
**`transport-stdio`** (stdio) or **`transport-http-credential`** (HTTP). It
does not claim `transport-http-oauth`. This is self-assessed evidence: no
shared conformance suite or certification process exists yet, so nothing here
makes the Reference "MAK4I Certified". Run the suite with `uv run pytest`.
`tests/test_protocol_conformance.py` validates live interface output (MCP
over HTTP with a credential and with OAuth, the OAuth metadata endpoints, the
admin event log) against the pinned protocol JSON Schemas. It fails, rather
than skips, when the pinned schemas are missing (`scripts/fetch_protocol.sh`).

Status values: **Met** means a test asserts the behavior. **Met (no
dedicated test)** means the behavior is in the code (named in the row) but no
test asserts it. **Partial** means part of the criterion lacks evidence or
behavior; the row says which part. **Not met** means the behavior or evidence
is missing.

Test paths are relative to `tests/` and use `file.py::test_name`.

## Server: Project Records (`server-project-records`)

### MAK-0005 Part A: project records

| ID | Criterion (short) | Evidence | Status |
|---|---|---|---|
| P1 | Create starts lineage at 1.0, `lineage_id = artifact_id` | `test_api.py::test_create_artifact_stores_and_returns_it` | Met |
| P2 | Supersede of non-head → `artifact_not_active`, nothing written | `test_api.py::test_supersede_already_superseded_artifact_is_rejected_and_writes_nothing`; `test_subject_key.py::test_superseding_an_already_superseded_version_still_fails_clearly` | Met |
| P3 | Concurrent supersessions: one wins, other `stale_head`, no partial write | `test_conflicts.py::test_concurrent_supersessions_of_one_head` (5 threads: exactly one succeeds, one active head, no integrity errors); `test_api.py::test_supersede_artifact_rejects_concurrent_modification_and_writes_nothing`. A loser that reads after the winner commits gets `artifact_not_active`, which is also correct. | Met |
| P4 | Versions immutable except lifecycle pointers | `test_conflicts.py::test_select_winner_withdraws_the_others_and_keeps_the_winner` (winner byte-identical, loser content and key preserved); `test_conflicts.py::test_separate_subjects_assigns_every_candidate` (old version keeps its key in history); `test_models.py::test_artifact_is_immutable` | Met |
| P5 | Subject key NFC + trim, carried forward, change → `subject_key_change`, release recorded, no reclaim | `test_conflicts.py::test_subject_keys_are_nfc_normalized`; `test_subject_key.py::test_subject_key_is_trimmed_and_blank_is_rejected`, `::test_supersede_preserves_subject_key_when_omitted_or_restated`, `::test_supersede_cannot_change_subject_key_and_writes_nothing`, `::test_released_lineage_cannot_reclaim_its_subject`, `::test_release_outside_a_conflict_still_works` | Met |
| P6 | Legacy versions readable, never rewritten | `test_subject_key.py::test_rc2_artifacts_without_subject_key_load_resolve_and_supersede`, `::test_unset_new_fields_are_omitted_from_storage_so_rc2_can_still_read_them`; `test_agent_identity.py::test_legacy_records_without_provenance_stay_readable`; `test_conflicts.py::test_legacy_records_without_subject_key_never_conflict` | Met |
| P7 | Integrity errors reported, never guessed | `test_resolver.py::test_zero_active_lineage_fails_closed`, `::test_multiple_active_lineage_fails_closed`, `::test_dangling_supersedes_pointer_fails_closed`, `::test_dangling_superseded_by_pointer_fails_closed`; `test_context_builder.py::test_build_carries_conflicts_and_integrity_errors` | Met |
| P8 | Records validate against `project-record.schema.json` | `test_protocol_conformance.py::test_reference_interfaces_conform_to_the_protocol_schemas` (create, supersede, merge output) | Met |
| P9 | Server-assigned fields rejected; omitted and `null` treated alike | `test_agent_identity.py::test_forged_identity_arguments_change_nothing` (`created_by`, `provenance`, etc.); `test_protocol_alignment.py::test_advertised_input_schemas_are_closed`, `::test_unknown_arguments_are_validation_errors_and_audited`; omitted and `null` read alike: `test_subject_key.py::test_unset_new_fields_are_omitted_from_storage_so_rc2_can_still_read_them`, `test_agent_identity.py::test_legacy_records_without_provenance_stay_readable` `test_protocol_alignment.py::test_rejected_create_inputs` (supplied `version`), `::test_null_optional_fields_are_treated_as_omitted`. | Met |
| P10 | Every non-legacy version carries authenticated provenance | `test_agent_identity.py::test_two_agents_with_separate_grants_write_attributable_records`, `::test_agents_can_write_through_oauth_and_are_still_the_author`; schema check of `provenance` in `test_protocol_conformance.py::test_reference_interfaces_conform_to_the_protocol_schemas` | Met |
| P11 | Failed supersession leaves lineage unchanged, logged in security log | `test_engine_audit_wiring.py::test_supersede_rejected_logs_supersede_rejected_event` (`SUPERSEDE_REJECTED` + `WRITE_REFUSED`, shared correlation id); `test_api.py::test_supersede_already_superseded_artifact_is_rejected_and_writes_nothing` | Met |
| P12 | Write ordering never leaves two active versions; headless lineage is an integrity error | `test_resolution_crash_recovery.py::test_crash_after_every_write_never_exposes_a_partial_result` (crash after every write, including merge's supersede); `test_api.py::test_check_integrity_reports_zero_active_lineage`. Plain `supersede_artifact` marks the old head superseded before it writes the successor (`api.py`), so a crash leaves zero active versions, never two. No crash is injected into plain supersede. | Met |
| P13 | Subject keys > 512 chars rejected | `test_conflicts.py::test_subject_keys_are_nfc_normalized` (513 chars rejected by `normalize_subject_key`, which create, supersede and separation call); tool schema `maxLength: 512` in `mcp_server.py` Over MCP: `test_protocol_alignment.py::test_rejected_create_inputs` (513 chars → `validation_error`). | Met |
| P14 | `withdrawn` set only by resolution, terminal | `test_conflicts.py::test_select_winner_withdraws_the_others_and_keeps_the_winner`, `::test_discovery_defaults`. Only `MAK4IEngine._withdraw` (resolution) writes `withdrawn`, and supersede requires `status == active` (`ArtifactNotActiveError`). No test tries to supersede a withdrawn version. | Met (no dedicated test) |
| P15 | Head half-superseded by pending resolution is not `zero_active` | `test_resolution_crash_recovery.py::test_crash_after_every_write_never_exposes_a_partial_result` (`integrity_errors == []` at every crash point) | Met |
| P16 | Duplicate `artifact_id` → `artifact_exists`, nothing written | `test_protocol_alignment.py::test_duplicate_create_is_artifact_exists`; `test_api.py::test_create_artifact_rejects_duplicate_id`; `test_local_json_store.py::test_put_new_rejects_duplicate_id` | Met |

### MAK-0004 Part A: subject conflicts

| ID | Criterion (short) | Evidence | Status |
|---|---|---|---|
| C1 | Same-lineage update supersedes, no conflict | `test_conflicts.py::test_same_lineage_update_supersedes_without_conflict`; `test_subject_key.py::test_explicit_supersede_changes_only_its_lineage_and_never_self_conflicts` | Met |
| C2 | Different subjects coexist; shared tags never conflict | `test_conflicts.py::test_different_subjects_and_shared_tags_coexist`; `test_subject_key.py::test_same_subject_key_but_different_artifact_types_do_not_conflict`, `::test_partially_overlapping_tags_do_not_conflict` | Met |
| C3 | Two lineages on one subject conflict; no newest-wins | `test_conflicts.py::test_competing_red_and_yellow_records_form_an_explicit_conflict` | Met |
| C4 | Third contributor extends same `conflict_id` | `test_conflicts.py::test_a_third_contributor_extends_the_same_conflict` | Met |
| C5 | Identical content conflicts, `identical_content: true` | `test_conflicts.py::test_identical_content_still_conflicts_and_says_so` | Met |
| C6 | Filters never hide one side | `test_conflicts.py::test_filters_never_hide_one_side_of_a_conflict` | Met |
| C7 | select_winner / merge / separate_subjects effects + complete record | `test_conflicts.py::test_select_winner_withdraws_the_others_and_keeps_the_winner`, `::test_merge_creates_one_authoritative_version`, `::test_separate_subjects_assigns_every_candidate`; `resolution-record` schema check in `test_protocol_conformance.py::test_reference_interfaces_conform_to_the_protocol_schemas` | Met |
| C8 | Incomplete or colliding separation rejected | `test_conflicts.py::test_separate_subjects_must_assign_not_dismiss` | Met |
| C9 | No `resolve` or no `read` → denied, nothing changes | `test_conflicts.py::test_resolution_needs_resolve_and_read` | Met |
| C10 | Stale set → `conflict_changed`; second → `conflict_not_open`; identical retry returns same record | `test_conflicts.py::test_stale_candidate_set_is_rejected`, `::test_retries_are_idempotent_and_second_resolutions_refused` | Met |
| C11 | Concurrent resolutions: exactly one completes | `test_conflicts.py::test_concurrent_resolutions_complete_exactly_once` | Met |
| C12 | New contribution after resolution → new `conflict_id` | `test_conflicts.py::test_new_contribution_after_resolution_is_a_new_conflict` | Met |
| C13 | Release during open conflict → `subject_in_conflict` | `test_conflicts.py::test_release_during_open_conflict_is_rejected`; `test_subject_key.py::test_release_during_an_open_conflict_is_rejected_and_resolution_settles_it` | Met |
| C14 | History keeps every version and resolution record | `test_conflicts.py::test_complete_history_is_preserved` | Met |
| C15 | Second client sees same conflicted/resolved state | `test_conflicts.py::test_conflict_and_resolution_seen_identically_from_another_client` (credential agent, OAuth agent, OAuth human) | Met |
| C16 | Reserve before effects; reserved write → `conflict_changed`; failed reservation aborts with no effect | `test_conflicts.py::test_reservation_failure_aborts_with_no_semantic_effects`, `::test_writes_to_reserved_heads_are_rejected`, `::test_candidate_change_mid_resolution_aborts_and_is_recorded` | Met |
| C17 | Crash after any write never shows resolved while pending; recovery matches | `test_resolution_crash_recovery.py::test_crash_after_every_write_never_exposes_a_partial_result`; `test_conflicts.py::test_interrupted_resolution_is_seen_as_resolving_and_recovered` | Met |
| C18 | `conflict_id = "cfl_" + sha256(org\nproject\ntype\nkey\ngeneration)[:32]` | `test_conflicts.py::test_conflict_id_matches_the_protocol_example` derives the id of the protocol's own conflict example (`examples/conflict/valid-open-two.json`) and gets the same value, and a different generation gives a different id; `::test_new_contribution_after_resolution_is_a_new_conflict` | Met |
| C19 | `resolving` treated as conflicted | `test_conflicts.py::test_interrupted_resolution_is_seen_as_resolving_and_recovered`; `test_resolution_crash_recovery.py::test_crash_after_every_write_never_exposes_a_partial_result` | Met |
| C20 | Candidate fields incl. `provenance`, ordered by `artifact_id` | `test_conflicts.py::test_competing_red_and_yellow_records_form_an_explicit_conflict`; `conflict` schema check in `test_protocol_conformance.py::test_reference_interfaces_conform_to_the_protocol_schemas` | Met |
| C21 | Conflicted `get_current` carries "don't choose a side" instruction | `test_conflicts.py::test_competing_red_and_yellow_records_form_an_explicit_conflict`; `test_context_builder.py::test_build_wraps_resolution_result` (`STANDARD_INSTRUCTION`: "do not pick a candidate yourself") | Met |
| C22 | Unreadable candidates counted in `hidden_candidate_count`, never disclosed | `test_conflicts.py::test_competing_red_and_yellow_records_form_an_explicit_conflict` asserts `hidden_candidate_count == 0`. Access is per project, so every reader can read every candidate, and MAK-0004 §A6.4 says the count is 0 at this revision. | Met |
| C23 | `write` neither required nor sufficient; `resolve` + `read` is | `test_conflicts.py::test_resolution_needs_resolve_and_read` (read+write denied; resolve alone denied); `::test_conflict_and_resolution_seen_identically_from_another_client` (token scoped `mak4i:read mak4i:resolve`, without write, resolves) | Met |
| C24 | Merge carries `merged_from`, `resolution_id`; originals superseded/withdrawn | `test_conflicts.py::test_merge_creates_one_authoritative_version` | Met |
| C25 | Unknown id → `not_found`; resolved → `conflict_not_open` | `test_conflicts.py::test_retries_are_idempotent_and_second_resolutions_refused`; `::test_conflict_and_resolution_seen_identically_from_another_client` (MCP `conflict_not_open`) | Met |
| C26 | Same idempotency key with different parameters rejected | `MAK4IEngine.resolve_conflict` (`api.py`) treats a request as a retry only when the key, action, parameters and candidates all match. Any other request fails with `conflict_not_open` (§A8.4). No test reuses a key with different parameters. | Met (no dedicated test) |
| C27 | Resolution start/completion/abort/rejection audited with actor and correlation id | `test_conflicts.py::test_every_resolution_event_names_the_actor_and_correlation_id` (all four events, each with the resolving principal as `actor` and a `correlation_id`; start and end of one resolution share it); `::test_audit_records_resolution_lifecycle` | Met |

### MAK-0006: identity and access control

| ID | Criterion (short) | Evidence | Status |
|---|---|---|---|
| I1 | Agents need a valid, org-unique, immutable `agent_id`; others have none | `test_agent_identity.py::test_agent_principals_require_a_valid_agent_id`, `::test_agent_id_is_unique_per_organization_and_never_reused`, `::test_agent_id_is_immutable_and_rename_changes_only_the_display_name` | Met |
| I2 | Two agents with separate grants write records attributed to themselves | `test_agent_identity.py::test_two_agents_with_separate_grants_write_attributable_records` | Met |
| I3 | Identity arguments change nothing; mismatching claim → `identity_mismatch` | `test_agent_identity.py::test_forged_identity_arguments_change_nothing`, `::test_identity_cannot_be_injected_into_the_engine_from_tool_arguments`; `test_protocol_alignment.py::test_differing_identity_claim_is_identity_mismatch`; `test_security_dev_preview.py::test_10_spoofed_created_by_cannot_override_authenticated_identity` | Met |
| I4 | OAuth client names and `clientInfo` recorded only as unverified | `test_agent_identity.py::test_client_names_are_recorded_only_as_unverified_metadata`; `test_oauth.py::test_cimd_client_with_loopback_any_port` ("unverified" label) | Met |
| I5 | Effective permissions = grant ∩ ceiling | `test_oauth.py::test_scopes_never_widen_a_grant`, `::test_scope_ceiling_returns_insufficient_scope`; `test_api.py::test_a_valid_credential_confers_no_more_access_than_its_principals_own_grants` | Met |
| I6 | Revocation table holds per request | Revoked credential: `test_mcp_http_transport.py::test_mcp_endpoint_rejects_a_revoked_credential`. Revoked authorization: `test_oauth.py::test_admin_revocation_by_principal_and_client`. Deactivated principal: `test_oauth.py::test_deactivated_principal_loses_access_immediately`, `test_control_plane.py::test_authenticate_rejects_when_principal_deactivated`. Suspended org: `test_control_plane.py::test_authenticate_rejects_when_organization_suspended`. Removed grant: `test_oauth.py::test_removed_grant_takes_effect_on_the_next_request`. Archived project: `test_administration.py::test_archive_is_safe_and_stops_access`. | Met |
| I7 | Denials don't reveal project existence | `test_security_dev_preview.py::test_8_denial_and_search_leak_no_unauthorized_project_metadata`; `test_authz.py::test_require_denies_unknown_project_identically`, `::test_require_denies_cross_org_access_without_leaking_org` | Met |
| I8 | Owners administer only their org; members nothing | `test_administration.py::test_only_owners_of_the_organization_administer_it`, `::test_project_permissions_never_confer_admin_or_instance_rights`; `test_control_plane.py::test_member_cannot_administer`, `::test_owner_cannot_administer_another_organization` | Met |
| I9 | Every admin action → attributable, secret-free event | `test_administration.py::test_every_admin_action_is_attributable_and_secret_free`; `admin-event` schema check in `test_protocol_conformance.py::test_reference_interfaces_conform_to_the_protocol_schemas` | Met |
| I10 | Secrets shown once, never in list/show or logs | `test_cli.py::test_credential_list_never_prints_a_token_or_hash`; `test_control_plane.py::test_list_credentials_scoped_to_the_principals_own_org_and_hashes_not_raw_tokens`; `test_audit_logger.py::test_log_authenticate_never_carries_token_material`; `test_oauth.py::test_no_secret_reaches_the_logs`; `test_cli_init.py::test_init_does_not_log_the_raw_token` | Met |
| I11 | Orgs have stable id and status; suspension blocks everything, records unchanged | `test_administration.py::test_suspending_an_organization_stops_authentication`; `test_control_plane.py::test_authenticate_rejects_when_organization_suspended`. OAuth subjects are re-checked per request by `ControlPlane.active_principal`, which also checks the organization's status. Suspension changes only the organization's status. | Met |
| I12 | `type` equals `principal_type` | `test_agent_identity.py::test_whoami_reports_the_stored_agent_identity`, `::test_principal_model_exposes_principal_type` | Met |
| I13 | Last owner can't be deactivated; reactivation doesn't restore OAuth | `test_control_plane.py::test_cannot_deactivate_the_last_active_owner`; deactivation revokes OAuth authorizations: `test_oauth.py::test_deactivated_principal_loses_access_immediately`. There is no principal-reactivation operation, so revoked authorizations cannot come back. | Met |
| I14 | Rename changes neither ids nor recorded provenance | `test_agent_identity.py::test_agent_id_is_immutable_and_rename_changes_only_the_display_name` | Met |
| I15 | `bot_id`, if accepted, is an alias of `agent_id` | Conditional. `bot_id` is not accepted anywhere. Tool inputs are closed, so it is rejected as `validation_error` (`test_protocol_alignment.py::test_rejected_create_inputs`). | Met |
| I16 | Deactivated `agent_id` never reassigned; no implicit agents | `test_agent_identity.py::test_agent_id_is_unique_per_organization_and_never_reused`, `::test_human_oauth_use_needs_no_agent_and_creates_none` | Met |
| I17 | (also `transport-stdio`; see below) | `test_protocol_alignment.py::test_stdio_session_with_revoked_credential_gets_access_denied` | Met |
| I18 | Operator actions: `auth_method = operator`, principal or `null` for bootstrap | `test_protocol_alignment.py::test_instance_operator_events_have_no_principal`; `test_authz.py::test_operator_impersonation_is_recorded_in_context`; `test_cli_audit_log.py::test_cli_writes_and_refusals_reach_the_audit_log` | Met |
| I19 | No unauthenticated remote administration endpoint | `test_administration.py::test_no_remote_administration_endpoint` | Met |
| I20 | Nothing deletes principals, projects, grants, credentials, records | `test_administration.py::test_no_destructive_purge_exists`, `::test_archive_is_safe_and_stops_access` | Met |
| I21 | Owners retrieve admin events durably | `test_administration.py::test_events_are_durable_across_restarts` | Met |
| I22 | Every refused write → §8.6 security event; 403 scope refusal attributed | `test_protocol_alignment.py::test_unknown_arguments_are_validation_errors_and_audited` (validation), `::test_differing_identity_claim_is_identity_mismatch` (identity), `::test_scope_refusal_is_attributed_to_the_principal` (scope, 403), `::test_duplicate_create_is_artifact_exists`; `test_engine_audit_wiring.py::test_supersede_rejected_logs_supersede_rejected_event` (lifecycle); `test_cli_audit_log.py::test_cli_writes_and_refusals_reach_the_audit_log` (CLI). Grant and `stale_head` refusals go through the same `_audited_write` wrapper (`api.py`) but no test asserts them. Missing grant: `test_protocol_alignment.py::test_write_without_grant_is_a_refused_write_event`. | Met |
| I23 | External identity adapter, if provided, behaves | Conditional. The Reference provides no external identity adapter. | Met (no dedicated test) |

### MAK-0008: tool, error and catalog criteria

| ID | Criterion (short) | Evidence | Status |
|---|---|---|---|
| M15 | Tool errors carry §9 `error.code`; results validate against `mcp-tools.schema.json` | `test_protocol_conformance.py::test_reference_interfaces_conform_to_the_protocol_schemas` (every tool's output and six error cases validated) | Met |
| M17 | Withdrawn/superseded never current; search defaults to active; history complete | `test_conflicts.py::test_discovery_defaults`; `test_mcp_server.py::test_search_defaults_to_active_status`, `::test_supersede_then_history_shows_both_versions_oldest_first`; `test_discovery.py::test_default_status_filters_to_active` | Met |
| M19 | Session bound to its principal | `test_oauth.py::test_session_is_bound_to_its_principal` (another principal's request on the session → 404) | Met |
| M20 | Health/readiness reveal no data | `test_mcp_http_transport.py::test_health_endpoint_requires_no_auth` (body is exactly `ok`), `::test_ready_endpoint_exposes_no_configuration_or_secrets` | Met |
| M34 | Error text block; denials don't reveal existence; `correlation_id` links to server event | `test_protocol_alignment.py::test_engine_refusals_share_the_error_correlation_id`, `::test_unknown_arguments_are_validation_errors_and_audited`; `test_agent_identity.py::test_two_agents_with_separate_grants_write_attributable_records` (text block); `test_security_dev_preview.py::test_8_denial_and_search_leak_no_unauthorized_project_metadata` | Met |
| M35 | Unknown arguments → `validation_error`; differing identity → `identity_mismatch` | `test_protocol_alignment.py::test_unknown_arguments_are_validation_errors_and_audited`, `::test_differing_identity_claim_is_identity_mismatch`, `::test_input_constraints_are_enforced`, `::test_advertised_input_schemas_are_closed` | Met |
| M36 | Tool ↔ CLI mapping documented; no implementation-specific `mak4i_` tools | Mapping table in `docs/MVP_ARCHITECTURE.md` ("Tool ↔ CLI mapping"); `test_mcp_server.py::test_lists_exactly_the_ten_required_tools` | Met |
| M37 | `whoami` always has `scopes` (null for credentials) and `protocol` | `test_protocol_alignment.py::test_whoami_reports_protocol_and_scopes_key`; `test_oauth.py::test_principal_credentials_keep_working_with_oauth_enabled`; `mak4i_whoami` output schema check in `test_protocol_conformance.py::test_reference_interfaces_conform_to_the_protocol_schemas` | Met |

## Transport: stdio (`transport-stdio`)

| ID | Criterion (short) | Evidence | Status |
|---|---|---|---|
| I17 | stdio re-checks authentication per operation; revoked → `access_denied` | `test_protocol_alignment.py::test_stdio_session_with_revoked_credential_gets_access_denied`; `test_oauth.py::test_stdio_session_rechecks_its_credential_before_each_call` | Met |

## Transport: HTTP + credential (`transport-http-credential`)

| ID | Criterion (short) | Evidence | Status |
|---|---|---|---|
| M1 | Unauthenticated → 401 challenge (`realm` with OAuth disabled) | OAuth disabled: `test_protocol_alignment.py::test_credential_mode_challenge_uses_a_realm` (`Bearer realm="mak4i"`, and `error="invalid_token"` for a bad token); `test_mcp_http_transport.py::test_mcp_endpoint_rejects_missing_auth`. OAuth enabled: `test_oauth.py::test_unauthenticated_mcp_request_gets_resource_metadata_challenge` | Met |
| M7 | Expired/revoked token → 401 `invalid_token`, no fallback | `test_mcp_http_transport.py::test_mcp_endpoint_rejects_a_revoked_credential`; `test_oauth.py::test_invalid_token_of_either_kind_never_falls_back`, `::test_invalid_token_challenge_says_invalid_token`, `::test_expired_access_token_is_rejected_without_fallback`; `test_control_plane.py::test_authenticate_rejects_expired_credential`; `test_protocol_alignment.py::test_revoked_credential_failure_names_the_credential` | Met |
| M11 | Credentials work; malformed/duplicate/query-string authorization rejected | `test_oauth.py::test_principal_credentials_keep_working_with_oauth_enabled`, `::test_duplicate_authorization_headers_or_query_tokens_are_rejected`, `::test_bearer_scheme_is_case_insensitive`; `test_mcp_http_transport.py::test_mcp_endpoint_rejects_wrong_auth` | Met |
| M14 | No secret in logs or error messages | `test_oauth.py::test_no_secret_reaches_the_logs`, `::test_secrets_are_stored_only_as_hashes`; `test_audit_logger.py::test_log_authenticate_never_carries_token_material`; `test_settings.py::test_secrets_by_file_reference_and_never_printed`; `test_cli_init.py::test_init_does_not_log_the_raw_token` | Met |
| M18 | Plain HTTP only on loopback; remote requires HTTPS | The default bind is loopback (`test_cli_init.py::test_serve_http_defaults_to_loopback_host_and_shows_the_endpoint`). OAuth URLs must be HTTPS off loopback (`test_oauth.py::test_settings_validation`). The TLS bootstrap profile binds MAK4I to loopback (`test_bootstrap.py::test_tls_install_binds_mak4i_to_loopback_only`). However, the server does not refuse plain HTTP on a non-loopback bind in credential mode (`MAK4I_HOST=0.0.0.0` is allowed), and the `private-http` bootstrap profile allows a private-network bind with a warning (`test_bootstrap.py::test_private_http_bind_safety`). HTTPS is left to the deployment. | Partial |
| M21 | Foreign tokens rejected, never forwarded | `test_oauth.py::test_invalid_token_of_either_kind_never_falls_back` (JWT-shaped token → 401), `::test_token_for_another_resource_is_rejected_at_the_mcp_endpoint`. The server never forwards tokens to anything upstream. | Met |
| M22 | Advertised URLs from configuration; forwarded headers only from trusted proxies | Configured URLs: `test_oauth.py::test_metadata_urls_follow_a_configured_path_prefix`; `test_cli.py::test_credential_issue_uses_public_endpoint_and_omits_stdio_guidance`. Proxy trust: `test_protocol_alignment.py::test_forwarded_headers_are_trusted_only_from_configured_proxies` — with `MAK4I_TRUSTED_PROXIES` unset, forwarded headers are off for every peer (uvicorn's implicit loopback trust is disabled); otherwise only the listed proxies. `test_settings.py::test_self_hosted_http_with_oauth` validates the setting. | Met |
| M38 | Token/revocation/registration endpoints rate-limited; signing keys persisted and rotatable | With OAuth disabled, these endpoints do not exist. Tokens are opaque and stored hashed, so there are no signing keys. With OAuth enabled, `oauth/web.py` rate-limits `token`, `revoke` and `register`, but only the sign-in limiter has a test (`test_oauth.py::test_sign_in_is_rate_limited`). | Met (no dedicated test) |
| M39 | Configuration validated at startup; invalid → refuse to start | `test_settings.py::test_server_refuses_to_start_on_invalid_configuration`, `::test_every_problem_is_reported_at_once` | Met |

## Remaining MAK-0008 criteria: HTTP + OAuth (`transport-http-oauth`), not claimed

The Reference includes an OAuth authorization server, but it does not claim
this profile. The profile needs real-client interoperability evidence (M16)
first, and `mak4i_whoami` does not report it
(`test_protocol_alignment.py::test_whoami_reports_protocol_and_scopes_key`).

| ID | Criterion (short) | Evidence | Status |
|---|---|---|---|
| M2 | Metadata public, correct behind path prefix | `test_oauth.py::test_metadata_documents_are_public_and_consistent`, `::test_metadata_urls_follow_a_configured_path_prefix`; schema checks in `test_protocol_conformance.py::test_reference_interfaces_conform_to_the_protocol_schemas` | Met |
| M3 | Code + PKCE S256; wrong verifier, missing, `plain` fail | `test_oauth.py::test_full_authorization_code_flow_reaches_mcp_tools`, `::test_pkce_is_required_and_s256_only`, `::test_wrong_pkce_verifier_is_rejected` | Met |
| M4 | Bad redirect → error page; loopback port rule | `test_oauth.py::test_unregistered_redirect_shows_an_error_page_and_never_redirects`, `::test_unknown_client_shows_an_error_page`, `::test_redirect_uri_matching_rules`, `::test_cimd_client_with_loopback_any_port` | Met |
| M5 | Code reuse fails and revokes | `test_oauth.py::test_code_reuse_fails_and_revokes_the_authorization`, `::test_expired_code_is_rejected`, `::test_code_issued_to_another_client_is_rejected` | Met |
| M6 | Wrong `resource` → `invalid_target`; other-resource token rejected | `test_oauth.py::test_wrong_resource_is_invalid_target`, `::test_token_for_another_resource_is_rejected_at_the_mcp_endpoint` | Met |
| M8 | Refresh rotates; replay revokes | `test_oauth.py::test_refresh_rotates_and_replay_revokes_the_authorization`, `::test_token_events_distinguish_sign_in_from_refresh` | Met |
| M9 | Sign-in codes single use, expire, rate-limited, never bearer | `test_oauth.py::test_sign_in_code_is_single_use`, `::test_sign_in_code_expires`, `::test_sign_in_is_rate_limited`, `::test_sign_in_code_is_never_a_bearer_token` | Met |
| M10 | Consent page content; CSRF | `test_oauth.py::test_consent_page_names_principal_client_host_and_scopes`, `::test_consent_denied_redirects_with_access_denied`, `::test_csrf_is_enforced_on_sign_in` | Met |
| M13 | OAuth state survives restart | `test_oauth.py::test_oauth_state_survives_a_restart` | Met |
| M16 | Two independent real OAuth clients against reachable HTTPS | `test_oauth_sdk_client_e2e.py` is an in-process MCP Python SDK smoke test. By its own docstring it is not interoperability evidence. No recorded runs with real clients exist. | Not met |
| M23 | Sign-in codes local-only, ≥128 bits, hashed; no password exchange | `test_oauth.py::test_cli_oauth_commands` and `test_administration.py::test_no_remote_administration_endpoint` cover local-only issuance. The 140-bit codes and hashed storage come from `oauth/service.py` (`_SIGN_IN_LENGTH`, `code_hash`) and are not asserted by a test. | Partial |
| M24 | Browser session ≤ 15 min; agent can be resource owner | `test_oauth.py::test_browser_session_expires_on_the_server_clock`; `test_agent_identity.py::test_agents_can_write_through_oauth_and_are_still_the_author`. The default is 900 s and is not configurable through the environment. The settings constructor does not cap it (tests override it). | Met |
| M25 | Remembered consent limited to pre-registered clients | Consent is never remembered (`oauth/service.py`), so the restriction holds trivially. | Met (no dedicated test) |
| M26 | Cookie flags, `no-store`, `frame-ancestors 'none'` | `test_oauth.py::test_pages_forbid_framing_and_caching` | Met |
| M27 | Authorization response carries `iss` | `test_oauth.py::test_full_authorization_code_flow_reaches_mcp_tools`, `::test_pkce_is_required_and_s256_only` | Met |
| M28 | ≥128-bit hashed codes/tokens; access TTL 1 h default, max 24 h | `test_oauth.py::test_secrets_are_stored_only_as_hashes`, `::test_full_authorization_code_flow_reaches_mcp_tools` (`expires_in == 3600`), `::test_settings_validation` (TTL over the maximum rejected) | Met |
| M29 | Idle 7 d / absolute 30 d; refresh may narrow, never widen; principal re-checked | `test_oauth.py::test_idle_and_absolute_refresh_lifetimes_force_sign_in`, `::test_refresh_may_narrow_but_not_widen_scope` (widening only), `::test_deactivated_principal_loses_access_immediately`. No test exercises a successful narrowing refresh. | Partial |
| M30 | Revocation accepts access and refresh tokens; 200 for unknown | `test_oauth.py::test_revocation_endpoint` covers an unknown token and a refresh token. No test revokes an access token at the endpoint. | Partial |
| M31 | No `scope` → `mak4i:read mak4i:write`; credentials have no ceiling | `test_oauth.py::test_principal_credentials_keep_working_with_oauth_enabled` (`scopes` null). The default scope is set in `oauth/service.py` (`DEFAULT_SCOPES`), but every test passes `scope` explicitly. | Partial |
| M33 | Owners list/disable clients, revoke one/principal/client authorizations, each audited | `test_oauth.py::test_admin_revocation_by_principal_and_client`, `::test_cli_oauth_commands`, `::test_other_organizations_owner_cannot_administer`. No test revokes a single authorization, and no test asserts the audit events. | Partial |

## OAuth options: CIMD (`oauth-cimd`) and DCR (`oauth-dcr`), not claimed

| ID | Criterion (short) | Evidence | Status |
|---|---|---|---|
| M12 | Only enabled modes advertised; CIMD refuses non-public addresses | `test_oauth.py::test_dcr_is_off_by_default`, `::test_dcr_registers_public_clients_only_when_enabled`, `::test_cimd_disabled_is_not_advertised_and_refused`, `::test_cimd_refuses_non_public_addresses`, `::test_cimd_url_rules` | Met |
| M32 | Pre-registered clients org-bound; CIMD fetch limits; DCR public only | `test_oauth.py::test_pre_registered_client_is_limited_to_its_organization`, `::test_dcr_registers_public_clients_only_when_enabled`, `::test_cimd_document_validation`. The CIMD fetch timeout, size limit, no-redirect rule and cache are in `oauth/cimd.py` but are not tested, because the tests use a fake fetcher. | Partial |

## Not applicable or not implemented

- **K1–K12 (`client-sdk`)** apply to clients and SDKs, not servers.
  **A1–A4 (`artifact`)** apply to MAK-0001 packaging documents. Neither
  applies to this server.
- **MAK-0009** (distributable artifacts, resources, rules) is an
  architecture-defined standard with no criteria at this revision. The
  Reference does not implement it, does not emit `resource_id` or `rule`,
  and does not claim support for it.
- **MAK-0002, MAK-0003 and MAK-0007** are Draft (In Progress) and have no
  criteria. The Reference does not implement them.

## Known limitations

These items are Partial or Not met:

- **M18 (claimed profile):** in credential mode the server accepts plain
  HTTP on a non-loopback bind. HTTPS for remote deployments depends on the
  deployment (TLS proxy or platform), not on the server.
- **M16 (OAuth, not claimed):** there is no evidence from two real clients
  against an HTTPS deployment. This is why `transport-http-oauth` is not
  claimed.
- **M23, M29, M30, M31, M33 (OAuth, not claimed):** each has a sub-behavior
  that is implemented but not tested: sign-in code entropy and hashing,
  narrowing refresh, revoking an access token, the default scope, and
  single-authorization revocation with audit.
- **M32 (CIMD/DCR option, not claimed):** the CIMD fetch timeout, size,
  no-redirect and cache limits are not tested.

Criteria marked Met (no dedicated test) in the claimed profiles still lack a
direct assertion: P14, C26, I23 (conditional; no external identity adapter
exists) and M38 (OAuth endpoint rate limits). M18 is the one Partial criterion
in the claimed profiles, so the `transport-http-credential` claim in
`src/mak4i/protocol.py` holds only for deployments that terminate TLS in
front of the server (as the bootstrap TLS profile does) or bind to loopback.
