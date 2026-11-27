// TM-04 E09 tests: OCR data contract + human-confirmation machine.
// Runs for real on Windows Swift 6.4.3 and on Mac.

import XCTest
import WorkCamModels

final class OCRContractTests: XCTestCase {

    private func candidate(_ text: String,
                           confidence: Double = 0.9)
            -> OCRCandidate {
        OCRCandidate(text: text, confidence: confidence,
                     u: 0.1, v: 0.2, w: 0.3, h: 0.1)
    }

    func test_fresh_field_is_unconfirmed_and_unsearchable() {
        var f = OCRFieldCandidate(fieldType: .serialNumber,
                                  candidates: [candidate("SN-4021")])
        XCTAssertEqual(f.state, .unconfirmed)
        XCTAssertFalse(f.isSearchable)   // B08: not searchable yet
    }

    func test_explicit_confirmation_by_a_human_makes_it_searchable() {
        var f = OCRFieldCandidate(fieldType: .serialNumber,
                                  candidates: [candidate("SN-4021")])
        let who = "inspector-7"
        let when = Date(timeIntervalSince1970: 1_760_000_000)
        XCTAssertTrue(f.confirm(text: "SN-4021", by: who, at: when))
        XCTAssertTrue(f.isSearchable)
        XCTAssertEqual(
            f.state,
            .confirmed(text: "SN-4021", confirmedBy: who,
                       confirmedAt: when))
    }

    func test_human_corrected_text_is_also_legitimate() {
        var f = OCRFieldCandidate(fieldType: .measurementReading,
                                  candidates: [candidate("12.5 bar")])
        // the user fixes the OCR output - allowed, still audited
        XCTAssertTrue(f.confirm(text: "12.6 bar", by: "u1",
                                at: Date(timeIntervalSince1970: 0)))
        XCTAssertTrue(f.isSearchable)
    }

    func test_confirming_a_field_with_zero_candidates_refused() {
        var f = OCRFieldCandidate(fieldType: .other, candidates: [])
        XCTAssertFalse(f.confirm(text: "whatever", by: "u1",
                                 at: Date(timeIntervalSince1970: 0)))
        XCTAssertEqual(f.state, .unconfirmed)
    }

    func test_cannot_confirm_twice_or_reconfirm_after_discard() {
        var f = OCRFieldCandidate(fieldType: .dateCode,
                                  candidates: [candidate("2024-03")])
        XCTAssertTrue(f.confirm(text: "2024-03", by: "u1",
                                at: Date(timeIntervalSince1970: 0)))
        XCTAssertFalse(f.confirm(text: "2024-03", by: "u2",
                                 at: Date(timeIntervalSince1970: 10)))
        f.discard(reason: "misread")
        XCTAssertEqual(f.state, .confirmed(text: "2024-03",
                                            confirmedBy: "u1",
                                            confirmedAt: Date(
                                                timeIntervalSince1970: 0)))
    }

    func test_discard_is_final_for_that_field_attempt() {
        var f = OCRFieldCandidate(fieldType: .locationLabel,
                                  candidates: [candidate("L12")])
        f.discard(reason: "not a location label")
        XCTAssertEqual(f.state, .discarded(reason:
                "not a location label"))
        XCTAssertFalse(f.isSearchable)
        // discarded cannot be confirmed afterwards (a NEW attempt
        // would be a new OCRFieldCandidate - the audit trail of the
        // discarded one is preserved by the caller)
        XCTAssertFalse(f.confirm(text: "L12", by: "u1",
                                 at: Date(timeIntervalSince1970: 0)))
    }

    func test_normalization_collapses_case_and_whitespace() {
        let c = candidate("sn   4021")
        XCTAssertEqual(c.normalizedText, "SN 4021")
        XCTAssertEqual(candidate("Sn-4021").normalizedText,
                       "SN-4021")
    }

    func test_state_roundtrips_through_codable() throws {
        var f = OCRFieldCandidate(fieldType: .serialNumber,
                                  candidates: [candidate("SN-9")])
        _ = f.confirm(text: "SN-9", by: "auditor",
                     at: Date(timeIntervalSince1970: 123))
        let data = try JSONEncoder().encode(f)
        let back = try JSONDecoder().decode(
            OCRFieldCandidate.self, from: data)
        XCTAssertEqual(back, f)
        XCTAssertTrue(back.isSearchable)   // survives the round trip
    }

    func test_field_taxonomy_is_closed() {
        XCTAssertEqual(OCRFieldType.allCases.map(\.rawValue),
                       ["serial_number", "model_plate",
                        "measurement_reading", "location_label",
                        "date_code", "barcode_text", "other"])
    }
}
