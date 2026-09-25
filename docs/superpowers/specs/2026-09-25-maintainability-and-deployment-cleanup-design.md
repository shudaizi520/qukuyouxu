# Maintainability and Deployment Cleanup Design

## Intent

Reduce accumulated maintenance debt without changing the visible product,
recommendation results, stored music-library state, or supported upgrade path.
The deployed TrueNAS application must run the exact code contained in the
published image, while `/data` remains the only host-mounted application state.

Success means:

- the live app keeps its current pages, themes, routes, schedules and data;
- the TrueNAS workload has one `/data` mount and no source-code overlays;
- code and assets proven to have no runtime consumer are removed;
- temporary response aliases that no current client reads are retired;
- large presentation and route modules gain clearer ownership boundaries without
  changing their public behavior;
- the complete Python, JavaScript, visual-contract and container checks remain
  green;
- a new release image is published and verified on the user's TrueNAS instance.

## Constraints

- Do not delete or rewrite `/data`, Plex playlists, QQ/Plex credentials, playback
  history, user profiles, or library metadata.
- Do not remove migration readers that are still exercised by stored-state or
  upgrade tests.
- Do not redesign the interface. CSS work is behavior-preserving consolidation.
- Do not introduce a front-end build tool, JavaScript framework, database
  migration framework, or new runtime dependency.
- Preserve Python 3.11 and 3.12 support and the existing multi-architecture image.
- Every behavior change follows RED-GREEN TDD. Pure deletion must first gain a
  regression test proving that no page or runtime entry point consumes it.
- Deployment changes are staged so the current custom-app configuration can be
  restored if the image-only workload does not pass health and page checks.

## Architecture

### 1. Reproducible deployment

The container image is the only source of executable code. TrueNAS retains the
existing read-write `/data` bind mount and application environment, port,
resource and security settings. The 44 read-only mounts into
`site-packages/helper` are removed from the custom-app YAML. Before updating,
the current YAML and volume inventory are captured as a rollback artifact.

The replacement is verified in this order: container reaches `RUNNING`,
`/healthz` reports the release version, all six HTML pages render that version,
static assets load, login remains available, resource limits remain 1 CPU / 768
MiB / 128 PIDs, and only `/data` remains mounted from the host.

### 2. Proven dead-code cleanup

`theme_mixin.py` is removed only after a test walks production imports and proves
that no packaged engine or entry point depends on `ThemeMixin`. `home.css` is
removed together with its static allow-list entry after a page-level asset test
proves that every allowed stylesheet is referenced by a shipped HTML page.

The response aliases `positive_tracks` and `negative_tracks` are removed from
the status payload because shipped JavaScript uses `preferred_tracks` and
`cooled_tracks`. Stored-state compatibility keys and the legacy Plex bootstrap
route remain until a separate data-lifecycle decision proves they are safe to
retire.

### 3. CSS ownership consolidation

The cascade remains plain CSS. Existing selectors are assigned to one of four
owners: global product primitives, theme/design tokens, management workspace,
or playlist/player presentation. Later duplicate overrides in `product.css` are
folded into their canonical owner only when computed-style and screenshot tests
prove identical behavior for light, paper and night themes.

The first cleanup targets exact duplicate or fully superseded declarations and
page-specific blocks that already have a dedicated stylesheet. It does not aim
for a particular line-count reduction. Correct theme contrast, settings layout,
playlist column alignment and immersive-player controls are release gates.

### 4. Python responsibility consolidation

Refactoring is limited to boundaries already implied by the code:

- application construction and cross-cutting middleware remain in `web.py`;
- page/static route registration moves to a focused web-surface module;
- Plex identity/lifecycle routes currently grouped under
  `daily_mix_v036.py` gain a stable, responsibility-based module name while the
  old import path remains as a thin compatibility facade during this release;
- recommendation selectors remain behaviorally unchanged; versioned policy
  modules are not deleted merely because of their names.

The route table, response shapes and persisted keys are tested before and after
the moves. No endpoint URL changes in this cleanup.

## Data and Backup Hygiene

The 27 existing database backups are not deleted during code deployment.
Instead, a documented retention policy is added: keep the newest three backups
plus one explicitly named milestone backup per major release. Applying that
policy to existing files is a separate destructive operation and requires a
future explicit deletion instruction.

## Testing

Each code phase begins with a failing test for the boundary being changed and
ends with the complete suite. Release verification includes:

- Python test suite and subtests;
- JavaScript tests;
- Playwright visual-contract tests outside the restricted sandbox;
- theme contract and container smoke checks;
- image build for the supported architecture(s);
- live TrueNAS health, rendered-version, mount and cgroup checks.

## Rollback

Code changes are committed in independent phases. The published previous image
tag remains available. Before the TrueNAS edit, save the current custom-app YAML
and volume list. If image-only deployment fails, restore that YAML and the prior
image without touching `/data`. Database backups are retained throughout.

## Out of Scope

- New UI features or visual redesign.
- Recommendation-policy changes.
- Removal of active legacy data readers.
- Deleting old database backups.
- Raising CPU or memory limits without workload evidence.
