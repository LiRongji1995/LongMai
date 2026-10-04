package daily

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

func TestServerObserversNeverCancelOnFailureOrStop(t *testing.T) {
	for _, created := range []bool{false, true} {
		for _, mode := range []string{"503", "disconnect", "stop"} {
			t.Run(mode+"_created_"+map[bool]string{false: "false", true: "true"}[created], func(t *testing.T) {
				var cancels, polls atomic.Int32
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					switch {
					case r.Method == "POST" && r.URL.Path == "/api/research/r1/run":
						_ = json.NewEncoder(w).Encode(map[string]any{"job_id": "TEST_FIXTURE_JOB", "created": created, "state": "RUNNING"})
					case r.Method == "POST":
						cancels.Add(1)
						_ = json.NewEncoder(w).Encode(c.Job{ID: "TEST_FIXTURE_JOB", State: "CANCELLED"})
					case r.Method == "GET":
						polls.Add(1)
						switch mode {
						case "503":
							w.WriteHeader(503)
						case "disconnect":
							connection, _, err := w.(http.Hijacker).Hijack()
							if err == nil {
								_ = connection.Close()
							}
						case "stop":
							cancel()
							_ = json.NewEncoder(w).Encode(c.Job{ID: "TEST_FIXTURE_JOB", State: "RUNNING"})
						}
					}
				}))
				defer server.Close()
				client := &remote{conf: Config{ServerURL: server.URL}, token: "TEST_FIXTURE_TOKEN", client: server.Client()}
				var log bytes.Buffer
				_, err := client.research(ctx, Plan{Budget: 2, InputID: "TEST_FIXTURE_INPUT"}, c.R1, "TEST_FIXTURE_BUILD", nil, &log)
				var stopped *ServerWaitError
				if !errors.As(err, &stopped) || stopped.JobID != "TEST_FIXTURE_JOB" || stopped.Created != created {
					t.Fatalf("identity lost: %v", err)
				}
				if cancels.Load() != 0 {
					t.Fatal("observer cancelled shared job")
				}
				if mode != "stop" && polls.Load() != 3 {
					t.Fatalf("unbounded or missing retries: %d", polls.Load())
				}
				if !bytes.Contains(log.Bytes(), []byte(`"job_id":"TEST_FIXTURE_JOB"`)) {
					t.Fatal("submission identity not reported")
				}
			})
		}
	}
}

func TestServerTemporary503ThenSuccessAndExplicitCancellation(t *testing.T) {
	var polls, cancels atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "POST" && r.URL.Path == "/api/research/r1/run" {
			_ = json.NewEncoder(w).Encode(map[string]any{"job_id": "TEST_FIXTURE_JOB", "created": false, "state": "RUNNING"})
			return
		}
		if r.Method == "POST" {
			cancels.Add(1)
			if r.Header.Get("Authorization") != "Bearer TEST_FIXTURE_TOKEN" {
				w.WriteHeader(401)
				return
			}
			_ = json.NewEncoder(w).Encode(c.Job{ID: "TEST_FIXTURE_JOB", State: "CANCELLED"})
			return
		}
		if polls.Add(1) == 1 {
			w.WriteHeader(503)
			return
		}
		_ = json.NewEncoder(w).Encode(c.Job{ID: "TEST_FIXTURE_JOB", State: "SUCCEEDED", RunID: "TEST_FIXTURE_RUN"})
	}))
	defer server.Close()
	client := &remote{conf: Config{ServerURL: server.URL}, token: "TEST_FIXTURE_TOKEN", client: server.Client()}
	id, err := client.research(context.Background(), Plan{Budget: 2, InputID: "TEST_FIXTURE_INPUT"}, c.R1, "TEST_FIXTURE_BUILD", nil, nil)
	if err != nil || id != "TEST_FIXTURE_RUN" || cancels.Load() != 0 {
		t.Fatal(id, err, cancels.Load())
	}
	client.token = ""
	if _, err := client.job(context.Background(), "TEST_FIXTURE_JOB", true); err == nil || cancels.Load() != 0 {
		t.Fatal("tokenless cancellation sent")
	}
	client.token = "TEST_FIXTURE_TOKEN"
	job, err := client.job(context.Background(), "TEST_FIXTURE_JOB", true)
	if err != nil || job.State != "CANCELLED" || cancels.Load() != 1 {
		t.Fatal(job, err, cancels.Load())
	}
}

func TestServerWaitDeadlineRetainsJobIdentity(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "POST" {
			_ = json.NewEncoder(w).Encode(map[string]any{"job_id": "TEST_FIXTURE_JOB", "created": true, "state": "RUNNING"})
			return
		}
		<-r.Context().Done()
	}))
	defer server.Close()
	client := &remote{conf: Config{ServerURL: server.URL}, token: "TEST_FIXTURE_TOKEN", client: server.Client()}
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	_, err := client.research(ctx, Plan{Budget: 1, InputID: "TEST_FIXTURE_INPUT"}, c.R1, "TEST_FIXTURE_BUILD", nil, nil)
	var stopped *ServerWaitError
	if !errors.As(err, &stopped) || !errors.Is(err, context.DeadlineExceeded) || stopped.JobID != "TEST_FIXTURE_JOB" {
		t.Fatal(err)
	}
}
