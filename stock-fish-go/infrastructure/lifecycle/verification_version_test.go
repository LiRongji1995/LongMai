package lifecycle

import (
	"context"
	"database/sql"
	"encoding/json"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/infrastructure/sqlitev2"
	"strings"
	"testing"
)

func TestRecoveryMetadataRejectsUnsupportedVerificationVersion(t *testing.T) {
	ctx := context.Background()
	root, _ := isolatedFixture(t)
	conf, e := Load(root)
	if e != nil {
		t.Fatal(e)
	}
	db, e := sql.Open("sqlite", sqlitev2.URI(conf.Target, false))
	if e != nil {
		t.Fatal(e)
	}
	defer db.Close()
	var raw string
	if e = db.QueryRow("SELECT verification FROM migration_state WHERE id=1").Scan(&raw); e != nil {
		t.Fatal(e)
	}
	var v sqlitev2.Verification
	if e = json.Unmarshal([]byte(raw), &v); e != nil {
		t.Fatal(e)
	}
	v.Version = "TEST_FIXTURE_UNSUPPORTED"
	changed, _ := json.Marshal(v)
	if _, e = db.Exec("UPDATE migration_state SET verification=? WHERE id=1", string(changed)); e != nil {
		t.Fatal(e)
	}
	if _, _, _, e = metadata(ctx, db); e == nil || !strings.Contains(e.Error(), "MIGRATION_VERIFICATION_INVALID") {
		t.Fatal("unsupported proof accepted", e)
	}
}
