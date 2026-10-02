package facts

type StockFacts struct {
	ID                          string `json:"instrument_id"`
	Name                        string `json:"name"`
	Facts                       Facts  `json:"facts"`
	Amount                      Number `json:"amount_cny"`
	CurrentMA20, PriorMA20      Number
	Recent3, Previous3          Number
	EvidenceHashes              []string                  `json:"selected_evidence_hashes"`
	EvidenceBindings            []SelectedEvidenceBinding `json:"selected_evidence_bindings"`
	Rejected                    map[string]int            `json:"rejected_evidence_counts"`
	Turnover                    Number                    `json:"turnover_fraction"`
	AmountChanges               map[int]Number            `json:"amount_changes"`
	CurrentLimit, PreviousLimit LimitFact
	Supply                      Condition `json:"supply_pressure"`
}
type StrengthMomentum struct {
	Calculation      string `json:"calculation_id"`
	ReturnState      string `json:"return_momentum"`
	RSState          string `json:"rs_momentum"`
	StrengthState    string `json:"strength_momentum"`
	StrengthRaw      Number `json:"strength_momentum_raw"`
	ReturnRaw        Number `json:"return_momentum_raw"`
	RSRaw            Number `json:"rs_momentum_raw"`
	ActivityState    string `json:"activity_momentum"`
	ActivityRaw      Number `json:"activity_momentum_raw"`
	AmountRaw        Number `json:"amount_momentum_raw"`
	TurnoverRaw      Number `json:"turnover_momentum_raw"`
	RecentAmount     Number `json:"recent_amount_avg_cny"`
	PreviousAmount   Number `json:"previous_amount_avg_cny"`
	RecentTurnover   Number `json:"recent_turnover_avg"`
	PreviousTurnover Number `json:"previous_turnover_avg"`
}
type Facts struct {
	Strength StrengthMomentum `json:"shared_strength_momentum"`
	LegacyRS map[int]Number

	Returns, RS                                                                                                  map[int]Number
	Momentum                                                                                                     string
	RecentTurnover, PriorTurnover, PriorReturn5, PriorReturn10, PriorPercentile10, PriorHigh, Close, AmountRatio Number
	PriorLimitUp                                                                                                 bool
	Path                                                                                                         Path
	SectorRS1                                                                                                    Number
	Trend                                                                                                        Trend
}
