package daily

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/provider"
)

func fixtureDependencyConfig(t *testing.T, script string) Config {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("POSIX fake interpreter fixture; native Windows check remains unverified")
	}
	dir := t.TempDir()
	python := filepath.Join(dir, "TEST_FIXTURE_interpreter")
	if err := os.WriteFile(python, []byte("#!/bin/sh\n"+script+"\n"), 0700); err != nil {
		t.Fatal(err)
	}
	worker := filepath.Join(dir, "daily_refresh_worker.py")
	if err := os.WriteFile(worker, provider.Worker, 0600); err != nil {
		t.Fatal(err)
	}
	return Config{Provider: "FIXED_MAINBOARD_PUBLIC_V1", Mode: "embedded", Python: python, Worker: worker, WorkerHash: provider.WorkerHash()}
}

func TestDependencyCheckTimeoutCanceledProtocolAndClassifiedFailure(t *testing.T) {
	for _, tc := range []struct {
		name, script, want string
	}{
		{"timeout", "sleep 5", "BLOCKED_DEPENDENCY_CHECK_TIMEOUT"},
		{"invalid_json", "printf 'invalid'", "BLOCKED_DEPENDENCY_CHECK_PROTOCOL"},
		{"old_find_spec_contract", `printf '%s' '{"network_probe":false,"missing_dependencies":[]}'`, "BLOCKED_DEPENDENCY_CHECK_PROTOCOL"},
		{"extension_missing", `printf '%s' '{"check_contract":"OFFLINE_ACTUAL_IMPORT_AND_LOCAL_CALL_V1","status":"BLOCKED_DEPENDENCY_CHECK","network_probe":false,"network_calls_forbidden":true,"failure_categories":["COMPILED_EXTENSION_OR_IMPORT_DEPENDENCY_MISSING"]}'; exit 1`, "BLOCKED_DEPENDENCY_CHECK"},
		{"verified", `printf '%s' '{"check_contract":"OFFLINE_ACTUAL_IMPORT_AND_LOCAL_CALL_V1","status":"INSTALLED_NOT_ONLINE_VERIFIED","network_probe":false,"network_calls_forbidden":true}'`, "INSTALLED_NOT_ONLINE_VERIFIED"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			conf := fixtureDependencyConfig(t, tc.script)
			limit := time.Second
			if tc.name == "timeout" {
				limit = 20 * time.Millisecond
			}
			got := dependencyCheckTimeout(context.Background(), conf, limit)
			if got["status"] != tc.want || got["network_probe"] != false {
				t.Fatalf("%+v", got)
			}
		})
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	got := dependencyCheckTimeout(ctx, fixtureDependencyConfig(t, "sleep 5"), time.Second)
	if got["status"] != "BLOCKED_DEPENDENCY_CHECK_CANCELED" {
		t.Fatal(got)
	}
}

func TestHistoryCapturePresenceReadOnlyAndFailClosed(t *testing.T) {
	config, s, _ := dailyFixture(t)
	conf, err := load(config)
	if err != nil {
		t.Fatal(err)
	}
	before := captureHistoryStatus(context.Background(), conf)
	if before["history_present"] != false || before["status"] != "COLD_CAPTURE_EXPLICIT_OPT_IN_REQUIRED" {
		t.Fatal(before)
	}
	if _, err = s.DB().Exec("INSERT INTO partitions VALUES(1,?)", fmt.Sprintf("%064d", 1)); err != nil {
		t.Fatal(err)
	}
	after := captureHistoryStatus(context.Background(), conf)
	if after["history_present"] != true || after["selection_status"] != "NOT_SELECTED" || after["adapter_implementation"] != "NOT_IMPLEMENTED" {
		t.Fatal(after)
	}
	if after["status"] != "BLOCKED_GO_V2_HISTORY_ADAPTER_UNAVAILABLE" {
		t.Fatal(after)
	}
	conf.Database += "_NOT_PRESENT"
	unknown := captureHistoryStatus(context.Background(), conf)
	if unknown["history_present"] != nil || unknown["selection_status"] != "NOT_INSPECTED" {
		t.Fatal(unknown)
	}
}
