# HackTrack database migration and performance plan

**Goal:** Move HackTrack from Neon to the existing Supabase PostgreSQL database while preserving application data, and address evidenced page-loading failures.

**Architecture:** Retain the existing psycopg/PostgreSQL data layer and application authentication. Assess source and target independently before copying data; switch the application connection only after verification. Diagnose API and frontend paths with reproducible evidence before changing them.

## Tasks

- [x] Inventory source schema, migration history, application configuration and target state without exposing credentials. Establish a repeatable performance baseline and reproduce reported failures where possible.
- [x] Fix confirmed shared connection and request-path failures with regression tests. Verify the focused tests and compare request behavior under the same conditions.
- [x] Apply the existing schema to an empty target with RLS and restricted API-role access; copy and compare all source rows, preserve the source, and retain the current connection for rollback until cutover.
- [ ] Configure the application for Supabase with appropriate connection pooling and update setup documentation. Keep credentials outside version control.
- [ ] Implement additional performance improvements supported by investigation and measurements; test affected flows and remeasure.
- [ ] Run the relevant backend and frontend checks, review the complete change, and report measured results and any remaining limitations.

## Safety and verification

- Keep credentials private and preserve Neon data until the Supabase copy is verified.
- Account for concurrent writes during the copy and confirm the destination is suitable before any data changes.
- Validate authentication, user isolation, sessions, and key user-facing workflows after switching.
- Preserve a clear rollback path to the original database; do not delete source data.

## Current state

Supabase contains the verified source snapshot. The local DATABASE_URL now points to Supabase and the Neon entry has been removed. The hosted deployment still needs its configuration checked and updated using the correct Vercel account. Backend tests, frontend lint and production build pass. Dashboard and profile smoke checks passed using a temporary real database session; Google sign-in and the hosted deployment still need verification. Future migrations that create tables must enable RLS and revoke Data API grants in the same transaction.
