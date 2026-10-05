package evidence

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"
	"strings"

	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

// FrozenSourceProofContractVersion does not change the stored market model.
// It versions the disposable original-byte proof used by incremental arithmetic.
const FrozenSourceProofContractVersion = "GO_DECODED_FROZEN_BAR_BYTES_V1"

type decodedFrozenBar struct {
	rawHash, projectionHash string
	rawBytes                int64
}
type decodedFrozenSource struct {
	contract, inputHash string
	bars                []decodedFrozenBar
}

// Only DecodeFrozenContext calls this after the complete envelope and all
// original rows passed strict validation. The private type cannot be supplied
// by a directly constructed Frozen or a deserialized claimed proof.
func decodedSourceProof(ctx context.Context, raw []byte, original []json.RawMessage, projected []c.Row) (*decodedFrozenSource, error) {
	if len(original) != len(projected) {
		return nil, contractError("frozen.input.bars", "ORIGINAL_ROW_PROOF_MISMATCH")
	}
	proof := &decodedFrozenSource{contract: FrozenSourceProofContractVersion, inputHash: c.Hash(raw)}
	for index, row := range original {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		proof.bars = append(proof.bars, decodedFrozenBar{c.Hash(row), c.RowHash(projected[index]), int64(len(row))})
	}
	return proof, nil
}

// DecodedInputBarIdentity returns evidence only when the current enclosing
// input hash and current validated projection match the original strict decode.
// Mutating/reordering an in-memory row or supplying a different input hash
// invalidates that row's attestation, without rejecting a valid full calculation.
func DecodedInputBarIdentity(f c.Frozen, inputHash string, index int) (c.EvidenceIdentity, bool) {
	proof, ok := f.SourceProof.(*decodedFrozenSource)
	if !ok || proof == nil || proof.contract != FrozenSourceProofContractVersion ||
		!ValidHash(inputHash) || proof.inputHash != inputHash || index < 0 ||
		index >= len(f.Input.Bars) || index >= len(proof.bars) {
		return c.EvidenceIdentity{}, false
	}
	bar := proof.bars[index]
	if !ValidHash(bar.rawHash) || bar.rawBytes <= 0 || bar.projectionHash != c.RowHash(f.Input.Bars[index]) {
		return c.EvidenceIdentity{}, false
	}
	return c.EvidenceIdentity{Origin: "FROZEN_INPUT", ParentHash: inputHash,
		RawSHA256: bar.rawHash, RawBytes: bar.rawBytes, Locator: fmt.Sprintf("/input/bars/%d", index)}, true
}

// MatchesDecodedInputBarBinding also checks the caller's selected projection
// and complete original identity. RawSHA alone is not a decoder attestation.
func MatchesDecodedInputBarBinding(f c.Frozen, inputHash string, binding c.SelectedEvidenceBinding) bool {
	identity := binding.Identity
	if identity.Origin != "FROZEN_INPUT" {
		return false
	}
	index, err := strconv.Atoi(strings.TrimPrefix(identity.Locator, "/input/bars/"))
	verified, ok := DecodedInputBarIdentity(f, inputHash, index)
	return err == nil && ok && verified == identity && binding.ProjectionHash == c.RowHash(f.Input.Bars[index])
}
