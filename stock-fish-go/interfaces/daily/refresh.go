package daily

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/native"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/provider"
	resources "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/runtime"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

func refreshReady(conf Config) string {
	if conf.Provider != "FIXED_MAINBOARD_PUBLIC_V1" {
		return "BLOCKED_PROVIDER_CONTRACT_NOT_CONFIGURED"
	}
	if conf.Mode != "embedded" {
		return "BLOCKED_REFRESH_SERVER_WORKER_NOT_CONFIGURED"
	}
	if conf.Python == "" || conf.Worker == "" {
		return "BLOCKED_WORKER_MISSING"
	}
	if _, e := os.Stat(conf.Python); e != nil {
		return "BLOCKED_WORKER_INTERPRETER_MISSING"
	}
	raw, e := os.ReadFile(conf.Worker)
	if e != nil {
		return "BLOCKED_WORKER_MISSING"
	}
	if c.Hash(raw) != provider.WorkerHash() || conf.WorkerHash != provider.WorkerHash() {
		return "BLOCKED_FIXED_WORKER_IDENTITY_MISMATCH"
	}
	return "CONFIGURED_NOT_ONLINE_VERIFIED"
}

type boundedBuffer struct {
	bytes.Buffer
	max int
}

func (b *boundedBuffer) Write(p []byte) (int, error) {
	if b.Len()+len(p) > b.max {
		return 0, errors.New("WORKER_OUTPUT_LIMIT")
	}
	return b.Buffer.Write(p)
}
func capture(ctx context.Context, conf Config, day string, log io.Writer) ([]byte, error) {
	return captureWithCold(ctx, conf, day, log, false)
}

