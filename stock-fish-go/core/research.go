package core

// Analyze preserves the explicit old-format, read-only adapter entry point.
func Analyze(inputPath, dbPath string, b *Budget) (Diagnostic, error) {
	raw, e := ReadBounded(inputPath, 64<<20, b)
	if e != nil {
		return Diagnostic{}, e
	}
	if e = b.Read(int64(len(raw))); e != nil {
		return Diagnostic{}, e
	}
	frozen, e := DecodeFrozenContext(b.Context, raw)
	if e != nil {
		return Diagnostic{}, e
	}
	var history HistoryReader
	if frozen.Input.HistoryRef != "" {
		h, err := OpenHistory(dbPath, frozen.Input.HistoryRef, frozen.Input.HistoryHash, b)
		if err != nil {
			return Diagnostic{}, err
		}
		defer h.Close()
		history = h
	}
	return AnalyzeFrozen(frozen, Hash(raw), history, b)
}
