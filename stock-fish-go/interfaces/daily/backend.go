package daily

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strings"
	"time"
)

type backend interface {
	Identity() map[string]string
	Ready(context.Context) (c.Readiness, error)
	Inputs(context.Context, string) ([]c.InputRecord, error)
	Runs(context.Context, int, int) ([]c.Run, error)
	Saved(context.Context, string, string, bool) (c.Run, error)
	Close() error
}

func PathHash(path string) (string, error) {
	p, e := filepath.EvalSymlinks(path)
	if e != nil {
		return "", e
	}
	p, e = filepath.Abs(p)
	return c.Hash([]byte(p)), e
}
func openBackend(ctx context.Context, conf Config) (backend, error) {
	if conf.Mode == "server" {
		return openServer(ctx, conf)
	}
	hash, e := PathHash(conf.Database)
	if e != nil {
		return nil, e
	}
	if hash != conf.DatabasePathHash {
		return nil, errors.New("DATABASE_PATH_IDENTITY_CHANGED")
	}
	r, e := sqlitev2.OpenReadOnly(ctx, conf.Database)
	if e != nil {
		return nil, e
	}
	if !reflect.DeepEqual(stableIdentity(r.Identity()), stableIdentity(conf.Identity)) {
		r.Close()
		return nil, errors.New("DATABASE_IDENTITY_CHANGED_REINITIALIZE_EXPLICITLY")
	}
	return r, nil
}

type remote struct {
	conf   Config
	client *http.Client
	token  string
}

type serverHTTPError struct {
	status    int
	operation string
}

func (e *serverHTTPError) Error() string {
	return fmt.Sprintf("SERVER_%s_HTTP_%d", e.operation, e.status)
}

// ServerWaitError records a stopped observer, not a cancellation of the shared
// server job. It preserves the submission identity even after a lost connection.
type ServerWaitError struct {
	JobID   string
	Created bool
	Reason  string
	Cause   error
}

func (e *ServerWaitError) Error() string {
	return fmt.Sprintf("SERVER_WAIT_%s job_id=%s created=%t cancellation_requested=false; query: stockfish daily --job-id %s --config CONFIG_FILE; cause: %v", e.Reason, e.JobID, e.Created, e.JobID, e.Cause)
}
func (e *ServerWaitError) Unwrap() error { return e.Cause }

var serverJobID = regexp.MustCompile(`^[A-Za-z0-9_.:-]{1,200}$`)

