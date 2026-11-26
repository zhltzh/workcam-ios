import Foundation

/// Read-only mirror of Windows' five independently observed pipeline facts.
func derivedState(_ f: [Bool]) -> String {
    let (metadata, original, binding, link, verified, failed) = (f[0], f[1], f[2], f[3], f[4], f[5])
    if verified && (!link || !binding) || failed && !link || link && !binding ||
        binding && (!metadata || !original) { return "violation" }
    if verified { return "trusted_signed" }
    if failed { return "evidence_failed" }
    if link { return "evidence_pending" }
    if binding { return "bound" }
    if metadata && original { return "stored_unbound" }
    if original { return "orphan_bytes" }
    if metadata { return "registered" }
    return "empty"
}

let path = URL(fileURLWithPath: CommandLine.arguments[1])
let expected = try JSONDecoder().decode([String].self, from: Data(contentsOf: path))
precondition(expected.count == 64)
for mask in expected.indices {
    let facts = (0..<6).map { mask & (1 << $0) != 0 }
    precondition(derivedState(facts) == expected[mask], "TM32 fact mask \(mask)")
}
print("PASS TM32: all 64 derived fact states match the fixed Windows reference")
