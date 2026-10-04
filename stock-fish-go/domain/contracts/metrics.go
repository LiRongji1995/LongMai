package contracts

type Metrics struct {
	WaitElapsed       float64 `json:"wait_elapsed_seconds"`
	ClockStatus       string  `json:"clock_status"`
	LogDropped        int64   `json:"log_dropped"`
	LogSinkStatus     string  `json:"log_sink_status"`
	FinalLogStatus    string  `json:"final_log_status"`
	CacheSampleAt     string  `json:"cache_sample_at"`
	CacheSampleStatus string  `json:"cache_sample_status"`
	CacheSamples      int64   `json:"cache_samples"`

	RunID          string `json:"run_id,omitempty"`
	InputID        string `json:"input_id,omitempty"`
	InputHash      string `json:"input_bytes_hash,omitempty"`
	SourceHash     string `json:"go_source_hash,omitempty"`
	Final          bool   `json:"final_resource_scope"`
	LogWriteErrors int64  `json:"log_write_errors"`
	CacheHits      int64  `json:"cache_hits"`
	CacheMisses    int64  `json:"cache_misses"`
	CacheCorrupt   int64  `json:"cache_corrupt"`
	CacheErrors    int64  `json:"cache_errors"`

	Elapsed              float64            `json:"elapsed_seconds"`
	Stage                string             `json:"stage"`
	Processed            int                `json:"processed"`
	Rows                 int64              `json:"source_rows"`
	SourceBytes          int64              `json:"source_bytes"`
	HistoryUniqueRows    int64              `json:"history_unique_rows"`
	HistoryUniqueBytes   int64              `json:"history_unique_payload_bytes"`
	HistoryRepeatedRows  int64              `json:"history_repeated_rows"`
	HistoryRepeatedBytes int64              `json:"history_repeated_payload_bytes"`
	IndexRows            int64              `json:"index_rows_verified"`
	ExpandedOutputBytes  int64              `json:"expanded_detail_output_bytes"`
	OutputBytes          int64              `json:"output_bytes"`
	CacheReadBytes       int64              `json:"cache_read_bytes"`
	CacheBytes           *int64             `json:"cache_bytes"`
	RSS                  *int64             `json:"rss_bytes"`
	PeakRSS              *int64             `json:"peak_rss_bytes"`
	GoHeap               uint64             `json:"go_heap_bytes"`
	RSSStatus            string             `json:"rss_status"`
	Stages               map[string]float64 `json:"stage_seconds"`
	Components           map[string]float64 `json:"component_seconds"`
}