func openServer(ctx context.Context, conf Config) (backend, error) {
	u, e := url.Parse(conf.ServerURL)
	if e != nil || u.Scheme != "http" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || u.Path != "" {
		return nil, errors.New("EXPLICIT_LOOPBACK_HTTP_SERVER_REQUIRED")
	}
	host := u.Hostname()
	ip := net.ParseIP(host)
	if host != "localhost" && (ip == nil || !ip.IsLoopback()) {
		return nil, errors.New("LOOPBACK_SERVER_REQUIRED")
	}
	r := &remote{conf: conf, client: &http.Client{Timeout: 5 * time.Minute, Transport: &http.Transport{Proxy: nil}, CheckRedirect: func(*http.Request, []*http.Request) error { return errors.New("SERVER_REDIRECT_REFUSED") }}}
	if conf.TokenFile != "" {
		st, e := os.Stat(conf.TokenFile)
		if e != nil {
			return nil, e
		}
		if st.Mode().Perm()&0077 != 0 {
			return nil, errors.New("TOKEN_FILE_REQUIRES_PRIVATE_PERMISSIONS")
		}
		raw, e := os.ReadFile(conf.TokenFile)
		if e != nil {
			return nil, e
		}
		r.token = strings.TrimSpace(string(raw))
		if len(r.token) > 4096 {
			return nil, errors.New("TOKEN_SIZE_LIMIT")
		}
	}
	var identity struct {
		Protocol string            `json:"protocol"`
		Identity map[string]string `json:"database_identity"`
		PathHash string            `json:"database_path_sha256"`
	}
	if e = r.get(ctx, "/api/daily/identity", &identity); e != nil {
		return nil, e
	}
	if identity.Protocol != Protocol || identity.PathHash != conf.DatabasePathHash || !reflect.DeepEqual(stableIdentity(identity.Identity), stableIdentity(conf.Identity)) {
		return nil, errors.New("SERVER_INSTANCE_DATABASE_OR_PROTOCOL_MISMATCH")
	}
	return r, nil
}
func (r *remote) get(ctx context.Context, path string, out any) error {
	req, e := http.NewRequestWithContext(ctx, "GET", r.conf.ServerURL+path, nil)
	if e != nil {
		return e
	}
	if r.token != "" {
		req.Header.Set("Authorization", "Bearer "+r.token)
	}
	resp, e := r.client.Do(req)
	if e != nil {
		return fmt.Errorf("SERVER_UNAVAILABLE_NO_LOCAL_FALLBACK: %w", e)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return &serverHTTPError{status: resp.StatusCode, operation: "QUERY_FAILED"}
	}
	raw, e := io.ReadAll(io.LimitReader(resp.Body, 128<<20+1))
	if e != nil {
		return e
	}
	if len(raw) > 128<<20 {
		return errors.New("SERVER_RESULT_SIZE_LIMIT")
	}
	return json.Unmarshal(raw, out)
}
func (r *remote) Identity() map[string]string { return r.conf.Identity }
func (r *remote) Close() error                { r.client.CloseIdleConnections(); return nil }
func (r *remote) Ready(ctx context.Context) (c.Readiness, error) {
	var v c.Readiness
	e := r.get(ctx, "/api/daily/readiness", &v)
	return v, e
}
func (r *remote) Inputs(ctx context.Context, day string) ([]c.InputRecord, error) {
	var v []c.InputRecord
	e := r.get(ctx, "/api/daily/inputs?observation="+url.QueryEscape(day), &v)
	return v, e
}
func (r *remote) Runs(ctx context.Context, limit, offset int) ([]c.Run, error) {
	var v []c.Run
	e := r.get(ctx, fmt.Sprintf("/api/daily/runs?limit=%d&offset=%d", limit, offset), &v)
	return v, e
}
func (r *remote) MatchingRuns(ctx context.Context, input, strategy string, limit, offset int) ([]c.Run, error) {
	var runs []c.Run
	err := r.get(ctx, fmt.Sprintf("/api/daily/runs?limit=%d&offset=%d&input_id=%s&strategy=%s", limit, offset, url.QueryEscape(input), url.QueryEscape(strategy)), &runs)
	return runs, err
}
func (r *remote) Saved(ctx context.Context, id, iid string, all bool) (c.Run, error) {
	var v c.Run
	e := r.get(ctx, "/api/daily/runs/"+url.PathEscape(id)+"?instrument="+url.QueryEscape(iid)+"&all="+fmt.Sprint(all), &v)
	return v, e
}
func (r *remote) post(ctx context.Context, path, key string, value any, out any) error {
	raw, e := json.Marshal(value)
	if e != nil {
		return e
	}
	req, e := http.NewRequestWithContext(ctx, "POST", r.conf.ServerURL+path, strings.NewReader(string(raw)))
	if e != nil {
		return e
	}
	req.Header.Set("Authorization", "Bearer "+r.token)
	req.Header.Set("Idempotency-Key", key)
	resp, e := r.client.Do(req)
	if e != nil {
		return fmt.Errorf("SERVER_REQUEST_FAILED: %w", e)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 && resp.StatusCode != 202 {
		return &serverHTTPError{status: resp.StatusCode, operation: "RESEARCH"}
	}
	return json.NewDecoder(io.LimitReader(resp.Body, 64<<10)).Decode(out)
}
func (r *remote) research(ctx context.Context, p Plan, s, source string, hashes map[string]string, log io.Writer) (string, error) {
	if r.token == "" {
		return "", errors.New("SERVER_RESEARCH_PRIVATE_TOKEN_FILE_REQUIRED")
	}
	// Transport can silently replay an idempotent GET after a reused connection
	// fails. Fresh observer connections leave the three-attempt retry budget
	// entirely under this loop's control, including disconnected responses.
	base := r.client.Transport
	if base == nil {
		base = http.DefaultTransport
	}
	transport, ok := base.(*http.Transport)
	if !ok {
		return "", errors.New("SERVER_OBSERVER_TRANSPORT_RETRY_POLICY_UNVERIFIED")
	}
	transport = transport.Clone()
	transport.DisableKeepAlives = true
	defer transport.CloseIdleConnections()
	observerClient := *r.client
	observerClient.Transport = transport
	observer := *r
	observer.client = &observerClient
	raw, _ := json.Marshal(hashes)
	req := c.ResearchRequest{InputID: p.InputID, Strategy: s, BudgetSeconds: p.Budget, TargetSession: p.Target, ConfigIdentity: c.Hash(raw), Implementation: source, EvidencePolicy: Policy}
	identity, _ := json.Marshal(req)
	key := "daily_" + c.Hash(identity)
	route := "/api/research/r1/run"
	if s == c.V2 {
		route = "/api/daily-primary/run"
	}
	body := map[string]any{"input_id": p.InputID, "observation_date": p.Observation, "budget_seconds": p.Budget, "target_session": p.Target, "config_identity": req.ConfigIdentity, "implementation_identity": source, "evidence_policy": Policy}
	work, cancel := context.WithTimeout(ctx, time.Duration(p.Budget)*time.Second)
	defer cancel()
	var result struct {
		ID      string `json:"job_id"`
		Created *bool  `json:"created"`
		State   string `json:"state"`
	}
	if e := r.post(work, route, key, body, &result); e != nil {
		return "", e
	}
	if !serverJobID.MatchString(result.ID) || result.Created == nil {
		return "", errors.New("SERVER_SUBMISSION_IDENTITY_INCOMPLETE_NO_CANCEL_SENT")
	}
	if log != nil {
		if e := json.NewEncoder(log).Encode(map[string]any{"job_id": result.ID, "created": *result.Created, "state": result.State, "action": "WAITING", "query_command": "stockfish daily --job-id " + result.ID + " --config CONFIG_FILE"}); e != nil {
			return "", &ServerWaitError{result.ID, *result.Created, "OUTPUT_FAILED", e}
		}
	}
	failures := 0
	for {
		if e := work.Err(); e != nil {
			return "", &ServerWaitError{result.ID, *result.Created, "STOPPED", e}
		}
		var j c.Job
		poll, endPoll := context.WithTimeout(work, 5*time.Second)
		e := observer.get(poll, "/api/jobs/"+url.PathEscape(result.ID), &j)
		endPoll()
		if e != nil {
			if work.Err() != nil {
				return "", &ServerWaitError{result.ID, *result.Created, "STOPPED", work.Err()}
			}
			failures++
			if !transientServerError(e) || failures >= 3 {
				return "", &ServerWaitError{result.ID, *result.Created, "QUERY_FAILED", e}
			}
			if e := waitPoll(work, time.Duration(failures)*250*time.Millisecond); e != nil {
				return "", &ServerWaitError{result.ID, *result.Created, "STOPPED", e}
			}
			continue
		}
		failures = 0
		if j.ID != result.ID {
			return "", &ServerWaitError{result.ID, *result.Created, "IDENTITY_MISMATCH", errors.New("SERVER_JOB_IDENTITY_MISMATCH")}
		}
		switch j.State {
		case "SUCCEEDED":
			if j.RunID == "" {
				return "", &ServerWaitError{result.ID, *result.Created, "RESULT_MISSING", errors.New("SERVER_SUCCEEDED_WITHOUT_RUN_ID")}
			}
			return j.RunID, nil
		case "FAILED", "CANCELLED", "INTERRUPTED":
			return "", fmt.Errorf("SERVER_ATTEMPT_%s_%s", j.ID, j.State)
		case "QUEUED", "RUNNING":
		default:
			return "", &ServerWaitError{result.ID, *result.Created, "STATE_UNKNOWN", fmt.Errorf("SERVER_UNKNOWN_JOB_STATE_%s", j.State)}
		}
		if e := waitPoll(work, 500*time.Millisecond); e != nil {
			return "", &ServerWaitError{result.ID, *result.Created, "STOPPED", e}
		}
	}
}

func waitPoll(ctx context.Context, duration time.Duration) error {
	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

func transientServerError(err error) bool {
	var response *serverHTTPError
	if errors.As(err, &response) {
		return response.status == 503 || response.status == 502 || response.status == 504 || response.status == 408 || response.status == 429
	}
	var network net.Error
	return errors.As(err, &network) || errors.Is(err, context.DeadlineExceeded)
}

func (r *remote) job(ctx context.Context, id string, cancel bool) (c.Job, error) {
	var job c.Job
	if !serverJobID.MatchString(id) {
		return job, errors.New("INVALID_SERVER_JOB_ID")
	}
	if cancel {
		if r.token == "" {
			return job, errors.New("EXPLICIT_SERVER_CANCEL_PRIVATE_TOKEN_REQUIRED")
		}
		if e := r.post(ctx, "/api/jobs/"+url.PathEscape(id)+"/cancel", "", map[string]any{}, &job); e != nil {
			return job, e
		}
	} else if e := r.get(ctx, "/api/jobs/"+url.PathEscape(id), &job); e != nil {
		return job, e
	}
	if job.ID != id {
		return job, errors.New("SERVER_JOB_IDENTITY_MISMATCH")
	}
	return job, nil
}

// New inputs update the publication inventory, not this database's stable
// migration identity. Its current inventory is still verified at each opening.
func stableIdentity(in map[string]string) map[string]string {
	out := map[string]string{}
	for k, v := range in {
		if k != "batch_projection_manifest_hash" {
			out[k] = v
		}
	}
	return out
}
