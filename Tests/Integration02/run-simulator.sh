#!/usr/bin/env bash
set -eu
fixed="${1:?path to fixed backend archive with venv312}"
n="${2:?unique result suffix}"
run=$(mktemp -d /tmp/workcam-int02-live.XXXXXX)
(cd "$fixed/backend" && exec env WORKCAM_ENV=development WORKCAM_ENROLLMENT_CODE=workcam-int02-local-fixture WORKCAM_BUSINESS_DB="$run/workcam.db" WORKCAM_ORIGINALS_DIR="$run/originals" "$fixed/venv312/bin/python" -m uvicorn app.main:app --host 127.0.0.1 --port 18765 --no-access-log) > "/tmp/workcam-int02-server-$n.log" 2>&1 &
pid=$!
trap 'kill "$pid" 2>/dev/null || true' EXIT
for attempt in {1..25}; do
  if curl -fsS http://127.0.0.1:18765/health >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -fsS http://127.0.0.1:18765/health >/dev/null
sim=$(xcrun simctl list devices booted | sed -nE 's/.*iPhone 18 Pro \(([0-9A-Fa-f-]+)\) \(Booted\).*/\1/p' | head -1)
test -n "$sim"
TEST_RUNNER_WORKCAM_LIVE_TEST=1 xcodebuild -jobs 1 -project WorkCam.xcodeproj -scheme WorkCam-Workflow -configuration Debug -destination "platform=iOS Simulator,id=$sim" -derivedDataPath /tmp/WorkCamInt02-Simulator -resultBundlePath "/tmp/WorkCamInt02-UI-$n.xcresult" -parallel-testing-enabled NO ARCHS=arm64 ONLY_ACTIVE_ARCH=YES -only-testing:WorkCamUITests/WorkflowTests/testSwiftClientAgainstFixedBackend test > "/tmp/workcam-int02-ui-$n.log" 2>&1
