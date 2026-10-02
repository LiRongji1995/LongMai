package native

import (
	"errors"
	c "github.com/LiRongji1995/stockfish-qlib-quant-system/stock-fish-go/domain/contracts"
)

// The manifest declares the same-index binding and shared parent identities.
// Only equal digests are elided; raw != projection always retains both values.
func compactEvidence(bindings []c.SelectedEvidenceBinding, projections []string, inputHash, historyHash string) error {
	if len(bindings) != len(projections) {
		return errors.New("SELECTED_EVIDENCE_BINDING_COUNT")
	}
	for i := range bindings {
		b := &bindings[i]
		if b.ProjectionHash != projections[i] {
			return errors.New("SELECTED_EVIDENCE_BINDING_ORDER")
		}
		switch b.Identity.Origin {
		case "HISTORY_ROW":
			if historyHash == "" || b.Identity.ParentHash != historyHash || len(b.Identity.RawSHA256) != 64 || b.Identity.Locator != b.Identity.RawSHA256 {
				return errors.New("AUTHENTICATED_HISTORY_BINDING_REQUIRED")
			}
			if b.Identity.RawSHA256 == b.ProjectionHash {
				b.RawEqualsProjection = true
				b.Identity.RawSHA256 = ""
			}
			b.Identity.Locator = ""
		case "FROZEN_INPUT":
			if b.Identity.ParentHash != inputHash || b.Identity.Locator == "" {
				return errors.New("FROZEN_INPUT_BINDING_REQUIRED")
			}
		default:
			return errors.New("AUTHENTICATED_EVIDENCE_BINDING_REQUIRED")
		}
		b.Identity.ParentHash = ""
		b.Identity.RawBytes = 0
		b.ProjectionHash = ""
	}
	return nil
}
