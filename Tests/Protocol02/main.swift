import Foundation

let vectorData = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
let object = try JSONSerialization.jsonObject(with: vectorData) as! [String: Any]
let vectors = object["cases"] as! [[String: Any]]
let vector = vectors.first!
var record = vector["record"] as! [String: Any]
record["signature"] = vector["signature_der_b64"]
record["public_key_b64"] = object["test_public_key_b64"]
let valid = try TM01Evidence.validate(JSONSerialization.data(withJSONObject: record))
precondition(valid.protocol_version == "1.0")
record["protocol_version"] = "1.1"
_ = try TM01Evidence.validate(JSONSerialization.data(withJSONObject: record))
record["protocol_version"] = "9.0"
let invalid = try JSONSerialization.data(withJSONObject: record)
precondition((try? TM01Evidence.validate(invalid)) == nil)
let interval = TM10Interval.estimate(server: 1000, rtt: 2, anchor: 10, capture: 30, driftPPM: 250,
                                     resolution: 0.01, ttl: 60, now: 1030, reset: false)!
precondition(abs(interval.uncertainty - 1.015) < 0.0001 && !interval.expired)
precondition(TM10Interval.estimate(server: 1000, rtt: 2, anchor: 10, capture: 30, driftPPM: 250,
                                  resolution: 0.01, ttl: 60, now: 1030, reset: true) == nil)
let prior = TM12Location.Fix(lat: 0, lon: 0, time: 10, accuracy: 4, altitude: 0, source: .gnss)
let fix = TM12Location.Fix(lat: 1, lon: 1, time: 11, accuracy: 4, altitude: 80, source: .gnss)
let quality = TM12Location.assess(fix, prior: prior, capture: 42)
precondition(quality.grade == .excellent && quality.stale && quality.flags.contains("ground_jump"))
precondition(TM12Location.assess(.init(lat: 1, lon: 1, time: nil, accuracy: 1, altitude: nil, source: .manual)).grade == .none)
print("PASS TM01 historical and unknown versions, TM10 interval/reset, TM12 quality/anomalies")
