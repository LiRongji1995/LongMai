package contracts

import ()

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
	Details   map[string]any       `json:"details,omitempty"`
	Evidence  []string             `json:"evidence_refs,omitempty"`
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

func Contains(a []string, v string) bool {
	for _, s := range a {
		if s == v {
			return true
		}
	}
	return false
}

type ErrCode string

func (e ErrCode) Error() string { return string(e) }
