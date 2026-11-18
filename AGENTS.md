# WorkCam engineering rules

- Keep this a native iPhone Swift / SwiftUI project. Prefer system frameworks and English UI.
- Keep changes scoped to the authorized task. Task 002 requires explicit review and authorization.
- Never represent placeholders or mock data as verified evidence. Unimplemented status must say Not Implemented.
- Do not add dependencies, networking, entitlements, permission prompts or security claims without an implemented requirement.
- Never commit signing secrets, private keys, profiles, or captured personal data.
- Record exact build commands, actual outcomes and blockers in docs/progress.md. Unsigned builds do not demonstrate device execution.
- Preserve separation of Debug and Release and update architecture / threat model when behavior changes.
