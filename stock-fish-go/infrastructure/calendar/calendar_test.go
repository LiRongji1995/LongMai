package calendar

import (
	"testing"
	"time"
)

func TestOfficialNextSession(t *testing.T) {
	for _, tt := range []struct{ now, target, completed string }{
		{"2026-10-03T04:00:00+08:00", "2026-10-08", "2026-09-30"},
		{"2026-10-08T09:29:59+08:00", "2026-10-08", "2026-09-30"},
		{"2026-10-08T09:30:00+08:00", "2026-10-09", "2026-09-30"},
		{"2026-10-08T15:00:00+08:00", "2026-10-09", "2026-10-08"},
	} {
		now, _ := time.Parse(time.RFC3339, tt.now)
		got, e := Official().Plan(now)
		if e != nil || got["target_session"] != tt.target || got["latest_completed_session"] != tt.completed {
			t.Fatal(tt, got, e)
		}
	}
	now, _ := time.Parse(time.RFC3339, "2027-01-03T09:00:00+08:00")
	if _, e := Official().Plan(now); e == nil {
		t.Fatal("uncovered year accepted")
	}
}
