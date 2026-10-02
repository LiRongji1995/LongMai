package contracts

type Partition struct {
	Session string `json:"session"`
	Hash    string `json:"partition_hash"`
	Rows    int64  `json:"row_count"`
}
type HistoryManifest struct {
	Version    string      `json:"artifact_version"`
	Partitions []Partition `json:"partitions"`
	Receipts   []Row       `json:"receipts"`
}
