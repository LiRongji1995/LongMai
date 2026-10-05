package contracts

type Input struct {
	Coverage         string   `json:"coverage_type"`
	Observation      string   `json:"observation_date"`
	HistoryRef       string   `json:"history_artifact_ref"`
	HistoryHash      string   `json:"history_artifact_hash"`
	Sessions         []string `json:"sessions"`
	Bars             []Row    `json:"bars"`
	Receipts         []Row    `json:"receipts"`
	Universe         Row      `json:"universe"`
	Themes           []Row    `json:"themes"`
	Funds            []Row    `json:"funds"`
	PreviousEmotions []Row    `json:"previous_emotions"`
}
type Frozen struct {
	Purpose string `json:"purpose,omitempty"`
	Input   Input  `json:"input"`
	Cutoff  string `json:"cutoff"`
	// SourceProof is an opaque, in-memory attestation owned by the strict
	// decoder. It is never stored in the source model or reconstructed by JSON.
	SourceProof any `json:"-"`
}
