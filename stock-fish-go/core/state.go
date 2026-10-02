package core

import "sort"

type State string

const (
	Pass           State = "PASS"
	Fail           State = "FAIL"
	Unknown        State = "UNKNOWN"
	NotEvaluated   State = "NOT_EVALUATED"
	NotImplemented State = "NOT_IMPLEMENTED"
	RuntimeError   State = "RUNTIME_ERROR"
)

type Condition struct {
	Status    State                `json:"status"`
	Reasons   []string             `json:"reason_codes"`
	Parts     map[string]Condition `json:"conditions,omitempty"`
	Value     Number               `json:"value"`
	Threshold Number               `json:"threshold"`
}

func C(s State, reason string) Condition {
	r := []string{}
	if s != Pass && reason != "" {
		r = append(r, reason)
	}
	return Condition{Status: s, Reasons: r}
}
func Compare(v, t Number, strict bool, code string) Condition {
	if !v.Known() {
		return C(Unknown, code+"_UNAVAILABLE")
	}
	s := Fail
	if v.Cmp(t) > 0 || (!strict && v.Cmp(t) == 0) {
		s = Pass
	}
	r := C(s, code)
	r.Value = v
	r.Threshold = t
	return r
}
func All(p map[string]Condition) Condition {
	s := Pass
	if len(p) == 0 {
		s = Unknown
	}
	for _, v := range p {
		if v.Status == RuntimeError || v.Status == NotImplemented {
			return C(v.Status, "IMPLEMENTATION_INCOMPLETE")
		}
		if v.Status == Fail {
			s = Fail
		} else if v.Status != Pass && s != Fail {
			s = Unknown
		}
	}
	r := C(s, "R1_REQUIRED_CONDITIONS")
	r.Parts = p
	return r
}
func Any(p map[string]Condition) Condition {
	s := Fail
	if len(p) == 0 {
		s = Unknown
	}
	for _, v := range p {
		if v.Status == RuntimeError || v.Status == NotImplemented {
			return C(v.Status, "IMPLEMENTATION_INCOMPLETE")
		}
		if v.Status == Pass {
			s = Pass
		} else if v.Status != Fail && s != Pass {
			s = Unknown
		}
	}
	r := C(s, "R1_ALTERNATIVES")
	r.Parts = p
	return r
}

var Stages = []string{"START", "ACCELERATION", "HIGH_LEVEL_REPAIR", "TREND_PULLBACK"}

type Decision struct {
	InstrumentID    string               `json:"instrument_id"`
	CandidateStatus string               `json:"candidate_status"`
	Common          Condition            `json:"common"`
	Setups          map[string]Condition `json:"setups"`
	Matched         []string             `json:"matched_setups"`
	Primary         string               `json:"primary_setup"`
}

func Decide(iid string, amount Number, limit string, ident map[string]Condition, ma20, longTrend Condition, trendState string, weakToStrong Condition, order []string) (Decision, error) {
	if len(order) == 0 {
		order = Stages
	}
	seen := map[string]bool{}
	for _, s := range order {
		seen[s] = true
	}
	for _, s := range Stages {
		if !seen[s] {
			return Decision{}, errCode("INVALID_DISPLAY_ORDER")
		}
	}
	if len(order) != 4 || len(seen) != 4 {
		return Decision{}, errCode("INVALID_DISPLAY_ORDER")
	}
	guard := C(Unknown, "CLOSE_LIMIT_DOWN_UNKNOWN_OR_TRUE")
	if limit == "TRUE" {
		guard = C(Fail, "CLOSE_LIMIT_DOWN_UNKNOWN_OR_TRUE")
	}
	if limit == "FALSE" {
		guard = C(Pass, "")
	}
	common := All(map[string]Condition{"liquidity": Compare(amount, N("500000000"), false, "AMOUNT_500M"), "close_limit_down": guard})
	d := Decision{InstrumentID: iid, Common: common, Setups: map[string]Condition{}, Matched: []string{}}
	for _, s := range Stages {
		if common.Status == Fail {
			d.Setups[s] = C(NotEvaluated, "COMMON_HARD_FAIL")
			continue
		}
		trend := ma20
		critical := C(Pass, "")
		if s == "TREND_PULLBACK" {
			trend = longTrend
			if trendState == "TREND_BROKEN" {
				critical = C(Fail, "SAM_CRITICAL_TREND_BROKEN")
			}
		}
		if limit == "TRUE" {
			critical = C(Fail, "SAM_CRITICAL_CLOSE_LIMIT_DOWN")
		}
		identification, ok := ident[s]
		if !ok {
			return Decision{}, errCode("MISSING_SETUP_IMPLEMENTATION")
		}
		behavior := identification
		if identification.Status == Pass {
			behavior = C(Pass, "")
			if s == "HIGH_LEVEL_REPAIR" {
				behavior = weakToStrong
			}
		}
		d.Setups[s] = All(map[string]Condition{"common": common, "trend": trend, "identification": identification, "behavior": behavior, "critical": critical})
		if d.Setups[s].Status == Pass {
			d.Matched = append(d.Matched, s)
		}
	}
	state := Any(d.Setups).Status
	if common.Status == Fail {
		state = Fail
	}
	d.CandidateStatus = "CORE_" + string(state)
	sort.Strings(d.Matched)
	for _, s := range order {
		if d.Setups[s].Status == Pass {
			d.Primary = s
			break
		}
	}
	return d, nil
}

type errCode string

func (e errCode) Error() string { return string(e) }
