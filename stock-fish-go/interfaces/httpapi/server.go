// Package httpapi is a thin transport over the same application used by CLI.
package httpapi

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/subtle"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/application"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/interfaces/daily"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/ports"
	"io"
	"net"
	"net/http"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"
)

var idPattern = regexp.MustCompile(`^[A-Za-z0-9_.:-]{1,200}$`)

type Server struct {
	Service  *application.Service
	Token    string
	queue    chan string
	ctx      context.Context
	cancel   context.CancelFunc
	done     chan struct{}
	submitMu sync.Mutex
}

func New(parent context.Context, s *application.Service, token string) *Server {
	ctx, cancel := context.WithCancel(parent)
	x := &Server{Service: s, Token: token, queue: make(chan string, 20), ctx: ctx, cancel: cancel, done: make(chan struct{})}
	go func() {
		defer close(x.done)
		for {
			select {
			case <-ctx.Done():
				return
			case id := <-x.queue:
				_, _ = s.Execute(ctx, id)
			}
		}
	}()
	return x
}
func (s *Server) Close() { s.cancel(); <-s.done }
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}
func failure(w http.ResponseWriter, e error) {
	code := 400
	if errors.Is(e, sql.ErrNoRows) {
		code = 404
	}
	writeJSON(w, code, map[string]any{"error": e.Error(), "status": "FAILED"})
}
func (s *Server) authorized(r *http.Request) bool {
	provided := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	return s.Token != "" && strings.HasPrefix(r.Header.Get("Authorization"), "Bearer ") && subtle.ConstantTimeCompare([]byte(provided), []byte(s.Token)) == 1
}
func decodeBody(w http.ResponseWriter, r *http.Request, value any) error {
	r.Body = http.MaxBytesReader(w, r.Body, 64<<10)
	raw, e := io.ReadAll(r.Body)
	if e != nil {
		return e
	}
	raw, e = c.CanonicalContext(r.Context(), raw)
	if e != nil {
		return e
	}
	if len(raw) == 0 || raw[0] != '{' {
		return errors.New("ONE_JSON_OBJECT_REQUIRED")
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	return d.Decode(value)
}
func query(r *http.Request, allowed ...string) error {
	values, e := url.ParseQuery(r.URL.RawQuery)
	if e != nil {
		return e
	}
	set := map[string]bool{}
	for _, key := range allowed {
		set[key] = true
	}
	for key, vs := range values {
		if !set[key] || len(vs) != 1 || vs[0] == "" {
			return fmt.Errorf("UNSUPPORTED_OR_DUPLICATE_QUERY_PARAMETER: %s", key)
		}
	}
	return e
}
func integer(r *http.Request, key string, fallback, min, max int) (int, error) {
	value := r.URL.Query().Get(key)
	if value == "" {
		return fallback, nil
	}
	n, e := strconv.Atoi(value)
	if e != nil || n < min || n > max {
		return 0, fmt.Errorf("INVALID_%s", strings.ToUpper(key))
	}
	return n, nil
}
func (s *Server) queries() (ports.ResearchQueries, error) {
	q, ok := s.Service.Store.(ports.ResearchQueries)
	if !ok {
		return nil, errors.New("SAVED_RESEARCH_QUERY_ADAPTER_REQUIRED")
	}
	return q, nil
}

func page(r *http.Request) (int, int, error) {
	size := 50
	index := 1
	var e error
	if v := r.URL.Query().Get("page_size"); v != "" {
		size, e = strconv.Atoi(v)
		if e != nil {
			return 0, 0, e
		}
	}
	if v := r.URL.Query().Get("page"); v != "" {
		index, e = strconv.Atoi(v)
		if e != nil {
			return 0, 0, e
		}
	}
	if size < 1 || size > 500 || index < 1 || index > 100000 {
		return 0, 0, errors.New("INVALID_PAGE")
	}
	return size, (index - 1) * size, nil
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("X-Content-Type-Options", "nosniff")
	w.Header().Set("Cache-Control", "no-store")
	host := r.Host
	if h, _, e := net.SplitHostPort(host); e == nil {
		host = h
	}
	ip := net.ParseIP(host)
	if host != "localhost" && (ip == nil || !ip.IsLoopback()) {
		writeJSON(w, 403, map[string]string{"error": "LOOPBACK_HOST_REQUIRED"})
		return
	}
	if r.Method != "GET" && !s.authorized(r) {
		writeJSON(w, 401, map[string]string{"error": "BEARER_AUTH_REQUIRED"})
		return
	}
	path := strings.Trim(r.URL.Path, "/")
	parts := strings.Split(path, "/")
	for _, p := range parts {
		if !idPattern.MatchString(p) {
			failure(w, errors.New("INVALID_PATH"))
			return
		}
	}
	ctx, release := context.WithTimeout(r.Context(), 5*time.Minute)
	defer release()
	r = r.WithContext(ctx)

	if r.Method == "GET" && strings.HasPrefix(path, "api/daily/") {
		repo, ok := s.Service.Store.(*sqlitev2.AppRepository)
		if !ok {
			failure(w, errors.New("SERVER_DATABASE_OWNER_REQUIRED"))
			return
		}
		reader, e := repo.DailyReadOnly(ctx)
		if e != nil {
			failure(w, e)
			return
		}
		defer reader.Close()
		if path == "api/daily/identity" {
			hash, e := daily.PathHash(repo.DatabasePath())
			if e != nil {
				failure(w, e)
				return
			}
			writeJSON(w, 200, map[string]any{"protocol": daily.Protocol, "database_identity": reader.Identity(), "database_path_sha256": hash})
			return
		}
		if path == "api/daily/readiness" {
			v, e := reader.Ready(ctx)
			if e != nil {
				failure(w, e)
				return
			}
			writeJSON(w, 200, v)
			return
		}
		if path == "api/daily/runs" {
			if e := query(r, "limit", "offset"); e != nil {
				failure(w, e)
				return
			}
			limit, e := integer(r, "limit", 200, 1, 200)
			if e != nil {
				failure(w, e)
				return
			}
			offset, e := integer(r, "offset", 0, 0, 1000000000)
			if e != nil {
				failure(w, e)
				return
			}
			v, e := reader.Runs(ctx, limit, offset)
			if e != nil {
				failure(w, e)
				return
			}
			writeJSON(w, 200, v)
			return
		}
		if path == "api/daily/inputs" {
			if e := query(r, "observation"); e != nil {
				failure(w, e)
				return
			}
			v, e := reader.Inputs(ctx, r.URL.Query().Get("observation"))
			if e != nil {
				failure(w, e)
				return
			}
			writeJSON(w, 200, v)
			return
		}
		if len(parts) == 4 && parts[2] == "runs" {
			if e := queryOptional(r, "instrument", "all"); e != nil {
				failure(w, e)
				return
			}
			v, e := reader.Saved(ctx, parts[3], r.URL.Query().Get("instrument"), r.URL.Query().Get("all") == "true")
			if e != nil {
				failure(w, e)
				return
			}
			writeJSON(w, 200, v)
			return
		}
		failure(w, errors.New("UNSUPPORTED_DAILY_QUERY"))
		return
	}
	if r.Method == "GET" && (path == "api/health" || path == "api/readiness") {
		ready, e := s.Service.Store.Ready(ctx)
		if e != nil {
			writeJSON(w, 503, map[string]string{"error": e.Error()})
			return
		}
		code := 200
		if path == "api/readiness" && !ready.Ready {
			code = 503
		}
		writeJSON(w, code, ready)
		return
	}
	if r.Method == "GET" && path == "api/candidate-schedule" {
		v, e := s.Service.SchedulePlan(ctx)
		if e != nil {
			failure(w, e)
			return
		}
		writeJSON(w, 200, v)
		return
	}
	if r.Method == "GET" && path == "api/strategy-experiments/specs" {
		if e := query(r); e != nil {
			failure(w, e)
			return
		}
		writeJSON(w, 200, []map[string]any{{"strategy_id": c.V2, "label": "STAGE_AWARE_MOMENTUM_V2", "implementation_status": "GO_NATIVE", "parameter_status": "UNVALIDATED", "market_edge": "NOT_ESTABLISHED"}, {"strategy_id": c.R1, "label": "STAGE_AWARE_RESEARCH_R1", "implementation_status": "GO_NATIVE", "parameter_status": "UNVALIDATED", "market_edge": "NOT_ESTABLISHED"}})
		return
	}
	if r.Method == "GET" && (path == "api/strategy-experiments" || path == "api/research/runs") {
		if path == "api/strategy-experiments" {
			if e := query(r, "limit", "offset"); e != nil {
				failure(w, e)
				return
			}
			limit, e := integer(r, "limit", 20, 1, 1000)
			if e != nil {
				failure(w, e)
				return
			}
			offset, e := integer(r, "offset", 0, 0, 1000000000)
			if e != nil {
				failure(w, e)
				return
			}
			q, e := s.queries()
			if e != nil {
				failure(w, e)
				return
			}
			v, e := q.ResearchRuns(ctx, limit, offset)
			if e != nil {
				failure(w, e)
				return
			}
			writeJSON(w, 200, v)
			return
		}
		if e := query(r, "page", "page_size"); e != nil {
			failure(w, e)
			return
		}
		size, offset, e := page(r)
		if e != nil {
			failure(w, e)
			return
		}
		v, e := s.Service.Store.Runs(ctx, size, offset)
		if e != nil {
			failure(w, e)
			return
		}
		writeJSON(w, 200, v)
		return
	}

	if r.Method == "POST" && (path == "api/daily-primary/run" || path == "api/research/r1/run") {
		var value struct {
			InputID        string `json:"input_id"`
			Observation    string `json:"observation_date"`
			Budget         int    `json:"budget_seconds"`
			Target         string `json:"target_session,omitempty"`
			Config         string `json:"config_identity,omitempty"`
			Implementation string `json:"implementation_identity,omitempty"`
			Policy         string `json:"evidence_policy,omitempty"`
		}
		if e := decodeBody(w, r, &value); e != nil {
			failure(w, e)
			return
		}
		if value.Observation != "" {
			date, e := time.Parse("2006-01-02", value.Observation)
			if e != nil || date.Format("2006-01-02") != value.Observation {
				failure(w, errors.New("INVALID_OBSERVATION_DATE"))
				return
			}
			queries, e := s.queries()
			if e != nil {
				failure(w, e)
				return
			}
			observation, e := queries.InputObservation(ctx, value.InputID)
			if e != nil {
				failure(w, e)
				return
			}
			if observation != value.Observation {
				failure(w, errors.New("INPUT_OBSERVATION_DATE_MISMATCH"))
				return
			}
		}
		if e := query(r); e != nil {
			failure(w, e)
			return
		}
		s.submitMu.Lock()
		defer s.submitMu.Unlock()
		strategy := c.V2
		if path == "api/research/r1/run" {
			strategy = c.R1
		}
		key := r.Header.Get("Idempotency-Key")
		if key == "" {
			var nonce [16]byte
			if _, e := rand.Read(nonce[:]); e != nil {
				failure(w, e)
				return
			}
			key = "http_" + hex.EncodeToString(nonce[:])
		}
		request := c.ResearchRequest{InputID: value.InputID, Strategy: strategy, BudgetSeconds: value.Budget, IdempotencyKey: key, TargetSession: value.Target, ConfigIdentity: value.Config, Implementation: value.Implementation, EvidencePolicy: value.Policy}
		if len(s.queue) == cap(s.queue) {
			writeJSON(w, 429, map[string]string{"error": "JOB_QUEUE_FULL"})
			return
		}
		j, created, e := s.Service.Submit(ctx, request)
		if e != nil {
			failure(w, e)
			return
		}
		if created {
			select {
			case s.queue <- j.ID:
			case <-s.ctx.Done():
				_, _ = s.Service.Cancel(ctx, j.ID)
			}
		}
		writeJSON(w, 202, map[string]any{"job_id": j.ID, "task_id": j.ID, "state": j.State, "status": "pending", "created": created})
		return
	}
	if len(parts) >= 3 && parts[0] == "api" && parts[1] == "jobs" {
		id := parts[2]
		if r.Method == "GET" && len(parts) == 3 {
			v, e := s.Service.Store.Job(ctx, id)
			if e != nil {
				failure(w, e)
				return
			}
			writeJSON(w, 200, v)
			return
		}
		if r.Method == "GET" && len(parts) == 4 && parts[3] == "events" {
			v, e := s.Service.Store.Events(ctx, id)
			if e != nil {
				failure(w, e)
				return
			}
			writeJSON(w, 200, v)
			return
		}
		if r.Method == "POST" && len(parts) == 4 && parts[3] == "cancel" {
			v, e := s.Service.Cancel(ctx, id)
			if e != nil {
				failure(w, e)
				return
			}
			writeJSON(w, 200, v)
			return
		}
	}
	if len(parts) >= 3 && parts[0] == "api" && (parts[1] == "strategy-experiments" || parts[1] == "candidate-runs") && r.Method == "GET" {
		id := parts[2]
		if len(parts) == 3 {
			if e := query(r); e != nil {
				failure(w, e)
				return
			}
			if parts[1] == "candidate-runs" {
				raw, e := s.Service.Store.Legacy(ctx, "candidate_runs", id)
				if e != nil {
					failure(w, e)
					return
				}
				writeJSON(w, 200, raw)
				return
			}
			v, e := s.Service.Store.Run(ctx, id)
			if errors.Is(e, sql.ErrNoRows) {
				raw, err := s.Service.Store.Legacy(ctx, "strategy_experiment_runs", id)
				if err != nil {
					failure(w, err)
					return
				}
				writeJSON(w, 200, raw)
				return
			}
			if e != nil {
				failure(w, e)
				return
			}
			writeJSON(w, 200, v)
			return
		}
		if parts[1] == "candidate-runs" {
			writeJSON(w, 501, map[string]string{"error": "FROZEN_DAILY_V1_VIEW_REQUIRES_RETAINED_ORIGINAL_ENTRY_POINT"})
			return
		}
		q, e := s.queries()
		if e != nil {
			failure(w, e)
			return
		}
		if len(parts) == 4 && parts[3] == "summary" {
			if e = query(r, "page", "page_size", "strategy_id", "selection"); e != nil {
				failure(w, e)
				return
			}
			size, offset, e := page(r)
			if e != nil {
				failure(w, e)
				return
			}
			raw, e := q.ResearchSummary(ctx, id, r.URL.Query().Get("strategy_id"), r.URL.Query().Get("selection"), offset/size+1, size)
			if e != nil {
				failure(w, e)
				return
			}
			writeJSON(w, 200, raw)
			return
		}
		if len(parts) == 5 && parts[3] == "items" {
			if e = query(r); e != nil {
				failure(w, e)
				return
			}
			raw, e := q.ResearchItem(ctx, id, parts[4])
			if e != nil {
				failure(w, e)
				return
			}
			writeJSON(w, 200, raw)
			return
		}
		if len(parts) == 4 && parts[3] == "review" {
			if e = query(r, "full"); e != nil {
				failure(w, e)
				return
			}
			full := r.URL.Query().Get("full")
			if full != "" && full != "0" && full != "1" && full != "false" && full != "true" {
				failure(w, errors.New("INVALID_FULL"))
				return
			}
			raw, e := q.ResearchReview(ctx, id, full == "1" || full == "true")
			if e != nil {
				failure(w, e)
				return
			}
			writeJSON(w, 200, raw)
			return
		}
		if len(parts) == 4 && parts[3] == "export" {
			if e = query(r, "format", "strategy_id", "selection"); e != nil {
				failure(w, e)
				return
			}
			format := r.URL.Query().Get("format")
			if format == "" {
				format = "json"
			}
			if format != "json" && format != "csv" {
				failure(w, errors.New("UNSUPPORTED_EXPORT_FORMAT"))
				return
			}
			_, nativeErr := s.Service.Store.Run(ctx, id)
			if nativeErr == nil {
				if format != "json" || r.URL.Query().Get("strategy_id") != "" || r.URL.Query().Get("selection") != "" {
					raw, mime, err := q.ResearchExport(ctx, id, r.URL.Query().Get("strategy_id"), r.URL.Query().Get("selection"), format)
					if err != nil {
						failure(w, err)
						return
					}
					w.Header().Set("Content-Type", mime)
					w.Header().Set("Content-Disposition", `attachment; filename="stockfish-result.`+format+`"`)
					_, _ = w.Write(raw)
					return
				}
				w.Header().Set("Content-Type", "application/json")
				w.Header().Set("Content-Disposition", `attachment; filename="stockfish-result.json"`)
				count := &countingWriter{ResponseWriter: w}
				if e = s.Service.ExportTo(ctx, id, count); e != nil && count.bytes == 0 {
					w.Header().Del("Content-Disposition")
					failure(w, e)
				}
				return
			}
			if !errors.Is(nativeErr, sql.ErrNoRows) {
				failure(w, nativeErr)
				return
			}
			raw, mime, e := q.ResearchExport(ctx, id, r.URL.Query().Get("strategy_id"), r.URL.Query().Get("selection"), format)
			if e != nil {
				failure(w, e)
				return
			}
			w.Header().Set("Content-Type", mime)
			w.Header().Set("Content-Disposition", `attachment; filename="r05-strategy-experiment.`+format+`"`)
			_, _ = w.Write(raw)
			return
		}
	}

	writeJSON(w, 404, map[string]string{"error": "ROUTE_NOT_SUPPORTED", "retained_module": "Python frozen/execution/Qlib endpoints retain their original entry points"})
}

type countingWriter struct {
	http.ResponseWriter
	bytes int
}

func (w *countingWriter) Write(b []byte) (int, error) {
	n, e := w.ResponseWriter.Write(b)
	w.bytes += n
	return n, e
}
func Listen(ctx context.Context, address string, s *Server) error {
	host, _, e := net.SplitHostPort(address)
	if e != nil {
		return e
	}
	ip := net.ParseIP(host)
	if ip == nil || !ip.IsLoopback() {
		return errors.New("EXPLICIT_LOOPBACK_LISTEN_REQUIRED")
	}
	server := &http.Server{Addr: address, Handler: s, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: 5 * time.Minute, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 32 << 10}
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = server.Shutdown(shutdown)
	}()
	e = server.ListenAndServe()
	if errors.Is(e, http.ErrServerClosed) {
		return nil
	}
	return fmt.Errorf("http server: %w", e)
}

func queryOptional(r *http.Request, allowed ...string) error {
	set := map[string]bool{}
	for _, key := range allowed {
		set[key] = true
	}
	for key, values := range r.URL.Query() {
		if !set[key] || len(values) != 1 {
			return errors.New("INVALID_DAILY_QUERY")
		}
	}
	return nil
}
