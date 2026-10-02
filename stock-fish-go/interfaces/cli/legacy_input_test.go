package cli_test

import (
	"bytes"
	"context"
	"encoding/json"
	"os"
	"testing"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
	ev "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/evidence"
	"github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/interfaces/cli"
)

func TestImportLegacyInputStrictMetadataAndIndependentNewDatabase(t *testing.T) {
	store, repo, _ := appFixture(t)
	ctx := context.Background()
	reference, e := os.ReadFile("../../core/testdata/input_contract_python.json")
	if e != nil {
		t.Fatal(e)
	}
	var fixture map[string]json.RawMessage
	if e = json.Unmarshal(reference, &fixture); e != nil {
		t.Fatal(e)
	}
	raw := fixture["input"]
	hash := c.Hash(raw)
	result, e := store.DB().Exec("INSERT INTO evidence_objects(raw_sha256,raw_size,codec,payload) VALUES(?,?,'raw',?)", hash, len(raw), []byte(raw))
	if e != nil {
		t.Fatal(e)
	}
	evidenceID, e := result.LastInsertId()
	if e != nil {
		t.Fatal(e)
	}
	if _, e = store.DB().Exec("INSERT INTO legacy_records VALUES('candidate_inputs','TEST_FIXTURE_OLD_INPUT',?,?,'TEST_FIXTURE')", hash, evidenceID); e != nil {
		t.Fatal(e)
	}
	var output, log bytes.Buffer
	if e = cli.Run(ctx, []string{"import-legacy-input", "--db", store.Path, "--id", "TEST_FIXTURE_OLD_INPUT", "--cutoff", "2026-01-05T20:00:00+08:00"}, &output, &log, "TEST_FIXTURE_METADATA_BUILD"); e != nil {
		t.Fatal(e, log.String())
	}
	var imported c.InputRecord
	if e = json.Unmarshal(output.Bytes(), &imported); e != nil {
		t.Fatal(e)
	}
	saved, e := repo.Input(ctx, imported.ID)
	if e != nil {
		t.Fatal(e)
	}
	if saved.RawHash != c.Hash(saved.Raw) || saved.RawHash != imported.RawHash {
		t.Fatal("registered wrapper byte identity lost")
	}
	if _, e = ev.DecodeFrozen(saved.Raw); e != nil {
		t.Fatal(e)
	}
	var envelope struct {
		Parent struct {
			Namespace string `json:"namespace"`
			ID        string `json:"external_id"`
			Hash      string `json:"raw_sha256"`
		} `json:"parent_input"`
		Input json.RawMessage `json:"input"`
	}
	if e = json.Unmarshal(saved.Raw, &envelope); e != nil {
		t.Fatal(e)
	}
	if envelope.Parent.Namespace != "candidate_inputs" || envelope.Parent.ID != "TEST_FIXTURE_OLD_INPUT" || envelope.Parent.Hash != hash {
		t.Fatal("legacy parent relation lost")
	}
	original, e := repo.Legacy(ctx, "candidate_inputs", "TEST_FIXTURE_OLD_INPUT")
	if e != nil {
		t.Fatal(e)
	}
	if !bytes.Equal(original, raw) {
		t.Fatal("migration parent bytes changed")
	}
	// No legacy database, raw file or Python subprocess participates in this path.
	if len(envelope.Input) == 0 || saved.Cutoff != "2026-01-05T20:00:00+08:00" {
		t.Fatal("frozen input/cutoff not retained")
	}
}
