package evidence

// DecimalProjection preserves the source Decimal identity (including scale)
// after a caller has established that s is a finite decimal token.
func DecimalProjection(s string) string { return decimalProjection(s) }
