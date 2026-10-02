package contracts

// EvidenceIdentity is adapter provenance, never inserted into a model Row.
// Input JSON pointers bind to immutable input bytes; historical rows bind to
// stored bytes and their enclosing content-addressed history snapshot.
type EvidenceIdentity struct {
	Origin     string `json:"origin"`
	ParentHash string `json:"parent_hash,omitempty"`
	RawSHA256  string `json:"raw_sha256,omitempty"`
	Locator    string `json:"locator,omitempty"`
	RawBytes   int64  `json:"raw_bytes,omitempty"`
}
type SelectedEvidenceBinding struct {
	ProjectionHash      string           `json:"go_projection_hash,omitempty"`
	Identity            EvidenceIdentity `json:"identity"`
	RawEqualsProjection bool             `json:"raw_sha256_is_projection,omitempty"`
}
