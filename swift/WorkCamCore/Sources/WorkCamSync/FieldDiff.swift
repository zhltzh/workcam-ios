// TM-04 B03: field-level three-way diff - decides what is a real
// conflict before anything is surfaced to the user.
//
// CLASSIC THREE-WAY MERGE SEMANTICS per field:
//   local == server            -> .identical   (same edit on both
//                                        sides: nothing to resolve)
//   base  == server, local != -> .independent (only local changed:
//                                        local wins, NOT a conflict)
//   base  == local,  server != -> .independent (server wins locally)
//   all three differ           -> .conflicting (the ONLY case that
//                                        may block or prompt a user)
//
// POLICY: only .conflicting fields ever reach the user. Everything
// else merges mechanically - but the merged RESULT still goes
// through the normal CAS submission (revision check on the server);
// nothing is ever silently written past a revision gate.

import Foundation
import WorkCamEvidence

public enum FieldChangeKind: String, Codable, Sendable, Equatable {
    case identical
    case independent
    case conflicting
}

public struct FieldDiff: Sendable, Equatable {
    public let field: String
    public let base: JSONValue?
    public let local: JSONValue?
    public let server: JSONValue?
    public let kind: FieldChangeKind

    public init(field: String, base: JSONValue?, local: JSONValue?,
                server: JSONValue?) {
        self.field = field
        self.base = base
        self.local = local
        self.server = server
        if local == server {
            self.kind = .identical
        } else if base == server {
            self.kind = .independent
        } else if base == local {
            self.kind = .independent
        } else {
            self.kind = .conflicting
        }
    }
}

public enum FieldDiffEngine {

    /// Union of keys across the three versions, sorted for
    /// deterministic output. Missing key on one side == .null value.
    public static func diff(base: [String: JSONValue],
                            local: [String: JSONValue],
                            server: [String: JSONValue])
            -> [FieldDiff] {
        let keys = Set(base.keys)
            .union(local.keys)
            .union(server.keys)
            .sorted()
        return keys.map { k in
            FieldDiff(field: k,
                      base: base[k] ?? .null,
                      local: local[k] ?? .null,
                      server: server[k] ?? .null)
        }
    }

    public struct Summary: Sendable, Equatable {
        public let diffs: [FieldDiff]

        public init(diffs: [FieldDiff]) {
            self.diffs = diffs
        }

        public var identical: [FieldDiff] {
            diffs.filter { $0.kind == .identical }
        }
        public var independent: [FieldDiff] {
            diffs.filter { $0.kind == .independent }
        }
        public var conflicting: [FieldDiff] {
            diffs.filter { $0.kind == .conflicting }
        }
        public var hasConflicts: Bool { !conflicting.isEmpty }
    }

    /// Mechanical merge for the NON-conflicting fields. The local
    /// values win where only local changed; server values apply
    /// where only the server changed. Conflicting fields are NOT
    /// decided here - they stay in `left` for a human or an explicit
    /// decision (B02).
    public static func mergedValue(
        diffs: [FieldDiff],
        resolving conflicts: [String: JSONValue]
    ) -> [String: JSONValue] {
        var out: [String: JSONValue] = [:]
        for d in diffs {
            switch d.kind {
            case .identical:
                out[d.field] = d.local
            case .independent:
                if d.base == d.server {
                    out[d.field] = d.local    // local-only change
                } else {
                    out[d.field] = d.server   // server-only change
                }
            case .conflicting:
                out[d.field] = conflicts[d.field] ?? d.local
            }
        }
        return out
    }
}
