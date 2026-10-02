package sqlitev2

import (
	"bytes"
	"context"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"
)

func TestKilledUncommittedBatchResumes(t *testing.T) {
	if path := os.Getenv("STOCKFISH_TEST_FIXTURE_CRASH_DB"); path != "" {
		s, e := Open(context.Background(), path, false)
		if e != nil {
			t.Fatal(e)
		}
		tx, e := s.db.Begin()
		if e != nil {
			t.Fatal(e)
		}
		if _, e = tx.Exec("INSERT INTO migration_checkpoints VALUES('TEST_FIXTURE_UNCOMMITTED',999,999,'TEST_FIXTURE')"); e != nil {
			t.Fatal(e)
		}
		if e = os.WriteFile(path+".TEST_FIXTURE_READY", []byte("uncommitted"), 0600); e != nil {
			t.Fatal(e)
		}
		for {
			time.Sleep(time.Second)
		}
	}
	_, src, target := fixture(t)
	ctx := context.Background()
	if _, e := Migrate(ctx, src, target, Options{BatchSize: 1, MaxBatches: 1}); e != nil {
		t.Fatal(e)
	}
	cmd := exec.Command(os.Args[0], "-test.run=^TestKilledUncommittedBatchResumes$")
	cmd.Env = append(os.Environ(), "STOCKFISH_TEST_FIXTURE_CRASH_DB="+target)
	var output bytes.Buffer
	cmd.Stdout = &output
	cmd.Stderr = &output
	if e := cmd.Start(); e != nil {
		t.Fatal(e)
	}
	defer cmd.Process.Kill()
	deadline := time.Now().Add(15 * time.Second)
	for {
		if _, e := os.Stat(target + ".TEST_FIXTURE_READY"); e == nil {
			break
		}
		if time.Now().After(deadline) {
			cmd.Process.Kill()
			cmd.Wait()
			t.Fatal("helper did not reach transaction", output.String())
		}
		time.Sleep(10 * time.Millisecond)
	}
	if e := cmd.Process.Kill(); e != nil {
		t.Fatal(e)
	}
	_ = cmd.Wait()
	s, e := Open(ctx, target, false)
	if e != nil {
		t.Fatal(e)
	}
	var n int
	if e = s.db.QueryRow("SELECT count(*) FROM migration_checkpoints WHERE stage='TEST_FIXTURE_UNCOMMITTED'").Scan(&n); e != nil || n != 0 {
		t.Fatal(n, e)
	}
	s.Close()
	if _, e = Migrate(ctx, src, target, Options{}); e != nil {
		t.Fatal(e)
	}
	if _, e = Verify(ctx, src, target, Options{}); e != nil {
		t.Fatal(e)
	}
	if e = Publish(ctx, target); e != nil {
		t.Fatal(e)
	}
}
func TestDecoderRejectsBombSizeAndCancelledWork(t *testing.T) {
	if _, e := decode("deflate", []byte{1}, 129<<20, ""); e == nil {
		t.Fatal("unbounded expansion")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, e := decodeContext(ctx, "raw", nil, 0, digest(nil)); e == nil {
		t.Fatal("cancel ignored")
	}
}
func TestDriverActuallyCancelsLongQuery(t *testing.T) {
	s, e := Open(context.Background(), filepath.Join(t.TempDir(), "TEST_FIXTURE.db"), true)
	if e != nil {
		t.Fatal(e)
	}
	defer s.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Millisecond)
	defer cancel()
	var n int64
	started := time.Now()
	e = s.db.QueryRowContext(ctx, `WITH RECURSIVE numbers(x) AS(SELECT 1 UNION ALL SELECT x+1 FROM numbers WHERE x<100000000) SELECT sum(x) FROM numbers`).Scan(&n)
	if e == nil {
		t.Fatal("long query ignored cancellation")
	}
	if time.Since(started) > 2*time.Second {
		t.Fatal("driver did not promptly cancel", time.Since(started))
	}
}