func captureWithCold(ctx context.Context, conf Config, day string, log io.Writer, allowCold bool) ([]byte, error) {
	if refreshReady(conf) != "CONFIGURED_NOT_ONLINE_VERIFIED" {
		return nil, errors.New(refreshReady(conf))
	}
	if e := os.MkdirAll(conf.Artifacts, 0700); e != nil {
		return nil, e
	}
	dir, e := os.MkdirTemp(conf.Artifacts, "refresh-")
	if e != nil {
		return nil, e
	}
	b, e := resources.NewBudget(ctx, 600*time.Second, log)
	if e != nil {
		return nil, e
	}
	defer b.Close()
	checked := dependencyCheckTimeout(b.Ctx(), conf, 30*time.Second)
	if checked["status"] != "INSTALLED_NOT_ONLINE_VERIFIED" {
		return nil, preserveRefreshPreflightFailure(dir, checked, "DEPENDENCY_CHECK_FAILED", b.Snapshot())
	}
	historyStatus := captureHistoryStatus(b.Ctx(), conf)
	var base c.InputRecord
	var proof native.RefreshHistoryProof
	requestFields := map[string]any{"contract": conf.Provider, "observation_date": day, "output": dir, "allow_cold_capture": allowCold, "history_status": historyStatus}
	if historyStatus["history_present"] == true {
		base, proof, e = prepareRefreshHistory(b.Ctx(), conf, day, dir, b)
		if e != nil {
			return nil, preserveRefreshPreflightFailure(dir, historyStatus, e.Error(), b.Snapshot())
		}
		requestFields["go_history_base"] = map[string]string{"manifest_file": proof.ManifestFile, "manifest_sha256": proof.ManifestHash}
		requestFields["history_bridge_sha256"] = provider.HistoryBridgeHash()
	} else if historyStatus["history_present"] != false {
		requestFields["history_reuse"] = historyStatus
	}
	request, _ := json.Marshal(requestFields)
	cmd := exec.CommandContext(b.Ctx(), conf.Python, conf.Worker)
	resources.ConfigureCommand(cmd)
	cmd.Stdin = bytes.NewReader(request)
	var stdout = boundedBuffer{max: 64 << 10}
	var stderr = boundedBuffer{max: 64 << 10}
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	start := time.Now().UTC()
	e = cmd.Run()
	clockErr := b.Check()
	var report map[string]any
	decodeErr := json.Unmarshal(stdout.Bytes(), &report)
	receipt := map[string]any{"contract": conf.Provider, "started_at": start.Format(time.RFC3339Nano), "completed_at": time.Now().UTC().Format(time.RFC3339Nano), "worker_sha256": conf.WorkerHash, "exit_success": e == nil, "worker_report": report, "metrics": b.Snapshot(), "history_status": historyStatus, "cold_capture_explicitly_requested": allowCold, "status": "FAILED"}
	if base.ID != "" {
		receipt["history_bridge_sha256"] = provider.HistoryBridgeHash()
		receipt["go_history_manifest_sha256"] = proof.ManifestHash
		receipt["base_input_sha256"] = base.RawHash
		receipt["go_composition_receipt"] = "composition-attempt.json"
	}
	if e == nil && decodeErr == nil && clockErr == nil && (report["status"] == "COMPLETE" || report["status"] == "PARTIAL") {
		receipt["status"] = report["status"]
	}
	f, er := os.OpenFile(filepath.Join(dir, "attempt.json"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if er != nil {
		return nil, er
	}
	er = json.NewEncoder(f).Encode(receipt)
	er = errors.Join(er, f.Close())
	if er != nil {
		return nil, er
	}
	if e != nil || decodeErr != nil || clockErr != nil || receipt["status"] == "FAILED" {
		return nil, errors.New("REFRESH_FAILED_ATTEMPT_PRESERVED_" + filepath.Base(dir))
	}
	frozen, e := os.Open(filepath.Join(dir, "frozen.json"))
	if e != nil {
		return nil, e
	}
	defer frozen.Close()
	raw, e := io.ReadAll(io.LimitReader(frozen, 64<<20+1))
	if len(raw) > 64<<20 {
		return nil, errors.New("FROZEN_INPUT_SIZE_LIMIT")
	}
	if e == nil && base.ID != "" {
		if e = recheckRefreshInput(b.Ctx(), conf, base); e == nil {
			raw, e = native.ComposeRefreshInput(base, raw, proof)
		}
		if e == nil {
			composed, err := os.OpenFile(filepath.Join(dir, "composed-frozen.json"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
			if err == nil {
				_, err = composed.Write(raw)
				err = errors.Join(err, composed.Sync(), composed.Close())
			}
			e = err
		}
		composition := map[string]any{"stage": "GO_ORIGINAL_INPUT_RECHECK_AND_COMPOSITION", "status": "FAILED", "original_input_sha256": base.RawHash, "go_history_manifest_sha256": proof.ManifestHash, "completed_at": time.Now().UTC().Format(time.RFC3339Nano)}
		if e == nil {
			composition["status"] = report["status"]
			composition["frozen_sha256"] = c.Hash(raw)
		} else {
			composition["reason"] = e.Error()
		}
		file, err := os.OpenFile(filepath.Join(dir, "composition-attempt.json"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
		if err != nil {
			return nil, err
		}
		err = json.NewEncoder(file).Encode(composition)
		err = errors.Join(err, file.Sync(), file.Close())
		if err != nil {
			return nil, err
		}
	}
	return raw, e
}

func preserveRefreshPreflightFailure(dir string, proof any, reason string, metrics c.Metrics) error {
	f, e := os.OpenFile(filepath.Join(dir, "attempt.json"), os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return e
	}
	e = json.NewEncoder(f).Encode(map[string]any{"contract": "FIXED_MAINBOARD_PUBLIC_V1", "status": "FAILED", "stage": "SDK_AND_VERIFIED_HISTORY_BEFORE_NETWORK", "reason": reason, "proof": proof, "metrics": metrics, "completed_at": time.Now().UTC().Format(time.RFC3339Nano)})
	e = errors.Join(e, f.Sync(), f.Close())
	if e != nil {
		return e
	}
	return errors.New("REFRESH_PREFLIGHT_FAILED_ATTEMPT_PRESERVED_" + filepath.Base(dir) + ": " + reason)
}

func dependencyCheck(ctx context.Context, conf Config) map[string]any {
	return dependencyCheckTimeout(ctx, conf, 30*time.Second)
}

func dependencyCheckTimeout(ctx context.Context, conf Config, timeout time.Duration) map[string]any {
	status := refreshReady(conf)
	if status != "CONFIGURED_NOT_ONLINE_VERIFIED" {
		return map[string]any{"status": status, "network_probe": false}
	}
	check, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext(check, conf.Python, conf.Worker, "--check")
	resources.ConfigureCommand(cmd)
	var stdout = boundedBuffer{max: 64 << 10}
	cmd.Stdout = &stdout
	cmd.Stderr = io.Discard
	e := cmd.Run()
	if check.Err() != nil {
		status := "BLOCKED_DEPENDENCY_CHECK_TIMEOUT"
		if errors.Is(check.Err(), context.Canceled) {
			status = "BLOCKED_DEPENDENCY_CHECK_CANCELED"
		}
		return map[string]any{"status": status, "network_probe": false, "check_timeout_seconds": timeout.Seconds()}
	}
	var v map[string]any
	if json.Unmarshal(stdout.Bytes(), &v) != nil {
		return map[string]any{"status": "BLOCKED_DEPENDENCY_CHECK_PROTOCOL", "network_probe": false}
	}
	if v["check_contract"] != "OFFLINE_ACTUAL_IMPORT_AND_LOCAL_CALL_V1" || v["network_probe"] != false || v["network_calls_forbidden"] != true {
		return map[string]any{"status": "BLOCKED_DEPENDENCY_CHECK_PROTOCOL", "network_probe": false}
	}
	if e != nil || v["status"] != "INSTALLED_NOT_ONLINE_VERIFIED" {
		v["status"] = "BLOCKED_DEPENDENCY_CHECK"
	} else {
		v["history_reuse"] = captureHistoryStatus(check, conf)
	}
	return v
}

func captureHistoryStatus(ctx context.Context, conf Config) map[string]any {
	r, err := openBackend(ctx, conf)
	if err != nil {
		return map[string]any{"status": "BLOCKED_HISTORY_STATUS_QUERY", "history_present": nil, "selection_status": "NOT_INSPECTED", "adapter_implementation": "NOT_IMPLEMENTED"}
	}
	defer r.Close()
	history, ok := r.(interface {
		HistoryCaptureStatus(context.Context) (map[string]any, error)
	})
	if !ok {
		return map[string]any{"status": "BLOCKED_HISTORY_STATUS_QUERY", "history_present": nil, "selection_status": "NOT_INSPECTED", "adapter_implementation": "NOT_IMPLEMENTED"}
	}
	status, err := history.HistoryCaptureStatus(ctx)
	if err != nil {
		return map[string]any{"status": "BLOCKED_HISTORY_STATUS_QUERY", "history_present": nil, "selection_status": "NOT_INSPECTED", "adapter_implementation": "NOT_IMPLEMENTED"}
	}
	if status["history_present"] == true && conf.Worker != "" {
		module, e := os.ReadFile(filepath.Join(filepath.Dir(conf.Worker), "go_history_bridge.py"))
		if e == nil && c.Hash(module) == provider.HistoryBridgeHash() {
			status["adapter_implementation"] = native.RefreshHistoryContract
			status["status"] = "GO_HISTORY_REQUIRES_VERIFIED_BASE_SELECTION_AND_PRICE_PROOF"
		}
	}
	return status
}
