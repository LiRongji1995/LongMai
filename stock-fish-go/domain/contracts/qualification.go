package contracts

type Qualification struct {
	Eligible         []string          `json:"eligible_ids"`
	Excluded         map[string]string `json:"known_excluded"`
	Unknown          map[string]string `json:"eligibility_unknown"`
	Absent           int               `json:"identity_absent"`
	UniverseComplete bool              `json:"universe_complete"`
}
