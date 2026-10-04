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
		return errors.New("SERVER_UNAVAILABLE_NO_LOCAL_FALLBACK")
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("SERVER_QUERY_FAILED_HTTP_%d", resp.StatusCode)
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
		return errors.New("SERVER_REQUEST_FAILED")
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 && resp.StatusCode != 202 {
		return fmt.Errorf("SERVER_RESEARCH_HTTP_%d", resp.StatusCode)
	}
	return json.NewDecoder(io.LimitReader(resp.Body, 64<<10)).Decode(out)
}
func (r *remote) research(ctx context.Context, p Plan, s, source string, hashes map[string]string) (string, error) {
	if r.token == "" {
		return "", errors.New("SERVER_RESEARCH_PRIVATE_TOKEN_FILE_REQUIRED")
	}
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
		ID string `json:"job_id"`
	}
	if e := r.post(work, route, key, body, &result); e != nil {
		return "", e
	}
	for {
		var j c.Job
		if e := r.get(work, "/api/jobs/"+url.PathEscape(result.ID), &j); e != nil {
			cleanup, cancel := context.WithTimeout(context.Background(), 3*time.Second)
			defer cancel()
			var ignored any
			_ = r.post(cleanup, "/api/jobs/"+url.PathEscape(result.ID)+"/cancel", "", map[string]any{}, &ignored)
			return "", e
		}
		switch j.State {
		case "SUCCEEDED":
			return j.RunID, nil
		case "FAILED", "CANCELLED", "INTERRUPTED":
			return "", fmt.Errorf("SERVER_ATTEMPT_%s_%s", j.ID, j.State)
		}
		select {
		case <-work.Done():
		case <-time.After(500 * time.Millisecond):
		}
	}
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
