package httpapi_test

import (
	"context"
	"encoding/json"
	"net/http/httptest"
	"strings"
	"testing"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

type jobFixtureEngine struct{}

func (jobFixtureEngine) Start(ctx context.Context, _ int) (context.Context, func(), error) {
	return ctx, func() {}, nil
}
func (jobFixtureEngine) Validate([]byte) (c.InputRecord, error) { return c.InputRecord{}, nil }
func (jobFixtureEngine) Evaluate(ctx context.Context, _ c.InputRecord, _ c.ResearchRequest) (c.Run, error) {
	<-ctx.Done()
	return c.Run{}, ctx.Err()
}

func TestSharedJobSubmissionAndExplicitAdministratorCancellation(t *testing.T) {
	f := setup(t)
	f.server.Service.Engine = jobFixtureEngine{}
	raw := []byte(`{"coverage_type":"TEST_FIXTURE"}`)
	if err := f.repo.PutInput(context.Background(), c.InputRecord{ID: "TEST_FIXTURE_JOB_INPUT", Raw: raw, RawHash: c.Hash(raw), Observation: "2026-09-30"}); err != nil {
		t.Fatal(err)
	}
	post := func(path, body, token string) *httptest.ResponseRecorder {
		req := httptest.NewRequest("POST", "http://127.0.0.1"+path, strings.NewReader(body))
		req.Header.Set("Idempotency-Key", "TEST_FIXTURE_SHARED_KEY")
		if token != "" {
			req.Header.Set("Authorization", "Bearer "+token)
		}
		response := httptest.NewRecorder()
		f.server.ServeHTTP(response, req)
		return response
	}
	var first, second struct {
		ID      string `json:"job_id"`
		Created bool   `json:"created"`
	}
	for i := 0; i < 2; i++ {
		response := post("/api/research/r1/run", `{"input_id":"TEST_FIXTURE_JOB_INPUT","budget_seconds":30}`, "TEST_FIXTURE_TOKEN")
		if response.Code != 202 {
			t.Fatal(response.Code, response.Body.String())
		}
		if i == 0 {
			if err := json.Unmarshal(response.Body.Bytes(), &first); err != nil {
				t.Fatal(err)
			}
		} else {
			if err := json.Unmarshal(response.Body.Bytes(), &second); err != nil {
				t.Fatal(err)
			}
		}
	}
	if first.ID == "" || !first.Created || second.Created || first.ID != second.ID {
		t.Fatalf("duplicate creation: %+v %+v", first, second)
	}
	path := "/api/jobs/" + first.ID + "/cancel"
	if response := post(path, `{}`, ""); response.Code != 401 {
		t.Fatal("cancel without authority", response.Code)
	}
	if response := post(path, `{"unexpected":true}`, "TEST_FIXTURE_TOKEN"); response.Code != 400 {
		t.Fatal("invalid cancel body accepted", response.Code)
	}
	job, err := f.repo.Job(context.Background(), first.ID)
	if err != nil || (job.State != "QUEUED" && job.State != "RUNNING") {
		t.Fatal("failed query or unauthorized cancel changed job", job, err)
	}
	response := post(path, `{}`, "TEST_FIXTURE_TOKEN")
	if response.Code != 200 {
		t.Fatal(response.Code, response.Body.String())
	}
	job, err = f.repo.Job(context.Background(), first.ID)
	if err != nil || job.State != "CANCELLED" {
		t.Fatal(job, err)
	}
	jobs, err := f.repo.Jobs(context.Background(), "")
	if err != nil || len(jobs) != 1 {
		t.Fatal("duplicate shared jobs", len(jobs), err)
	}
}
